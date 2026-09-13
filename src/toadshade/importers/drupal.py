"""drupal-to-toadshade — Drupal 10/11 entities into page bundles.

The reverse of Drupal-Canvas-Page-Migrate: read a Drupal site's database and
write one bundle per node, taxonomy term, and user, filed by path alias.

It reads the database directly rather than going through JSON:API, so the site
does not have to be running — a restored backup is enough. The split follows
`base.py` exactly:

    DrupalDatabase    knows Drupal's SQL storage: the serialized `config`
                      table, `{entity}_field_data`, `{entity}__{field}`,
                      `path_alias`, `file_managed`. Yields plain dicts and has
                      never heard of Toadshade.
    DrupalToToadshade turns one of those dicts into a BundleDraft.

Scope, deliberately: the default revision in the default language. Paragraphs
and media are embedded in the entity that references them rather than
exported as pages of their own. Every field is kept, user fields included —
filtering personal data is a separate decision, not this tool's.

    drupal-to-toadshade content/ --db mysql://drupal@localhost/drupal \\
        --files-dir /var/www/site/web/sites/default/files

MySQL/MariaDB needs `pip install "toadshade[drupal]"`. A `sqlite:///site.db`
URL works with the standard library alone, which is how the tests run.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import decimal
import hashlib
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import unquote, urlparse

from .. import __version__
from .base import BundleDraft, Exporter, markdown_for, slugify

# --------------------------------------------------------------------------
# 1. PHP's serialize() format, which is how Drupal stores configuration
# --------------------------------------------------------------------------


def php_unserialize(data):
    """Decode a PHP-serialized value. Arrays with keys 0..n-1 become lists.

    Covers what Drupal's `config` table actually contains — strings, ints,
    floats, booleans, null, arrays, and the occasional plain object (read as a
    dict). String lengths are byte counts, so the input is handled as bytes.
    """
    if isinstance(data, memoryview):
        data = bytes(data)
    if isinstance(data, str):
        data = data.encode("utf-8")
    value, _end = _unserialize(data, 0)
    return value


def _read_until(data: bytes, pos: int, stop: bytes):
    end = data.index(stop, pos)
    return data[pos:end].decode("ascii"), end + len(stop)


def _unserialize(data: bytes, pos: int):
    kind = data[pos:pos + 1]
    if kind == b"N":
        return None, pos + 2
    if kind in (b"i", b"b", b"d"):
        raw, pos = _read_until(data, pos + 2, b";")
        if kind == b"i":
            return int(raw), pos
        if kind == b"b":
            return raw == "1", pos
        return float(raw), pos
    if kind == b"s":
        length, pos = _read_until(data, pos + 2, b":")
        start = pos + 1                       # opening quote
        end = start + int(length)
        return data[start:end].decode("utf-8", errors="replace"), end + 2
    if kind == b"a":
        count, pos = _read_until(data, pos + 2, b":")
        return _read_items(data, pos + 1, int(count))
    if kind == b"O":
        length, pos = _read_until(data, pos + 2, b":")
        pos += 1 + int(length) + 2            # "ClassName":
        count, pos = _read_until(data, pos, b":")
        return _read_items(data, pos + 1, int(count))
    raise ValueError(f"unsupported PHP serialize token {kind!r} at byte {pos}")


def _read_items(data: bytes, pos: int, count: int):
    items = {}
    for _ in range(count):
        key, pos = _unserialize(data, pos)
        items[key], pos = _unserialize(data, pos)
    if list(items) == list(range(count)):
        return list(items.values()), pos + 1  # closing brace
    return items, pos + 1


# --------------------------------------------------------------------------
# 2. Formatted text: HTML into Markdown
# --------------------------------------------------------------------------

HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCKS = {
    "p", "div", "section", "article", "header", "footer", "aside", "main",
    "nav", "figure", "figcaption", "table", "tr", "dl", "dt", "dd", "address",
}
INLINE = {"strong": "**", "b": "**", "em": "*", "i": "*", "code": "`", "s": "~~", "del": "~~"}
SKIP = {"script", "style"}
_BR = "\x00"


class _MarkdownWriter(HTMLParser):
    """Collect Markdown blocks from HTML. Forgiving: never raises on bad markup.

    Keeps what a reviewer needs to read the copy — paragraphs, headings,
    links, emphasis, lists, quotes, images. Tables and embeds fall through as
    plain text; the untouched HTML is kept alongside for anything lost here.
    """

    def __init__(self, image=None):
        super().__init__(convert_charrefs=True)
        self.image = image          # callback(src, alt) -> src to write
        self.blocks: list = []      # (markdown, is_list_item)
        self.parts: list = []
        self.prefix = ""
        self.lists: list = []       # [tag, items so far]
        self.links: list = []       # (index into parts, href)
        self.quote = 0
        self.pre = False
        self.skip = 0

    def flush(self):
        text = "".join(self.parts)
        self.parts = []
        text = re.sub(r"[ \t\r\n\f\xa0]+", " ", text).replace(_BR, "\n")
        text = "\n".join(line.strip() for line in text.split("\n")).strip()
        if not text:
            return
        prefix, self.prefix = self.prefix, ""
        text = prefix + text
        if self.quote:
            text = "\n".join("> " * self.quote + line for line in text.split("\n"))
        self.blocks.append((text, bool(prefix) and bool(self.lists)))

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if self.skip:
            return
        attrs = dict(attrs)
        if tag in HEADINGS:
            self.flush()
            self.prefix = "#" * int(tag[1]) + " "
        elif tag in ("ul", "ol"):
            self.flush()
            self.lists.append([tag, 0])
        elif tag == "li":
            self.flush()
            if not self.lists:
                self.lists.append(["ul", 0])
            self.lists[-1][1] += 1
            kind, n = self.lists[-1]
            indent = "  " * (len(self.lists) - 1)
            self.prefix = indent + (f"{n}. " if kind == "ol" else "- ")
        elif tag == "blockquote":
            self.flush()
            self.quote += 1
        elif tag == "pre":
            self.flush()
            self.pre = True
        elif tag == "br":
            self.parts.append("\n" if self.pre else _BR)
        elif tag == "hr":
            self.flush()
            self.blocks.append(("---", False))
        elif tag == "a":
            self.links.append((len(self.parts), attrs.get("href")))
        elif tag == "img":
            src, alt = attrs.get("src") or "", attrs.get("alt") or ""
            if src and self.image:
                src = self.image(src, alt)
            if src:
                self.parts.append(f"![{alt}]({src})")
        elif tag in INLINE and not self.pre:
            self.parts.append(INLINE[tag])
        elif tag in BLOCKS:
            self.flush()

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in HEADINGS or tag == "li":
            self.flush()
            self.prefix = ""
        elif tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag == "blockquote":
            self.flush()
            self.quote = max(0, self.quote - 1)
        elif tag == "pre":
            self.end_pre()
        elif tag == "a":
            if self.links:
                start, href = self.links.pop()
                if href and start <= len(self.parts):
                    self.parts.insert(start, "[")
                    self.parts.append(f"]({href})")
        elif tag in INLINE and not self.pre:
            self.parts.append(INLINE[tag])
        elif tag in BLOCKS:
            self.flush()

    def end_pre(self):
        text = "".join(self.parts).strip("\n")
        self.parts = []
        self.pre = False
        if text.strip():
            self.blocks.append((f"```\n{text}\n```", False))

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def close(self):
        super().close()
        if self.pre:
            self.end_pre()
        self.flush()


def html_to_markdown(html: str, image=None) -> str:
    """HTML in, Markdown out. Consecutive list items are kept on adjacent lines.

    `image(src, alt)` is called for every <img> and returns the src to write,
    which is how an inline image gets repointed at its copy in `assets/`.
    """
    writer = _MarkdownWriter(image)
    try:
        writer.feed(html or "")
        writer.close()
    except Exception:
        pass  # a body that defeats the parser still yields what came before
    out = []
    for i, (text, item) in enumerate(writer.blocks):
        if i:
            out.append("\n" if item and writer.blocks[i - 1][1] else "\n\n")
        out.append(text)
    return "".join(out)


# --------------------------------------------------------------------------
# 3. Reading Drupal's database — the fetch layer, free of Toadshade
# --------------------------------------------------------------------------

#: Where each content entity type keeps its rows. Adding a fielded entity type
#: is one entry here. `path` is None for types that never have their own URL.
#: `meta` lists base columns that describe the entity rather than being its
#: content (author, promotion, account settings): kept, but in the bundle's
#: meta instead of on the page. `files` lists multi-column base fields that
#: point at a managed file, like a media item's thumbnail.
ENTITY_TYPES = {
    "node": {
        "base": "node", "data": "node_field_data", "id": "nid", "revision": "vid",
        "bundle": "type", "label": "title", "path": "/node/{id}",
        "references": {"uid": "user"}, "meta": ("uid", "promote", "sticky"),
    },
    "taxonomy_term": {
        "base": "taxonomy_term_data", "data": "taxonomy_term_field_data", "id": "tid",
        "revision": "revision_id", "bundle": "vid", "label": "name",
        "path": "/taxonomy/term/{id}", "meta": ("weight",),
        "multi": {"parent": ("entity_reference", "taxonomy_term")},
    },
    "user": {
        "base": "users", "data": "users_field_data", "id": "uid", "revision": None,
        "bundle": None, "label": "name", "path": "/user/{id}",
        "meta": ("mail", "init", "pass", "timezone", "access", "login",
                 "preferred_langcode", "preferred_admin_langcode"),
        "multi": {"roles": ("entity_reference", "user_role")},
    },
    "media": {
        "base": "media", "data": "media_field_data", "id": "mid", "revision": "vid",
        "bundle": "bundle", "label": "name", "path": "/media/{id}",
        "references": {"uid": "user"}, "meta": ("uid",), "files": ("thumbnail",),
    },
    "paragraph": {
        "base": "paragraphs_item", "data": "paragraphs_item_field_data", "id": "id",
        "revision": "revision_id", "bundle": "type", "label": None, "path": None,
    },
}

#: Referenced entities of these types are embedded in the referencing record.
EMBEDDED_TYPES = {"media", "paragraph"}
MAX_DEPTH = 10
IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def dedicated_table_name(entity_type: str, field_name: str, storage_uuid: str = "") -> str:
    """The `{entity}__{field}` table, including Drupal's rule for long names.

    Mirrors DefaultTableMapping: past 48 characters the name becomes the
    entity type (trimmed to 34) plus ten hex digits of a hash of the field
    storage's UUID.
    """
    name = f"{entity_type}__{field_name}"
    if len(name) > 48:
        digest = hashlib.sha256((storage_uuid or "").encode("utf-8")).hexdigest()[:10]
        name = f"{entity_type[:34]}__{digest}"
    return name


def connect(dsn: str, extra: str = "drupal", password_env: str = "DRUPAL_DB_PASSWORD"):
    """Open a DB-API connection from `mysql://user:pass@host:port/db` or
    `sqlite:///path.db`. The MySQL password may come from `password_env`.
    `extra` names the pip extra to suggest when PyMySQL is missing."""
    parsed = urlparse(dsn)
    if parsed.scheme == "sqlite":
        import sqlite3  # noqa: PLC0415

        path = dsn.split("://", 1)[1]
        return sqlite3.connect(path[1:] if path.startswith("/") else path)
    if parsed.scheme in ("mysql", "mariadb"):
        try:
            import pymysql  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise SystemExit(
                "Reading MySQL/MariaDB needs PyMySQL:\n"
                f"    pip install 'toadshade[{extra}]'"
            ) from exc
        password = (
            unquote(parsed.password) if parsed.password is not None
            else os.environ.get(password_env, "")
        )
        return pymysql.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 3306,
            user=unquote(parsed.username or ""),
            password=password,
            database=parsed.path.lstrip("/"),
            charset="utf8mb4",
        )
    raise SystemExit(f"unsupported database URL {dsn!r}; use mysql://user@host/database")


def _plain(value):
    """Database values as JSON-friendly Python values."""
    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


class DrupalDatabase:
    """Read-only access to a Drupal 10/11 database. Yields plain dicts."""

    FIELD_ROW_COLUMNS = {"bundle", "deleted", "entity_id", "revision_id", "langcode", "delta"}
    #: Where each entity type keeps its rows. A subclass reading another
    #: storage (Backdrop's, say) swaps this table and the queries that use it.
    descriptors = ENTITY_TYPES

    def __init__(self, connection, prefix: str = ""):
        self.connection = connection
        self.prefix = prefix
        is_sqlite = type(connection).__module__.startswith("sqlite3")
        self.placeholder = "?" if is_sqlite else "%s"
        self._config: dict = {}
        self._field_rows: dict = {}
        self._references: dict = {}
        self._files: dict = {}
        self._aliases: dict | None = None

    # -- plumbing ---------------------------------------------------------

    def table(self, name: str) -> str:
        if not IDENTIFIER.match(name):
            raise ValueError(f"refusing unsafe table name {name!r}")
        return self.prefix + name

    def query(self, sql: str, params=()) -> list:
        cursor = self.connection.cursor()
        try:
            cursor.execute(sql.replace("%s", self.placeholder), tuple(params))
            columns = [d[0] for d in cursor.description or ()]
            return [
                {c: _plain(v) for c, v in zip(columns, row)} for row in cursor.fetchall()
            ]
        finally:
            cursor.close()

    def optional_query(self, sql: str, params=()) -> list:
        """A query against a table a site may not have (no Paragraphs, say)."""
        try:
            return self.query(sql, params)
        except Exception:
            return []

    # -- configuration ----------------------------------------------------

    def config(self, name: str):
        if name not in self._config:
            rows = self.optional_query(
                f"SELECT data FROM {self.table('config')} WHERE collection = %s AND name = %s",
                ("", name),
            )
            self._config[name] = php_unserialize(rows[0]["data"]) if rows else None
        return self._config[name]

    def config_prefix(self, prefix: str) -> dict:
        rows = self.optional_query(
            f"SELECT name, data FROM {self.table('config')} "
            "WHERE collection = %s AND name LIKE %s ORDER BY name",
            ("", prefix + "%"),
        )
        return {
            row["name"]: php_unserialize(row["data"])
            for row in rows if row["name"].startswith(prefix)
        }

    def field_definitions(self, entity_type: str, bundle: str) -> list:
        """Configurable fields on one bundle, in form-display order."""
        storages = {
            cfg.get("field_name"): cfg
            for cfg in self.config_prefix(f"field.storage.{entity_type}.").values()
        }
        display = self.config(f"core.entity_form_display.{entity_type}.{bundle}.default") or {}
        weights = display.get("content") or {}

        definitions = []
        for cfg in self.config_prefix(f"field.field.{entity_type}.{bundle}.").values():
            name = cfg.get("field_name")
            storage = storages.get(name)
            if not storage:
                continue
            definitions.append({
                "name": name,
                "type": storage.get("type") or cfg.get("field_type"),
                "label": cfg.get("label") or name,
                "cardinality": storage.get("cardinality", 1),
                "target_type": (storage.get("settings") or {}).get("target_type"),
                "table": dedicated_table_name(entity_type, name, storage.get("uuid", "")),
            })

        def order(definition):
            placed = weights.get(definition["name"])
            if isinstance(placed, dict):
                return (0, placed.get("weight") or 0, definition["name"])
            return (1, 0, definition["name"])   # hidden on the form: last

        return sorted(definitions, key=order)

    def field_rows(self, table: str) -> dict:
        """Every live row of one field table, grouped by entity id."""
        if table not in self._field_rows:
            grouped: dict = {}
            for row in self.optional_query(
                f"SELECT * FROM {self.table(table)} WHERE deleted = 0 ORDER BY entity_id, delta"
            ):
                grouped.setdefault(row["entity_id"], []).append(row)
            self._field_rows[table] = grouped
        return self._field_rows[table]

    # -- paths and files --------------------------------------------------

    def aliases(self) -> dict:
        """{system path: {langcode: alias}} for every active alias."""
        if self._aliases is None:
            self._aliases = {}
            for row in self.optional_query(
                f"SELECT path, alias, langcode FROM {self.table('path_alias')} "
                "WHERE status = 1 ORDER BY id"
            ):
                self._aliases.setdefault(row["path"], {})[row["langcode"]] = row["alias"]
        return self._aliases

    def alias_for(self, path: str, langcode: str | None = None):
        by_language = self.aliases().get(path) or {}
        for candidate in (langcode, "und", "zxx"):
            if candidate in by_language:
                return by_language[candidate]
        return next(iter(by_language.values()), None)

    def front_page(self):
        return ((self.config("system.site") or {}).get("page") or {}).get("front")

    def is_front(self, path: str, alias=None) -> bool:
        front = self.front_page()
        return bool(front) and front in (path, alias)

    def file(self, fid):
        if fid not in self._files:
            rows = self.optional_query(
                f"SELECT * FROM {self.table('file_managed')} WHERE fid = %s", (fid,)
            )
            self._files[fid] = rows[0] if rows else None
        return self._files[fid]

    def reference(self, entity_type: str, entity_id):
        """What a reference to a page-like entity points at: label and URL."""
        key = (entity_type, entity_id)
        if key not in self._references:
            desc = self.descriptors.get(entity_type)
            if not desc or not desc["path"]:
                self._references[key] = None
            else:
                rows = self.optional_query(
                    f"SELECT {desc['label']} AS label, langcode FROM {self.table(desc['data'])} "
                    f"WHERE {desc['id']} = %s AND default_langcode = 1",
                    (entity_id,),
                )
                path = desc["path"].format(id=entity_id)
                row = rows[0] if rows else {}
                alias = self.alias_for(path, row.get("langcode"))
                self._references[key] = {
                    "entity_type": entity_type, "id": entity_id, "exists": bool(rows),
                    "label": row.get("label"), "path": path, "alias": alias,
                    "front": self.is_front(path, alias),
                }
        return self._references[key]

    # -- entities ---------------------------------------------------------

    def _select(self, entity_type: str, where: str = "", params=()) -> list:
        desc = self.descriptors[entity_type]
        return self.optional_query(
            f"SELECT d.*, b.uuid AS uuid FROM {self.table(desc['data'])} d "
            f"JOIN {self.table(desc['base'])} b ON b.{desc['id']} = d.{desc['id']} "
            f"WHERE d.default_langcode = 1 {where} ORDER BY d.{desc['id']}",
            params,
        )

    def fetch_entities(self, entity_type: str, bundles=None) -> Iterator[dict]:
        """Yield every entity of one type, default revision and language only."""
        desc = self.descriptors[entity_type]
        for row in self._select(entity_type):
            if entity_type == "user" and not row["uid"]:
                continue  # uid 0 is the anonymous user, not a person
            bundle = row[desc["bundle"]] if desc["bundle"] else entity_type
            if bundles and bundle not in bundles:
                continue
            yield self.record(entity_type, row)

    def load_entity(self, entity_type: str, entity_id, depth: int = 0):
        rows = self._select(entity_type, f"AND d.{self.descriptors[entity_type]['id']} = %s", (entity_id,))
        return self.record(entity_type, rows[0], depth) if rows else None

    def record(self, entity_type: str, row: dict, depth: int = 0) -> dict:
        desc = self.descriptors[entity_type]
        entity_id = row[desc["id"]]
        bundle = row[desc["bundle"]] if desc["bundle"] else entity_type
        langcode = row.get("langcode")
        path = desc["path"].format(id=entity_id) if desc["path"] else None
        alias = self.alias_for(path, langcode) if path else None

        record = {
            "entity_type": entity_type,
            "bundle": bundle,
            "id": entity_id,
            "revision_id": row.get(desc["revision"]) if desc["revision"] else None,
            "uuid": row.get("uuid"),
            "langcode": langcode,
            "label": row.get(desc["label"]) if desc["label"] else None,
            "path": path,
            "alias": alias,
            "front": bool(path) and self.is_front(path, alias),
            "base": {k: v for k, v in row.items() if k != "uuid"},
            "base_references": {
                column: self.reference(target, row[column])
                for column, target in (desc.get("references") or {}).items()
                if row.get(column)
            },
            "base_files": {
                name: self.file(row[f"{name}__target_id"])
                for name in desc.get("files", ())
                if row.get(f"{name}__target_id")
            },
            "fields": [],
        }

        definitions = self.field_definitions(entity_type, bundle)
        for name, (field_type, target_type) in (desc.get("multi") or {}).items():
            definitions.append({
                "name": name, "type": field_type, "label": name, "cardinality": -1,
                "target_type": target_type, "table": f"{entity_type}__{name}",
            })

        for definition in definitions:
            values = [
                self._enrich(definition, value, depth)
                for value in self.field_values(definition, entity_id, langcode)
            ]
            record["fields"].append({**definition, "values": values})
        return record

    def field_values(self, definition: dict, entity_id, langcode) -> list:
        prefix = definition["name"] + "_"
        values = []
        for row in self.field_rows(definition["table"]).get(entity_id, []):
            if langcode and row.get("langcode") not in (langcode, None):
                continue  # a translation's copy of the value
            value = {
                column[len(prefix):]: v for column, v in row.items()
                if column.startswith(prefix) and column not in self.FIELD_ROW_COLUMNS
            }
            if "target_id" in value and not value["target_id"]:
                continue  # e.g. a term's parent 0: "no parent", not a reference
            values.append(value)
        return values

    def _enrich(self, definition: dict, value: dict, depth: int) -> dict:
        """Resolve what a raw field value points at: files, embedded
        paragraphs and media, and the URLs of referenced pages."""
        field_type, target_type = definition["type"], definition["target_type"]
        if field_type in ("image", "file"):
            value["file"] = self.file(value.get("target_id"))
        elif field_type in ("entity_reference", "entity_reference_revisions"):
            if target_type in EMBEDDED_TYPES and target_type in self.descriptors and depth < MAX_DEPTH:
                value["entity"] = self.load_entity(target_type, value.get("target_id"), depth + 1)
            elif target_type in self.descriptors:
                value["target"] = self.reference(target_type, value.get("target_id"))
        elif field_type == "link":
            uri = value.get("uri") or ""
            match = re.match(r"^entity:([a-z_]+)/(\d+)$", uri)
            if match:
                value["target"] = self.reference(match.group(1), int(match.group(2)))
            elif uri.startswith("internal:"):
                value["alias"] = self.alias_for(uri[len("internal:"):])
        return value


# --------------------------------------------------------------------------
# 4. Records into bundles
# --------------------------------------------------------------------------

#: Row columns that are storage bookkeeping rather than content.
BOOKKEEPING = {
    "uuid", "langcode", "default_langcode", "revision_translation_affected",
    "revision_default", "parent_id", "parent_type", "parent_field_name",
    "behavior_settings",
}
#: Kept in `meta` for a page, dropped for embedded paragraphs and media.
META_COLUMNS = ("status", "created", "changed")
TIMESTAMP_COLUMNS = {"created", "changed", "access", "login"}
TEXT_TYPES = {"text", "text_long", "text_with_summary"}
FILE_TYPES = {"image", "file"}
#: Props the Markdown layer leaves out: machine detail, not reviewable prose.
NOT_FOR_REVIEWERS = {"body_html", "format"}

DEFAULT_ENTITY_TYPES = ("node", "taxonomy_term", "user")
DEFAULT_FILES_URL = "/sites/default/files"


def _iso(value):
    """A Unix timestamp as ISO 8601 UTC. Zero ("never") becomes None."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return value
    if not seconds:
        return None
    return _dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc).isoformat()


def page_url(target: dict) -> str:
    """The public URL of a record or reference: front page, alias, or path."""
    if target.get("front"):
        return "/"
    return target.get("alias") or target.get("path") or ""


def base_value(record: dict, column: str):
    """A base column's value as exported: references as URLs, times as ISO."""
    reference = record["base_references"].get(column)
    if reference:
        return page_url(reference)
    value = record["base"].get(column)
    return _iso(value) if column in TIMESTAMP_COLUMNS else value


class _BundleBuilder:
    """Builds one bundle's component tree, collecting its asset bytes."""

    def __init__(self, exporter: "DrupalToToadshade"):
        self.exporter = exporter
        self.count = 0
        self.assets: dict = {}       # bundle path -> bytes
        self._by_source: dict = {}   # file on disk -> bundle path

    def next_id(self) -> str:
        self.count += 1
        return f"s{self.count}"

    # -- components -------------------------------------------------------

    def component(self, record: dict) -> dict:
        desc = self.exporter.descriptors[record["entity_type"]]
        component = {"id": self.next_id(), "type": f"{record['entity_type']}-{record['bundle']}"}
        props: dict = {}
        slots: dict = {}
        if record.get("label") is not None:
            props["title"] = record["label"]

        skip = {desc["id"], desc["revision"], desc["bundle"], desc["label"],
                *BOOKKEEPING, *META_COLUMNS, *desc.get("meta", ())}
        groups: dict = {}
        for column, value in record["base"].items():
            if column in skip:
                continue
            if "__" in column:
                name, part = column.split("__", 1)
                groups.setdefault(name, {})[part] = value
                continue
            value = base_value(record, column)
            if value is not None:
                props[column] = value

        # Base fields stored as several columns: a term's description, a
        # media item's thumbnail. A thumbnail that is the item's own image
        # file adds nothing, so it is left out.
        field_uris = {
            (value.get("file") or {}).get("uri")
            for field in record["fields"] if field["type"] in FILE_TYPES
            for value in field["values"]
        }
        for name, parts in groups.items():
            if name in record["base_files"]:
                uri = (record["base_files"][name] or {}).get("uri")
                if uri and uri not in field_uris:
                    self.base_file(name, parts, uri, record.get("label"), props)
            elif "value" in parts and "format" in parts:
                if parts["value"]:
                    slots[name] = [self.text(parts, name.replace("_", " ").capitalize())]
            elif any(v is not None for v in parts.values()):
                props[name] = {k: v for k, v in parts.items() if v is not None}

        for field in record["fields"]:
            self.field(field, props, slots)

        component["props"] = props
        if slots:
            component["slots"] = slots
        return component

    def field(self, field: dict, props: dict, slots: dict) -> None:
        field_type, name, values = field["type"], field["name"], field["values"]
        if not values:
            return
        single = field["cardinality"] == 1

        if field_type in TEXT_TYPES:
            slots[name] = [self.text(v, field["label"]) for v in values]
        elif field_type in FILE_TYPES:
            slots[name] = [self.file(field_type, v, field["label"]) for v in values]
        elif field_type in ("entity_reference", "entity_reference_revisions"):
            children, references = [], []
            for value in values:
                if value.get("entity"):
                    children.append(self.component(value["entity"]))
                elif value.get("target"):
                    references.append(page_url(value["target"]))
                else:
                    references.append(str(value.get("target_id")))
            if children:
                slots[name] = children
            if references:
                props[name] = references[0] if single else references
        elif field_type == "link":
            links, options = [], []
            for value in values:
                if value.get("target"):
                    url = page_url(value["target"])
                else:
                    uri = value.get("uri") or ""
                    url = value.get("alias") or (uri[len("internal:"):] if uri.startswith("internal:") else uri)
                links.append(f"{value['title']} → {url}" if value.get("title") else url)
                try:
                    options.append(php_unserialize(value["options"]) if value.get("options") else None)
                except (ValueError, IndexError):
                    options.append(value.get("options"))
            props[name] = links[0] if single else links
            if any(options):
                props[f"{name}_options"] = options[0] if single else options
        else:
            scalars = [self.scalar(field_type, v) for v in values]
            props[name] = scalars[0] if single else scalars

    @staticmethod
    def scalar(field_type: str, value: dict):
        if set(value) != {"value"}:
            if field_type == "smartdate":
                # Smart Date stores Unix timestamps; ISO is what a reviewer reads.
                value = {k: _iso(v) if k in ("value", "end_value") else v for k, v in value.items()}
            return {k: v for k, v in value.items() if v is not None}
        raw = value["value"]
        if field_type == "boolean":
            return bool(int(raw or 0))
        if field_type == "timestamp":
            return _iso(raw)
        return raw

    def text(self, value: dict, label: str) -> dict:
        component = {"id": self.next_id(), "type": "text", "label": label}
        html = value.get("value") or ""
        images: list = []
        props = {
            "body": html_to_markdown(html, image=lambda src, alt: self.inline_image(src, alt, images)),
            "body_html": html,
        }
        if value.get("format"):
            props["format"] = value["format"]
        if value.get("summary"):
            props["summary"] = value["summary"]
        component["props"] = props
        if images:
            component["slots"] = {"images": images}
        return component

    def file(self, field_type: str, value: dict, label: str) -> dict:
        component = {"id": self.next_id(), "type": field_type, "label": label}
        uri = (value.get("file") or {}).get("uri")
        asset = self.asset(self.exporter.local_path_for_uri(uri)) if uri else None
        title = value.get("title") or value.get("description")

        props: dict = {}
        if asset:
            ref = {"$asset": asset}
            if field_type == "image":
                ref["alt"] = value.get("alt") or ""
            if title:
                ref["title"] = title
            props[field_type] = ref
        else:
            # Not on disk, so a plain string — never a reference that fails.
            props[f"{field_type}_url"] = uri or f"file:{value.get('target_id')}"
            if value.get("alt"):
                props["alt"] = value["alt"]
            if title:
                props["title"] = title
        for key in ("width", "height"):
            if value.get(key) is not None:
                props[key] = value[key]
        component["props"] = props
        return component

    def base_file(self, name: str, parts: dict, uri: str, label, props: dict) -> None:
        asset = self.asset(self.exporter.local_path_for_uri(uri))
        if asset:
            ref = {"$asset": asset, "alt": parts.get("alt") or label or ""}
            if parts.get("title"):
                ref["title"] = parts["title"]
            props[name] = ref
        else:
            props[f"{name}_url"] = uri

    def inline_image(self, src: str, alt: str, sink: list) -> str:
        """An <img> in body HTML: copy it into the bundle when it's a site file."""
        asset = self.asset(self.exporter.local_path_for_url(src))
        if not asset:
            return src
        sink.append({
            "id": self.next_id(),
            "type": "image",
            "props": {"image": {"$asset": asset, "alt": alt}},
        })
        return asset

    # -- assets -----------------------------------------------------------

    def asset(self, path) -> str | None:
        """Read a file into the bundle once, under a unique `assets/` name."""
        if path is None:
            return None
        key = str(path)
        if key not in self._by_source:
            stem = slugify(path.stem, fallback="file")
            suffix = re.sub(r"[^a-z0-9.]", "", path.suffix.lower())
            candidate, n = f"assets/{stem}{suffix}", 2
            while candidate in self.assets:
                candidate = f"assets/{stem}-{n}{suffix}"
                n += 1
            self.assets[candidate] = path.read_bytes()
            self._by_source[key] = candidate
        return self._by_source[key]


def drupal_markdown(draft: BundleDraft) -> str:
    """The generic human layer, minus HTML and format names, with field
    labels as headings where a component has no title of its own."""

    def for_reviewers(components):
        out = []
        for component in components:
            component = dict(component)
            props = {
                k: v for k, v in (component.get("props") or {}).items()
                if k not in NOT_FOR_REVIEWERS
            }
            if component.get("label") and not (props.get("heading") or props.get("title")):
                props = {"heading": component["label"], **props}
            component["props"] = props
            if component.get("slots"):
                component["slots"] = {
                    name: for_reviewers(children)
                    for name, children in component["slots"].items()
                }
            out.append(component)
        return out

    return markdown_for(BundleDraft(
        slug=draft.slug, title=draft.title, alias=draft.alias,
        components=for_reviewers(draft.components),
    ))


# --------------------------------------------------------------------------
# 5. The exporter
# --------------------------------------------------------------------------


class DrupalToToadshade(Exporter):
    """One node, term, or user, one bundle, filed by its path alias."""

    name = "drupal-to-toadshade"
    #: The swappable pieces. `backdrop.py` replaces all four; the bundle
    #: layer below reads the source only through them.
    database_class = DrupalDatabase
    builder_class = _BundleBuilder
    descriptors = ENTITY_TYPES
    source_prefix = "drupal"

    def __init__(
        self, source, content_root, files_dir=None, private_dir=None,
        files_url=DEFAULT_FILES_URL, entity_types=DEFAULT_ENTITY_TYPES,
        bundles=None, stop_after=None, prefix="", sections=None,
    ):
        super().__init__(source, content_root)
        if isinstance(source, DrupalDatabase):
            self.db = source
        elif isinstance(source, str):
            self.db = self.database_class(connect(source), prefix)
        else:
            self.db = self.database_class(source, prefix)
        self.files_dir = Path(files_dir) if files_dir else None
        self.private_dir = Path(private_dir) if private_dir else None
        self.files_url = files_url.rstrip("/") + "/"
        self.entity_types = list(entity_types)
        self.bundles = set(bundles) if bundles else None
        self.stop_after = stop_after
        #: {"session": "meetings"} or {"node.session": "meetings"}
        self.sections = dict(sections or {})
        self._parents: set | None = None
        self._placed: set = set()

    # -- fetch ------------------------------------------------------------

    def fetch(self) -> Iterable[dict]:
        count = 0
        for entity_type in self.entity_types:
            for record in self.db.fetch_entities(entity_type, self.bundles):
                if self.stop_after and count >= self.stop_after:
                    return
                count += 1
                yield record

    # -- write ------------------------------------------------------------

    def to_bundle(self, record: dict) -> BundleDraft | None:
        builder = self.builder_class(self)
        component = builder.component(record)

        alias = page_url(record).replace(" ", "%20")
        if alias != "/":
            alias = "/" + alias.strip("/")
        directory, slug = self.place(alias, record["entity_type"], record["bundle"])

        meta = {
            "source": f"{self.source_prefix}:{record['entity_type']}/{record['id']}",
            "generator": f"{self.name} {__version__}",
            "entity_type": record["entity_type"],
            "bundle": record["bundle"],
            "id": record["id"],
            "revision_id": record["revision_id"],
            "uuid": record["uuid"],
            "langcode": record["langcode"],
            "path": record["path"],
        }
        for column in (*META_COLUMNS, *self.descriptors[record["entity_type"]].get("meta", ())):
            meta[column] = base_value(record, column)
        meta = {k: v for k, v in meta.items() if v is not None}

        title = record.get("label") or f"{record['bundle']} {record['id']}"
        draft = BundleDraft(
            slug=slug,
            title=str(title),
            alias=alias,
            components=[component],
            assets=builder.assets,
            meta=meta,
            directory=directory,
        )
        draft.markdown = drupal_markdown(draft)
        return draft

    def place(self, alias: str, entity_type: str = "", bundle: str = "") -> tuple:
        """Alias to (directory, slug).

        A page whose alias is also a parent of other pages moves one level
        down, because bundles never nest. A page whose alias has no hierarchy
        at all (`/matt-glaman`) goes under its bundle's section folder, if one
        is configured (`--sections person=people`).
        """
        segments = [slugify(s, fallback="page") for s in alias.strip("/").split("/") if s]
        is_parent = bool(segments) and tuple(segments) in self.parents()
        if not segments:
            segments = ["home"]
        directory = segments if is_parent else segments[:-1]
        if alias != "/" and len(segments) == 1 and not is_parent:
            directory = self.section_for(entity_type, bundle) + directory
        directory = "/".join(directory)

        slug, n = segments[-1], 2
        while (directory, slug) in self._placed:
            slug = f"{segments[-1]}-{n}"
            n += 1
        self._placed.add((directory, slug))
        return directory, slug

    def parents(self) -> set:
        """Every slugified path prefix that has pages beneath it."""
        if self._parents is None:
            paths = [a for by_lang in self.db.aliases().values() for a in by_lang.values()]
            # System paths (/node/5) hold bundles too: /node/0, /user/0, ...
            paths += [d["path"].format(id=0) for d in self.descriptors.values() if d["path"]]
            # A section folder holds bundles, so it counts as a parent too.
            paths += [f"/{folder}/0" for folder in self.sections.values()]
            self._parents = set()
            for path in paths:
                segments = [slugify(s, fallback="page") for s in path.strip("/").split("/") if s]
                for i in range(1, len(segments)):
                    self._parents.add(tuple(segments[:i]))
        return self._parents

    def section_for(self, entity_type: str, bundle: str) -> list:
        """The section folder for `node.session` or plain `session`, as parts."""
        folder = self.sections.get(f"{entity_type}.{bundle}") or self.sections.get(bundle) or ""
        return [slugify(part, fallback="section") for part in folder.split("/") if part]

    # -- files ------------------------------------------------------------

    def local_path_for_uri(self, uri: str):
        """`public://2026-03/x.jpg` to the file on disk, if it is there."""
        scheme, _, rest = (uri or "").partition("://")
        root = {"public": self.files_dir, "private": self.private_dir}.get(scheme)
        return _inside(root, rest)

    def local_path_for_url(self, src: str):
        """A site-relative file URL, including image-style derivatives, to the
        original file on disk."""
        path = unquote(urlparse(src).path)
        if path.startswith("/system/files/"):
            return _inside(self.private_dir, path[len("/system/files/"):])
        if not path.startswith(self.files_url):
            return None
        rest = path[len(self.files_url):]
        style = re.match(r"^styles/[^/]+/(public|private)/(.+)$", rest)
        if style:
            root = self.files_dir if style.group(1) == "public" else self.private_dir
            return _inside(root, style.group(2))
        return _inside(self.files_dir, rest)


def _inside(root, relative: str):
    """`root / relative` if it is an existing file that stays inside root."""
    if root is None or not relative:
        return None
    root = Path(root).resolve()
    candidate = (root / relative).resolve()
    if root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


# --------------------------------------------------------------------------
# 6. Command line
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="drupal-to-toadshade",
        description="Export Drupal 10/11 entities to Toadshade page bundles.",
        epilog="The MySQL password can be given in DRUPAL_DB_PASSWORD instead of the URL.",
    )
    parser.add_argument("content_root", help="directory to write bundles into")
    parser.add_argument("--db", required=True,
                        help="mysql://user[:pass]@host[:port]/database, or sqlite:///file.db")
    parser.add_argument("--files-dir", help="the site's public files directory (sites/default/files)")
    parser.add_argument("--private-dir", help="the site's private files directory")
    parser.add_argument("--files-url", default=DEFAULT_FILES_URL,
                        help=f"URL path of the public files directory (default {DEFAULT_FILES_URL})")
    parser.add_argument("--entity-types", default=",".join(DEFAULT_ENTITY_TYPES),
                        help="comma-separated entity types to export")
    parser.add_argument("--bundles", help="comma-separated bundles to include (default: all)")
    parser.add_argument("--sections",
                        help="bundle=folder pairs for pages whose URL has no hierarchy, "
                             "e.g. session=meetings,person=people")
    parser.add_argument("--prefix", default="", help="database table prefix")
    parser.add_argument("--stop-after", type=int, help="stop after this many entities")
    parser.add_argument("--no-render", action="store_true",
                        help="skip writing the {slug}.html previews")
    args = parser.parse_args(argv)

    entity_types = [t.strip() for t in args.entity_types.split(",") if t.strip()]
    unknown = [t for t in entity_types if t not in ENTITY_TYPES or not ENTITY_TYPES[t]["path"]]
    if unknown:
        parser.error(f"cannot export entity type(s) {', '.join(unknown)} as pages")

    exporter = DrupalToToadshade(
        source=args.db,
        content_root=args.content_root,
        files_dir=args.files_dir,
        private_dir=args.private_dir,
        files_url=args.files_url,
        entity_types=entity_types,
        bundles=[b.strip() for b in args.bundles.split(",")] if args.bundles else None,
        stop_after=args.stop_after,
        prefix=args.prefix,
        sections=dict(
            pair.strip().split("=", 1) for pair in (args.sections or "").split(",") if "=" in pair
        ),
    )

    count = 0
    for path in exporter.export(render=not args.no_render):
        print(path)
        count += 1

    print(f"\n{count} bundle(s) written to {Path(args.content_root)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

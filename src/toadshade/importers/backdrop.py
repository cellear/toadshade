"""backdrop-to-toadshade — Backdrop CMS 1.x entities into page bundles.

Backdrop is a fork of Drupal 7. Its content is in the database, Drupal 7
style (`node`, `field_data_{field}`, `taxonomy_term_data`, `users`,
`file_managed`, `url_alias`), but its configuration — field definitions, the
front page — is JSON files in the active config directory. So this exporter
needs both:

    backdrop-to-toadshade content/ --db mysql://backdrop@localhost/backdrop \\
        --config-dir /var/www/site/files/config_abc123/active \\
        --files-dir /var/www/site/files

The split is the Drupal exporter's, and so is almost all of the code:

    BackdropDatabase    reads Backdrop's storage and yields records in exactly
                        the shape `DrupalDatabase.record()` yields. Knows
                        nothing about Toadshade.
    BackdropToToadshade the Drupal bundle layer (components, assets, Markdown,
                        placement by alias) with Backdrop's descriptors
                        swapped in.

Every table and column name here was checked against Backdrop's own
`hook_schema()` implementations and `field_sql_storage.module` (1.35.x).

Scope matches the Drupal exporter: nodes, taxonomy terms and users are pages;
file and image fields become assets; the current revision in the default
language. Layouts (the Layout module) and Views are site building, not
content, and are not read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterator

from .drupal import (
    DrupalDatabase,
    DrupalToToadshade,
    _BundleBuilder,
    _iso,
    connect,
    php_unserialize,
)

# --------------------------------------------------------------------------
# 1. Where Backdrop keeps things
# --------------------------------------------------------------------------

#: The Drupal exporter's descriptor table, for Backdrop's base tables. Extra
#: keys used only here: `rename` maps a Backdrop base column to the Drupal 10
#: column name the bundle layer understands (`description` is a text field
#: stored as two columns, `picture` is a file id).
ENTITY_TYPES = {
    "node": {
        "base": "node", "id": "nid", "revision": "vid", "bundle": "type",
        "label": "title", "path": "/node/{id}", "references": {"uid": "user"},
        "meta": ("uid", "promote", "sticky", "comment", "tnid", "translate",
                 "scheduled", "comment_close_override"),
    },
    "taxonomy_term": {
        "base": "taxonomy_term_data", "id": "tid", "revision": None,
        "bundle": "vocabulary", "label": "name", "path": "/taxonomy/term/{id}",
        "meta": ("weight",),
        "multi": {"parent": ("entity_reference", "taxonomy_term")},
        "rename": {"description": "description__value", "format": "description__format"},
    },
    "user": {
        "base": "users", "id": "uid", "revision": None, "bundle": None,
        "label": "name", "path": "/user/{id}",
        "meta": ("mail", "init", "pass", "timezone", "access", "login", "language", "data"),
        "multi": {"roles": ("entity_reference", "user_role")},
        "files": ("picture",),
        "rename": {"signature": "signature__value", "signature_format": "signature__format",
                   "picture": "picture__target_id"},
    },
}

#: Backdrop field types whose Drupal 10 counterpart has another name, with the
#: target entity type where it is fixed by the field type.
FIELD_TYPES = {
    "taxonomy_term_reference": ("entity_reference", "taxonomy_term"),
    "entityreference": ("entity_reference", None),   # target from settings
    "link_field": ("link", None),
    "list_boolean": ("boolean", None),
}
#: Column renames per Backdrop field type, into the Drupal 10 value shape.
COLUMNS = {
    "image": {"fid": "target_id"},
    "file": {"fid": "target_id"},
    "taxonomy_term_reference": {"tid": "target_id"},
    "link_field": {"url": "uri", "attributes": "options"},
    "email": {"email": "value"},
    "date": {"value2": "end_value"},
    "datetime": {"value2": "end_value"},
    "datestamp": {"value2": "end_value"},
}
#: Relationships that Drupal 10 stores as fields and Backdrop as plain tables.
#: `DrupalDatabase.record()` asks for them by their Drupal 10 table name.
VIRTUAL_TABLES = {
    "taxonomy_term__parent": ("taxonomy_term_hierarchy", "tid", "parent", "parent_target_id"),
    "user__roles": ("users_roles", "uid", "role", "roles_target_id"),
}
LANGUAGE_NONE = "und"
SYSTEM_PATH = re.compile(r"^/?(node|taxonomy/term|user)/(\d+)/?$")
ENTITY_FOR_PATH = {"node": "node", "taxonomy/term": "taxonomy_term", "user": "user"}


def _slashed(path: str) -> str:
    """Backdrop stores `node/1` and `about/team`; everything else uses `/node/1`."""
    return "/" + (path or "").strip("/")


# --------------------------------------------------------------------------
# 2. Reading Backdrop — the fetch layer, free of Toadshade
# --------------------------------------------------------------------------


class BackdropDatabase(DrupalDatabase):
    """Read-only access to a Backdrop 1.x database plus its config directory.

    Yields the same record dicts as `DrupalDatabase`, so anything written
    against those works on Backdrop unchanged.
    """

    descriptors = ENTITY_TYPES

    def __init__(self, connection, config_dir=None, prefix: str = ""):
        super().__init__(connection, prefix)
        self.config_dir = Path(config_dir) if config_dir else None

    # -- configuration: JSON files, or the config_active table -------------

    def config(self, name: str):
        if name not in self._config:
            data = None
            if self.config_dir is not None:
                path = self.config_dir / f"{name}.json"
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
            else:
                # Sites that set `config_active_class` to ConfigDatabaseStorage
                # keep the same JSON in a table.
                rows = self.optional_query(
                    f"SELECT data FROM {self.table('config_active')} WHERE name = %s", (name,)
                )
                data = json.loads(rows[0]["data"]) if rows else None
            self._config[name] = data
        return self._config[name]

    def config_names(self, prefix: str) -> list:
        if self.config_dir is not None:
            names = [p.name[:-len(".json")] for p in self.config_dir.glob(f"{prefix}*.json")]
        else:
            names = [row["name"] for row in self.optional_query(
                f"SELECT name FROM {self.table('config_active')} WHERE name LIKE %s", (prefix + "%",)
            )]
        return sorted(n for n in names if n.startswith(prefix))

    def config_prefix(self, prefix: str) -> dict:
        return {name: self.config(name) for name in self.config_names(prefix)}

    def field_definitions(self, entity_type: str, bundle: str) -> list:
        """Configurable fields on one bundle, in the order of their widgets."""
        definitions = []
        for instance in self.config_prefix(f"field.instance.{entity_type}.{bundle}.").values():
            if not instance or int(instance.get("deleted") or 0):
                continue
            name = instance.get("field_name")
            field = self.config(f"field.field.{name}") or {}
            if not field or int(field.get("deleted") or 0):
                continue
            storage_type = field.get("type")
            field_type, target_type = FIELD_TYPES.get(storage_type, (storage_type, None))
            settings = field.get("settings") or {}
            if storage_type == "entityreference":
                target_type = settings.get("target_type")
            definitions.append({
                "name": name,
                "type": field_type,
                "storage_type": storage_type,
                "label": instance.get("label") or name,
                "cardinality": int(field.get("cardinality", 1)),
                "target_type": target_type,
                "table": f"field_data_{name}",
                "entity_type": entity_type,
                "weight": float((instance.get("widget") or {}).get("weight") or 0),
            })
        return sorted(definitions, key=lambda d: (d["weight"], d["name"]))

    def field_rows(self, table: str, entity_type: str = "") -> dict:
        """Live rows of one field table for one entity type, by entity id."""
        key = (table, entity_type)
        if key not in self._field_rows:
            grouped: dict = {}
            if table in VIRTUAL_TABLES:
                source, id_column, value_column, as_name = VIRTUAL_TABLES[table]
                rows = self.optional_query(
                    f"SELECT {id_column} AS entity_id, {value_column} AS {as_name} "
                    f"FROM {self.table(source)} ORDER BY {id_column}, {value_column}"
                )
            else:
                rows = self.optional_query(
                    f"SELECT * FROM {self.table(table)} WHERE deleted = 0 AND entity_type = %s "
                    "ORDER BY entity_id, delta",
                    (entity_type,),
                )
            for row in rows:
                grouped.setdefault(row["entity_id"], []).append(row)
            self._field_rows[key] = grouped
        return self._field_rows[key]

    def field_values(self, definition: dict, entity_id, langcode) -> list:
        rows = self.field_rows(definition["table"], definition.get("entity_type", ""))
        rows = rows.get(entity_id, [])
        # Field rows are 'und' unless the field is translatable, in which case
        # they carry the entity's language. Rows in any other language are a
        # translation's copy. If nothing matches, keep one language's rows.
        wanted = {langcode, LANGUAGE_NONE, None, ""}
        kept = [r for r in rows if r.get("language") in wanted]
        if not kept and rows:
            first = rows[0].get("language")
            kept = [r for r in rows if r.get("language") == first]

        prefix = definition["name"] + "_"
        renames = COLUMNS.get(definition.get("storage_type"), {})
        values = []
        for row in kept:
            value = {}
            for column, v in row.items():
                if column.startswith(prefix):
                    part = column[len(prefix):]
                    value[renames.get(part, part)] = v
            if "target_id" in value and not value["target_id"]:
                continue  # a term's parent 0: "no parent", not a reference
            values.append(value)
        return values

    def _enrich(self, definition: dict, value: dict, depth: int) -> dict:
        if definition["type"] == "link":
            # Backdrop link URLs are `node/2`, `about`, `<front>` or absolute.
            uri = value.get("uri") or ""
            match = SYSTEM_PATH.match(uri)
            if match:
                value["target"] = self.reference(ENTITY_FOR_PATH[match.group(1)], int(match.group(2)))
            elif uri == "<front>":
                value["alias"] = "/"
            elif uri and not re.match(r"^([a-z][a-z0-9+.-]*:|//|#)", uri, re.I):
                path = _slashed(uri)
                value["alias"] = self.alias_for(path) or path
            return value
        return super()._enrich(definition, value, depth)

    # -- paths and files --------------------------------------------------

    def aliases(self) -> dict:
        """{"/node/1": {langcode: "/about"}}. The newest alias wins, as in
        Backdrop's own lookup (ORDER BY pid)."""
        if self._aliases is None:
            self._aliases = {}
            for row in self.optional_query(
                f"SELECT source, alias, langcode FROM {self.table('url_alias')} ORDER BY pid"
            ):
                by_language = self._aliases.setdefault(_slashed(row["source"]), {})
                by_language[row["langcode"]] = _slashed(row["alias"])
        return self._aliases

    def front_page(self):
        front = (self.config("system.core") or {}).get("site_frontpage")
        return _slashed(front) if front else None

    def reference(self, entity_type: str, entity_id):
        key = (entity_type, entity_id)
        if key not in self._references:
            desc = self.descriptors.get(entity_type)
            if not desc or not desc["path"]:
                self._references[key] = None
            else:
                rows = self.optional_query(
                    f"SELECT * FROM {self.table(desc['base'])} WHERE {desc['id']} = %s",
                    (entity_id,),
                )
                row = rows[0] if rows else {}
                path = desc["path"].format(id=entity_id)
                alias = self.alias_for(path, row.get("langcode"))
                self._references[key] = {
                    "entity_type": entity_type, "id": entity_id, "exists": bool(rows),
                    "label": row.get(desc["label"]), "path": path, "alias": alias,
                    "front": self.is_front(path, alias),
                }
        return self._references[key]

    # -- entities ---------------------------------------------------------

    def _select(self, entity_type: str, where: str = "", params=()) -> list:
        """Base rows, renamed into the Drupal 10 column shape."""
        desc = self.descriptors[entity_type]
        rows = self.optional_query(
            f"SELECT d.* FROM {self.table(desc['base'])} d WHERE 1 = 1 {where} "
            f"ORDER BY d.{desc['id']}",
            params,
        )
        renames = desc.get("rename") or {}
        out = []
        for row in rows:
            row = {renames.get(k, k): v for k, v in row.items()}
            for name in desc.get("files", ()):
                if not row.get(f"{name}__target_id"):
                    row[f"{name}__target_id"] = None   # 0 means "no picture"
            if entity_type == "user" and row.get("data"):
                try:
                    row["data"] = php_unserialize(row["data"])
                except (ValueError, IndexError):
                    pass
            out.append(row)
        return out

    def fetch_entities(self, entity_type: str, bundles=None) -> Iterator[dict]:
        """Every entity of one type: current revision, source language only."""
        desc = self.descriptors[entity_type]
        for row in self._select(entity_type):
            if entity_type == "user" and not row["uid"]:
                continue  # uid 0 is the anonymous user, not a person
            if entity_type == "node" and row.get("tnid") and row["tnid"] != row["nid"]:
                continue  # a translation of another node (translation module)
            bundle = row[desc["bundle"]] if desc["bundle"] else entity_type
            if bundles and bundle not in bundles:
                continue
            yield self.record(entity_type, row)


# --------------------------------------------------------------------------
# 3. Records into bundles: the Drupal bundle layer, with Backdrop's dates
# --------------------------------------------------------------------------

DATE_TYPES = {"date", "datetime", "datestamp"}


class _BackdropBundleBuilder(_BundleBuilder):
    @staticmethod
    def scalar(field_type: str, value: dict):
        if field_type in DATE_TYPES:
            def iso(v):
                if v is None or v == "":
                    return None
                if field_type == "datestamp":
                    return _iso(v)
                return str(v).replace(" ", "T")   # datetime: '2026-04-18 14:00:00'
            value = {k: iso(v) if k in ("value", "end_value") else v for k, v in value.items()}
            value = {k: v for k, v in value.items() if v is not None}
            return value["value"] if set(value) == {"value"} else value
        return _BundleBuilder.scalar(field_type, value)


DEFAULT_ENTITY_TYPES = ("node", "taxonomy_term", "user")
DEFAULT_FILES_URL = "/files"


class BackdropToToadshade(DrupalToToadshade):
    """One Backdrop node, term, or user, one bundle, filed by its URL alias."""

    name = "backdrop-to-toadshade"
    database_class = BackdropDatabase
    builder_class = _BackdropBundleBuilder
    descriptors = ENTITY_TYPES
    source_prefix = "backdrop"

    def __init__(self, source, content_root, config_dir=None, files_dir=None,
                 files_url=DEFAULT_FILES_URL, entity_types=DEFAULT_ENTITY_TYPES, **kwargs):
        prefix = kwargs.get("prefix", "")
        if isinstance(source, str):
            source = BackdropDatabase(
                connect(source, extra="backdrop", password_env="BACKDROP_DB_PASSWORD"),
                config_dir, prefix,
            )
        elif not isinstance(source, BackdropDatabase):
            source = BackdropDatabase(source, config_dir, prefix)
        super().__init__(source, content_root, files_dir=files_dir, files_url=files_url,
                         entity_types=entity_types, **kwargs)


# --------------------------------------------------------------------------
# 4. Command line
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="backdrop-to-toadshade",
        description="Export Backdrop CMS 1.x entities to Toadshade page bundles.",
        epilog="The MySQL password can be given in BACKDROP_DB_PASSWORD instead of the URL. "
               "The active config directory is $config_directories['active'] in settings.php, "
               "usually files/config_<hash>/active.",
    )
    parser.add_argument("content_root", help="directory to write bundles into")
    parser.add_argument("--db", required=True,
                        help="mysql://user[:pass]@host[:port]/database, or sqlite:///file.db")
    parser.add_argument("--config-dir",
                        help="the active config directory; omit only if config is kept in the "
                             "database (config_active table)")
    parser.add_argument("--files-dir", help="the site's public files directory (usually files/)")
    parser.add_argument("--private-dir", help="the site's private files directory")
    parser.add_argument("--files-url", default=DEFAULT_FILES_URL,
                        help=f"URL path of the public files directory (default {DEFAULT_FILES_URL})")
    parser.add_argument("--entity-types", default=",".join(DEFAULT_ENTITY_TYPES),
                        help="comma-separated entity types to export")
    parser.add_argument("--bundles", help="comma-separated bundles to include (default: all)")
    parser.add_argument("--sections",
                        help="bundle=folder pairs for pages whose URL has no hierarchy, "
                             "e.g. post=blog,page=pages")
    parser.add_argument("--prefix", default="", help="database table prefix")
    parser.add_argument("--stop-after", type=int, help="stop after this many entities")
    parser.add_argument("--no-render", action="store_true",
                        help="skip writing the {slug}.html previews")
    args = parser.parse_args(argv)

    entity_types = [t.strip() for t in args.entity_types.split(",") if t.strip()]
    unknown = [t for t in entity_types if t not in ENTITY_TYPES]
    if unknown:
        parser.error(f"cannot export entity type(s) {', '.join(unknown)} as pages")
    if args.config_dir and not Path(args.config_dir).is_dir():
        parser.error(f"--config-dir {args.config_dir} is not a directory")

    exporter = BackdropToToadshade(
        source=args.db,
        content_root=args.content_root,
        config_dir=args.config_dir,
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
    if exporter.db.config("system.core") is None:
        parser.error("no Backdrop configuration found (system.core.json); "
                     "pass --config-dir pointing at the active config directory")

    count = 0
    for path in exporter.export(render=not args.no_render):
        print(path)
        count += 1

    print(f"\n{count} bundle(s) written to {Path(args.content_root)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

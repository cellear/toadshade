"""apple-notes-to-toadshade — Apple Notes into page bundles.

A port of Simon Willison's `apple-notes-to-sqlite`, and a demonstration of the
claim in `docs/importers.md`: the hard part of any such tool is the fetch
layer, and it should not be welded to one output shape.

So this module does not reimplement the extraction. `apple-notes-to-sqlite`
drives AppleScript through `osascript`, streams the result, and reassembles it
into note dicts — roughly eighty lines that had to be discovered by
experiment, including the detail that `osascript` emits mac_roman. All of that
is imported and used verbatim:

    from apple_notes_to_sqlite.cli import extract_notes

Its own writing layer is a single line — `db["notes"].insert(note, pk="id")`.
This module replaces that one line with a component-tree builder and a bundle
writer, and nothing else changes. That is the entire point.

Two ways in, so the reuse is not merely theoretical:

    apple-notes-to-toadshade content/              # live, imports extract_notes
    apple-notes-to-sqlite --dump | apple-notes-to-toadshade content/ -

The second path needs nothing installed but this package: `--dump` already
emits newline-delimited JSON, so any tool that can produce the same shape can
feed this one.

Live mode requires macOS, Apple Notes, and the first run will raise a
permission prompt from the operating system. Nothing is sent anywhere; the
bundles are written to the content root you name.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as _dt
import json
import re
import sys
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Iterator

from .base import BundleDraft, Exporter

# --------------------------------------------------------------------------
# 1. Parsing a note's HTML body into components
# --------------------------------------------------------------------------

#: Apple Notes bodies are HTML: divs, headings, lists, the occasional image.
#: These are the tags worth reacting to; everything else contributes text.
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BREAKS = {"div", "p", "br", "li", "tr"}


class _NoteParser(HTMLParser):
    """Walk a note's HTML and collect a flat list of blocks.

    Deliberately forgiving. Apple Notes emits well-formed but idiosyncratic
    markup, and a note that parses oddly should still produce a usable bundle
    rather than an exception.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list = []          # ("heading"|"text"|"list"|"image", payload)
        self._buf: list = []
        self._list_items: list | None = None
        self._list_kind = "ul"
        self._checklist = False
        self._heading = None

    # -- helpers ---------------------------------------------------------

    def _flush_text(self):
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            if self._heading:
                self.blocks.append(("heading", text))
                self._heading = None
            else:
                self.blocks.append(("text", text))
        elif self._heading:
            self._heading = None

    # -- HTMLParser interface --------------------------------------------

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in HEADINGS:
            self._flush_text()
            self._heading = tag
        elif tag in ("ul", "ol"):
            self._flush_text()
            self._list_items = []
            self._list_kind = tag
            # Apple Notes marks checklists with a class on the list element.
            self._checklist = "checklist" in (attrs.get("class") or "")
        elif tag == "li":
            if self._list_items is not None:
                self._flush_li()
        elif tag == "img":
            self._flush_text()
            self.blocks.append(("image", attrs))
        elif tag in BREAKS:
            self._flush_text()

    def _flush_li(self):
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text and self._list_items is not None:
            self._list_items.append(text)

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self._flush_li()
            if self._list_items:
                self.blocks.append(
                    ("list", {
                        "items": self._list_items,
                        "ordered": self._list_kind == "ol",
                        "checklist": self._checklist,
                    })
                )
            self._list_items = None
            self._checklist = False
        elif tag in HEADINGS or tag in BREAKS:
            if self._list_items is not None and tag == "li":
                self._flush_li()
            else:
                self._flush_text()

    def handle_data(self, data):
        self._buf.append(data)

    def close(self):
        super().close()
        if self._list_items is not None:
            self._flush_li()
            if self._list_items:
                self.blocks.append(("list", {
                    "items": self._list_items,
                    "ordered": self._list_kind == "ol",
                    "checklist": self._checklist,
                }))
            self._list_items = None
        self._flush_text()


def parse_body(html: str) -> list:
    """HTML in, flat block list out. Never raises on malformed input."""
    parser = _NoteParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        # A note that defeats the parser still yields whatever came before it.
        pass
    return parser.blocks


# --------------------------------------------------------------------------
# 2. Blocks into a Toadshade component tree
# --------------------------------------------------------------------------


def blocks_to_components(blocks: list, assets: dict) -> list:
    """Group blocks under headings, producing sections with nested content.

    A note with no headings becomes one `note-text` component. A note with
    headings becomes one `section` per heading, with its lists and images in
    the section's `content` slot — which is what makes the bundle's component
    tree worth looking at rather than a single opaque blob of prose.

    `assets` is mutated: data-URI images are decoded into it, keyed by the
    bundle-relative path they will be written to.
    """
    components: list = []
    counter = {"n": 0}

    def next_id(prefix="s"):
        counter["n"] += 1
        return f"{prefix}{counter['n']}"

    current = None           # the open section, if any
    pending_text: list = []

    def target_slot():
        """Where a non-text block goes: into the open section, or top level."""
        if current is None:
            return components
        return current.setdefault("slots", {}).setdefault("content", [])

    def flush_text():
        if not pending_text:
            return
        body = "\n\n".join(pending_text)
        pending_text.clear()
        if current is not None and "body" not in current["props"]:
            current["props"]["body"] = body
        else:
            target_slot().append({
                "id": next_id("t"),
                "type": "note-text",
                "props": {"body": body},
            })

    for kind, payload in blocks:
        if kind == "heading":
            flush_text()
            current = {
                "id": next_id(),
                "type": "section",
                "props": {"heading": payload},
            }
            components.append(current)
        elif kind == "text":
            pending_text.append(payload)
        elif kind == "list":
            flush_text()
            target_slot().append({
                "id": next_id("l"),
                "type": "checklist" if payload["checklist"] else "note-list",
                "props": {
                    "items": payload["items"],
                    "ordered": payload["ordered"],
                },
            })
        elif kind == "image":
            flush_text()
            ref = _image_asset(payload, assets, next_id("img"))
            if isinstance(ref, dict):
                target_slot().append({
                    "id": next_id("i"),
                    "type": "note-image",
                    "props": {"image": ref},
                })
            elif ref:
                # Not pullable into the bundle, so it stays a plain string
                # prop rather than a broken asset reference.
                target_slot().append({
                    "id": next_id("i"),
                    "type": "note-image",
                    "props": {"image_url": ref},
                })

    flush_text()

    # A note that is nothing but prose still needs one component.
    if not components:
        components.append({
            "id": "s1",
            "type": "note-text",
            "props": {"body": ""},
        })
    return components


DATA_URI_RE = re.compile(r"^data:(?P<mime>[\w.+-]+/[\w.+-]+)?;base64,(?P<data>.*)$", re.S)

MIME_SUFFIX = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp", "image/heic": ".heic",
    "image/tiff": ".tiff",
}


def _image_asset(attrs: dict, assets: dict, stem: str):
    """Turn an <img> into an asset reference, decoding data URIs into bytes.

    Returns an asset-reference dict when the bytes can be pulled into the
    bundle, or a plain URL string when they cannot (a remote image, an
    unreadable data URI). Nothing is silently dropped — an image that cannot
    be colocated is still recorded, just as a string rather than a reference,
    so no bundle ever carries an asset reference that does not resolve.
    """
    src = (attrs.get("src") or "").strip()
    alt = (attrs.get("alt") or "").strip()
    if not src:
        return None

    match = DATA_URI_RE.match(src)
    if not match:
        return src

    try:
        blob = base64.b64decode(match.group("data"), validate=False)
    except (binascii.Error, ValueError):
        return src[:120]

    suffix = MIME_SUFFIX.get((match.group("mime") or "").lower(), ".bin")
    path = f"assets/{stem}{suffix}"
    assets[path] = blob
    return {"$asset": path, "alt": alt}


# --------------------------------------------------------------------------
# 3. Slugs and dates
# --------------------------------------------------------------------------

SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(text: str, fallback: str = "note") -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = SLUG_STRIP.sub("-", ascii_only).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or fallback)[:60].strip("-") or fallback


def note_year(note: dict) -> str:
    """The note's creation year, or 'undated' when the date is unusable."""
    raw = (note.get("created") or "").strip()
    for parser in (
        lambda s: _dt.datetime.fromisoformat(s.replace("Z", "+00:00")),
        lambda s: _dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"),
    ):
        try:
            return str(parser(raw).year)
        except (ValueError, TypeError):
            continue
    return "undated"


# --------------------------------------------------------------------------
# 4. The human layer
# --------------------------------------------------------------------------


def note_markdown(title: str, alias: str, components: list, meta: dict) -> str:
    """Write the Markdown layer so it reads like the note it came from.

    `base.markdown_for` would produce something correct but generic. An
    exporter that knows its source can do better, which is what the
    `BundleDraft.markdown` hook is for: here a bulleted list comes back as a
    bulleted list and a checklist comes back with checkboxes, because that is
    what the person wrote in Notes.
    """
    lines = [f"# {title}", "", f"*alias: `{alias}`*"]

    stamp = " · ".join(
        f"{label} {meta[key][:10]}"
        for key, label in (("created", "created"), ("updated", "updated"))
        if meta.get(key)
    )
    if stamp:
        lines.append(f"*{stamp}*")
    lines += ["", "---", ""]

    def emit(items, prefix="", depth=0):
        for i, component in enumerate(items, start=1):
            number = f"{prefix}.{i}" if prefix else str(i)
            props = component.get("props") or {}
            ctype = component.get("type", "")
            heading = props.get("heading") or props.get("title")

            level = "#" * min(depth + 2, 6)
            lines.append(f"{level} {number}. {heading or ctype}")
            lines.append("")
            lines.append(f"*id: `{component.get('id', '')}` · type: `{ctype}`*")
            lines.append("")

            if props.get("body"):
                lines.extend([props["body"], ""])

            if props.get("items"):
                ordered = props.get("ordered")
                for n, item in enumerate(props["items"], start=1):
                    if ctype == "checklist":
                        lines.append(f"- [ ] {item}")
                    elif ordered:
                        lines.append(f"{n}. {item}")
                    else:
                        lines.append(f"- {item}")
                lines.append("")

            image = props.get("image")
            if isinstance(image, dict) and image.get("$asset"):
                alt = image.get("alt", "")
                lines.extend(
                    [f'<img src="{image["$asset"]}" alt="{alt}" width="320">', ""]
                )
            elif props.get("image_url"):
                lines.extend([f'*(image not embedded: {props["image_url"]})*', ""])

            for children in (component.get("slots") or {}).values():
                emit(children, number, depth + 1)

            if depth == 0:
                lines.extend(["---", ""])

    emit(components)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 5. The exporter
# --------------------------------------------------------------------------


class AppleNotesToToadshade(Exporter):
    """One Apple Note, one bundle, filed under notes/<year>/<slug>/."""

    name = "apple-notes-to-toadshade"

    def __init__(self, source, content_root, stop_after=None):
        super().__init__(source, content_root)
        self.stop_after = stop_after
        self._slugs_seen: set = set()

    # -- fetch: borrowed wholesale ---------------------------------------

    def fetch(self) -> Iterable[dict]:
        """Yield raw note dicts.

        `self.source` is either the sentinel "live" — in which case this
        delegates to `apple_notes_to_sqlite.cli.extract_notes`, the actual
        AppleScript driver — or a path to newline-delimited JSON in the same
        shape (`-` for stdin), which is what `--dump` writes.
        """
        if self.source == "live":
            try:
                from apple_notes_to_sqlite.cli import extract_notes
            except ImportError as exc:  # pragma: no cover - environment-specific
                raise SystemExit(
                    "Live mode needs Simon Willison's extractor:\n"
                    "    pip install apple-notes-to-sqlite\n"
                    "Or pipe its output instead:\n"
                    "    apple-notes-to-sqlite --dump | apple-notes-to-toadshade OUT -"
                ) from exc
            notes = extract_notes()
        else:
            notes = _read_jsonl(self.source)

        for i, note in enumerate(notes):
            if self.stop_after and i >= self.stop_after:
                break
            yield note

    # -- write: the part that differs ------------------------------------

    def to_bundle(self, note: dict) -> BundleDraft | None:
        title = (note.get("title") or "").strip() or "Untitled note"
        year = note_year(note)

        slug = self._unique_slug(slugify(title), year)
        assets: dict = {}
        components = blocks_to_components(parse_body(note.get("body", "")), assets)

        meta = {"source": f"apple-notes:{note.get('id', '')}"}
        for key in ("created", "updated"):
            if note.get(key):
                meta[key] = note[key]

        alias = f"/notes/{year}/{slug}"
        return BundleDraft(
            slug=slug,
            title=title,
            alias=alias,
            components=components,
            assets=assets,
            meta=meta,
            markdown=note_markdown(title, alias, components, meta),
            directory=f"notes/{year}",
        )

    def _unique_slug(self, slug: str, year: str) -> str:
        """Two notes titled 'Ideas' must not become one bundle."""
        candidate, n = slug, 2
        while (year, candidate) in self._slugs_seen:
            candidate = f"{slug}-{n}"
            n += 1
        self._slugs_seen.add((year, candidate))
        return candidate


def _read_jsonl(path) -> Iterator[dict]:
    handle = sys.stdin if str(path) == "-" else open(path, encoding="utf-8")
    try:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)
    finally:
        if handle is not sys.stdin:
            handle.close()


# --------------------------------------------------------------------------
# 6. Command line
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="apple-notes-to-toadshade",
        description="Export Apple Notes to Toadshade page bundles.",
        epilog=(
            "Live mode needs macOS and will prompt for Notes access. "
            "To avoid that, pipe an existing dump: "
            "apple-notes-to-sqlite --dump | apple-notes-to-toadshade content/ -"
        ),
    )
    parser.add_argument("content_root", help="directory to write bundles into")
    parser.add_argument(
        "source", nargs="?", default="live",
        help="newline-delimited JSON from `apple-notes-to-sqlite --dump` "
             "('-' for stdin). Omit to read Apple Notes directly.",
    )
    parser.add_argument("--stop-after", type=int, help="stop after this many notes")
    parser.add_argument("--no-render", action="store_true",
                        help="skip writing the {slug}.html previews")
    args = parser.parse_args(argv)

    exporter = AppleNotesToToadshade(
        source=args.source,
        content_root=args.content_root,
        stop_after=args.stop_after,
    )

    count = 0
    for path in exporter.export(render=not args.no_render):
        print(path)
        count += 1

    print(f"\n{count} bundle(s) written to {Path(args.content_root)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

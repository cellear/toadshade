"""wordpress-to-toadshade — a WordPress export (WXR) into page bundles.

WordPress's own Tools → Export writes a WordPress eXtended RSS file: RSS 2.0
with `wp:`, `content:`, `excerpt:` and `dc:` namespaces, one `<item>` per
post, page, attachment, menu item and so on. This exporter reads that file
and writes one bundle per post, page, or custom post type item:

    wordpress-to-toadshade content/ export.xml \\
        --uploads-dir ~/Sites/mysite/wp-content/uploads --sections post=blog

The split follows `base.py`:

    WxrFile              streams the XML with iterparse, yields plain item
                         dicts, and has never heard of Toadshade.
    parse_blocks()       Gutenberg's block comment grammar, into plain dicts.
    WordPressToToadshade turns an item into a BundleDraft: the post's own
                         fields first, then one component per block.

Standard library only. The database is not read (a later phase), and neither
are comments or menus.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Iterator
from urllib.parse import unquote, urlparse
from xml.etree.ElementTree import iterparse

from .. import __version__
from .base import AliasPlacement, BundleDraft, Exporter, slugify
from .drupal import _inside, drupal_markdown, html_to_markdown, php_unserialize

# --------------------------------------------------------------------------
# 1. Reading WXR — the fetch layer, free of Toadshade
# --------------------------------------------------------------------------

#: Bytes that are not allowed in XML 1.0 but turn up in real exports.
_INVALID_XML = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class _CleanStream:
    """A binary file whose read() drops XML-invalid control characters, so one
    stray \\x0b pasted into a post does not stop the whole export."""

    def __init__(self, handle):
        self.handle = handle

    def read(self, size=-1):
        return _INVALID_XML.sub(b"", self.handle.read(size))


def _prefix(namespace: str) -> str:
    """Namespace URI to the prefix WordPress uses. WXR 1.0–1.2, http or https."""
    if "/excerpt/" in namespace:
        return "excerpt"
    if re.match(r"^https?://wordpress\.org/export/", namespace):
        return "wp"
    if "purl.org/rss/1.0/modules/content" in namespace:
        return "content"
    if "purl.org/dc/elements" in namespace:
        return "dc"
    return ""


def _tag(tag: str) -> str:
    """`{http://wordpress.org/export/1.2/}post_id` as `wp:post_id`."""
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        prefix = _prefix(namespace)
        return f"{prefix}:{local}" if prefix else local
    return tag


#: Item children renamed; every other `wp:x` becomes plain `x`.
ITEM_KEYS = {"dc:creator": "creator", "content:encoded": "content", "excerpt:encoded": "excerpt"}
INT_KEYS = ("post_id", "post_parent", "menu_order", "is_sticky")
SERIALIZED = re.compile(r'^(a:\d+:\{.*\}|O:\d+:".*\}|s:\d+:".*";|i:-?\d+;|d:[-0-9.eE]+;|b:[01];|N;)$', re.S)


def maybe_unserialize(value):
    """Postmeta values are PHP-serialized when they are arrays or objects."""
    if isinstance(value, str) and SERIALIZED.match(value.strip()):
        try:
            return php_unserialize(value.strip())
        except (ValueError, IndexError):
            pass
    return value


def _children(elem) -> dict:
    return {_tag(child.tag): (child.text or "") for child in elem}


def _item(elem) -> dict:
    record: dict = {"categories": [], "postmeta": {}, "comment_count": 0}
    for child in elem:
        tag = _tag(child.tag)
        text = child.text or ""
        if tag == "category":
            record["categories"].append({
                "domain": child.get("domain") or "category",
                "nicename": child.get("nicename") or "",
                "name": text,
            })
        elif tag == "wp:postmeta":
            pair = _children(child)
            key, value = pair.get("wp:meta_key", ""), maybe_unserialize(pair.get("wp:meta_value", ""))
            record["postmeta"].setdefault(key, []).append(value)
        elif tag == "wp:comment":
            record["comment_count"] += 1   # comments are out of scope; count only
        elif tag == "guid":
            record["guid"] = text
            record["guid_is_permalink"] = child.get("isPermaLink") != "false"
        else:
            key = ITEM_KEYS.get(tag) or (tag[3:] if tag.startswith("wp:") else tag)
            record[key] = text
    # A key WordPress stored once is a value; a repeated key keeps its list.
    record["postmeta"] = {k: v[0] if len(v) == 1 else v for k, v in record["postmeta"].items()}
    for key in INT_KEYS:
        try:
            record[key] = int(record.get(key) or 0)
        except ValueError:
            pass
    return record


class WxrFile:
    """A WordPress export file, read in a streaming pass per call.

    `items()` yields one plain dict per `<item>`. `site` is filled in with the
    channel's own data (base URLs, authors, categories, tags, terms) as the
    stream reaches it, which in WordPress's exports is before the first item.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.site: dict = {"authors": {}, "categories": {}, "tags": {}, "terms": {}}

    def items(self) -> Iterator[dict]:
        with open(self.path, "rb") as handle:
            depth_path: list = []
            channel = None
            for event, elem in iterparse(_CleanStream(handle), events=("start", "end")):
                if event == "start":
                    depth_path.append(_tag(elem.tag))
                    if depth_path[-1] == "channel" and channel is None:
                        channel = elem
                    continue
                tag = depth_path.pop()
                if not depth_path or depth_path[-1] != "channel":
                    continue
                if tag == "item":
                    yield _item(elem)
                elif tag == "wp:author":
                    author = {k[len("wp:author_"):]: v for k, v in _children(elem).items()
                              if k.startswith("wp:author_")}
                    self.site["authors"][author.get("login", "")] = author
                elif tag == "wp:category":
                    data = _children(elem)
                    self.site["categories"][data.get("wp:category_nicename", "")] = {
                        "name": data.get("wp:cat_name", ""), "parent": data.get("wp:category_parent", ""),
                    }
                elif tag == "wp:tag":
                    data = _children(elem)
                    self.site["tags"][data.get("wp:tag_slug", "")] = {"name": data.get("wp:tag_name", "")}
                elif tag == "wp:term":
                    data = _children(elem)
                    key = f"{data.get('wp:term_taxonomy', '')}/{data.get('wp:term_slug', '')}"
                    self.site["terms"][key] = {"name": data.get("wp:term_name", ""),
                                               "parent": data.get("wp:term_parent", "")}
                elif len(elem) == 0:
                    self.site[tag[3:] if tag.startswith("wp:") else tag] = elem.text or ""
                else:
                    continue
                if channel is not None:
                    try:
                        channel.remove(elem)   # keep memory flat on large exports
                    except ValueError:
                        pass


# --------------------------------------------------------------------------
# 2. Gutenberg blocks — the block comment grammar, into plain dicts
# --------------------------------------------------------------------------

#: `<!-- wp:name {json} -->`, `<!-- /wp:name -->`, `<!-- wp:name {json} /-->`.
#: WordPress escapes `--` inside attribute JSON, so the first `} -->` ends it.
BLOCK_COMMENT = re.compile(
    r"<!--\s+(?P<closer>/)?wp:(?P<namespace>[a-z][a-z0-9_-]*/)?(?P<name>[a-z][a-z0-9_-]*)"
    r"\s+(?P<attrs>\{.*?\}\s+)?(?P<void>/)?-->",
    re.S,
)


def _new_block(match) -> dict:
    namespace = (match.group("namespace") or "core/")[:-1]
    raw = (match.group("attrs") or "").strip()
    try:
        attrs = json.loads(raw) if raw else {}
    except ValueError:
        attrs = {"_unparsed_attributes": raw}
    return {"name": f"{namespace}/{match.group('name')}", "attrs": attrs,
            "inner_blocks": [], "inner_html": "", "inner_content": []}


def parse_blocks(document: str) -> list:
    """Parse post content into blocks, like WordPress's `parse_blocks()`.

    Each block is `{"name": "core/paragraph", "attrs": {...}, "inner_blocks":
    [...], "inner_html": "...", "inner_content": [str | None]}`, where a None
    in `inner_content` marks where an inner block sits. HTML outside any
    block comes back as a block whose name is None (classic content).
    """
    output: list = []
    stack: list = []
    pos = 0

    def add_text(text):
        if not text:
            return
        if stack:
            stack[-1]["inner_html"] += text
            stack[-1]["inner_content"].append(text)
        else:
            output.append({"name": None, "attrs": {}, "inner_blocks": [],
                           "inner_html": text, "inner_content": [text]})

    def attach(block):
        if stack:
            stack[-1]["inner_blocks"].append(block)
            stack[-1]["inner_content"].append(None)
        else:
            output.append(block)

    for match in BLOCK_COMMENT.finditer(document or ""):
        add_text(document[pos:match.start()])
        pos = match.end()
        name = f"{(match.group('namespace') or 'core/')[:-1]}/{match.group('name')}"
        if match.group("closer"):
            if any(block["name"] == name for block in stack):
                while stack:
                    block = stack.pop()
                    attach(block)
                    if block["name"] == name:
                        break
            continue  # a stray closer is ignored, as WordPress does
        block = _new_block(match)
        if match.group("void"):
            attach(block)
        else:
            stack.append(block)
    add_text(document[pos:] if document else "")
    while stack:          # unclosed blocks are closed at the end
        attach(stack.pop())
    return output


def autop(text: str) -> str:
    """A small `wpautop()`: classic content's blank lines become paragraphs
    and single newlines become <br>, as WordPress does when it displays it."""
    text = (text or "").replace("\r\n", "\n").strip()
    out = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if re.match(r"<(p|div|h[1-6]|ul|ol|blockquote|pre|table|figure|hr|address|section|dl)\b", chunk, re.I):
            out.append(chunk)
        else:
            out.append("<p>" + chunk.replace("\n", "<br>\n") + "</p>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# 3. Items into bundles
# --------------------------------------------------------------------------

#: WordPress's internal post types: never pages of their own.
INTERNAL_TYPES = {
    "attachment", "revision", "nav_menu_item", "custom_css", "customize_changeset",
    "oembed_cache", "user_request", "wp_block", "wp_template", "wp_template_part",
    "wp_global_styles", "wp_navigation", "wp_font_family", "wp_font_face",
}
#: Statuses that are not content: an editor's never-saved draft, and the bin.
SKIPPED_STATUSES = {"auto-draft", "trash"}
#: Where WordPress puts term archives by default. WXR does not record a site's
#: custom category or tag base, so these are an assumption (see the docs).
TERM_BASES = {"category": "/category", "post_tag": "/tag"}
UPLOADS = "/wp-content/uploads/"
SIZE_SUFFIX = re.compile(r"-(\d+x\d+|scaled|rotated)(?=\.[A-Za-z0-9]+$)")
IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
ATTR = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("([^"]*)"|\'([^\']*)\')')
CAPTION = re.compile(r"<figcaption\b[^>]*>(.*?)</figcaption>", re.I | re.S)


def _attrs(tag: str) -> dict:
    return {m.group(1).lower(): html.unescape(m.group(3) if m.group(3) is not None else m.group(4))
            for m in ATTR.finditer(tag)}


def _plain_text(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup or "")).strip()


def _wp_date(value: str):
    """'2026-03-14 09:00:00' as ISO 8601; the all-zero date as None."""
    if not value or value.startswith("0000-00-00"):
        return None
    return value.strip().replace(" ", "T")


def humanize(name: str) -> str:
    local = name.split("/")[-1].replace("-", " ").replace("_", " ")
    return local[:1].upper() + local[1:]


def block_type(name: str) -> str:
    """`core/paragraph` as `wp-paragraph`; `jetpack/markdown` as `wp-jetpack/markdown`."""
    namespace, _, local = name.partition("/")
    return f"wp-{local}" if namespace == "core" else f"wp-{name}"


class _WordPressBundleBuilder:
    """Builds one item's component list, collecting its asset bytes."""

    def __init__(self, exporter: "WordPressToToadshade"):
        self.exporter = exporter
        self.count = 0
        self.assets: dict = {}
        self._by_source: dict = {}

    def next_id(self) -> str:
        self.count += 1
        return f"s{self.count}"

    # -- the post's own fields --------------------------------------------

    def entry(self, item: dict) -> dict:
        props: dict = {"title": item.get("title") or ""}
        if (item.get("excerpt") or "").strip():
            props["excerpt"] = item["excerpt"].strip()

        terms: dict = {}
        for term in item.get("categories", []):
            label = term["name"] or term["nicename"]
            url = self.exporter.term_url(term["domain"], term["nicename"])
            key = {"category": "categories", "post_tag": "tags"}.get(term["domain"], term["domain"])
            terms.setdefault(key, []).append(f"{label} → {url}" if url else label)
        props.update(terms)

        thumbnail = (item.get("postmeta") or {}).get("_thumbnail_id")
        if thumbnail:
            attachment = self.exporter.index()["attachments"].get(_int(thumbnail))
            path = self.exporter.local_path_for_attachment(attachment) if attachment else None
            asset = self.asset(path)
            if asset:
                props["featured_image"] = {
                    "$asset": asset,
                    "alt": attachment.get("alt") or attachment.get("title") or "",
                }
            else:
                props["featured_image_url"] = (attachment or {}).get("url") or f"attachment:{thumbnail}"
        return {"id": self.next_id(), "type": slugify(item.get("post_type") or "post", "post"),
                "props": props}

    # -- content ----------------------------------------------------------

    def content(self, item: dict) -> list:
        blocks = parse_blocks(item.get("content") or "")
        components = []
        for block in blocks:
            component = self.block(block)
            if component is not None:
                components.append(component)
        return components

    def block(self, block: dict, depth: int = 0):
        if block["name"] is None:
            if not block["inner_html"].strip():
                return None     # the whitespace between blocks
            return self.text(autop(block["inner_html"]))

        name = block["name"]
        component = {"id": self.next_id(), "type": block_type(name), "label": humanize(name)}
        props = dict(block["attrs"])
        slots: dict = {}
        inner_html = "".join(part for part in block["inner_content"] if part is not None).strip()

        if name == "core/image":
            self.image_block(props, inner_html)
        elif inner_html:
            images: list = []
            markdown = html_to_markdown(
                inner_html, image=lambda src, alt: self.inline_image(src, alt, images))
            if markdown.strip():
                props["body"] = markdown
                props["body_html"] = inner_html
            if images:
                slots["images"] = images

        inner = block["inner_blocks"]
        if name == "core/block" and props.get("ref") and depth < 10:
            # A reusable block: its content lives in a wp_block item.
            inner = parse_blocks(self.exporter.index()["reusable"].get(_int(props["ref"]), ""))
        children = [c for c in (self.block(b, depth + 1) for b in inner) if c is not None]
        if children:
            slots["inner_blocks"] = children

        component["props"] = props
        if slots:
            component["slots"] = slots
        return component

    def text(self, body_html: str) -> dict:
        component = {"id": self.next_id(), "type": "text", "label": "Classic content"}
        images: list = []
        component["props"] = {
            "body": html_to_markdown(body_html, image=lambda src, alt: self.inline_image(src, alt, images)),
            "body_html": body_html,
        }
        if images:
            component["slots"] = {"images": images}
        return component

    def image_block(self, props: dict, inner_html: str) -> None:
        img = _attrs((IMG_TAG.search(inner_html) or [""])[0]) if IMG_TAG.search(inner_html) else {}
        caption = _plain_text((CAPTION.search(inner_html) or [None, ""])[1]) if CAPTION.search(inner_html) else ""
        attachment_id = props.get("id")
        if not attachment_id:
            match = re.search(r"\bwp-image-(\d+)\b", img.get("class", ""))
            attachment_id = int(match.group(1)) if match else None
        attachment = self.exporter.index()["attachments"].get(_int(attachment_id)) if attachment_id else None

        path = self.exporter.local_path_for_attachment(attachment) if attachment else None
        if path is None and img.get("src"):
            path = self.exporter.local_path_for_url(img["src"])
        alt = img.get("alt") or (attachment or {}).get("alt") or ""
        asset = self.asset(path)
        if asset:
            ref = {"$asset": asset, "alt": alt}
            if caption:
                ref["title"] = caption
            props["image"] = ref
        else:
            props["image_url"] = img.get("src") or (attachment or {}).get("url") or f"attachment:{attachment_id}"
            if alt:
                props["alt"] = alt
            if caption:
                props["caption"] = caption

    def inline_image(self, src: str, alt: str, sink: list) -> str:
        """An <img> in block or classic HTML: copied in when it is an upload."""
        attachment = self.exporter.index()["attachments_by_url"].get(SIZE_SUFFIX.sub("", src.split("?")[0]))
        path = self.exporter.local_path_for_attachment(attachment) if attachment else None
        asset = self.asset(path or self.exporter.local_path_for_url(src))
        if not asset:
            return src
        sink.append({"id": self.next_id(), "type": "image",
                     "props": {"image": {"$asset": asset, "alt": alt or (attachment or {}).get("alt", "")}}})
        return asset

    def asset(self, path):
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


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class WordPressToToadshade(Exporter):
    """One post, page, or custom post type item, one bundle, filed by permalink."""

    name = "wordpress-to-toadshade"

    def __init__(self, source, content_root, uploads_dir=None, post_types=None,
                 sections=None, stop_after=None):
        super().__init__(source, content_root)
        self.wxr = source if isinstance(source, WxrFile) else WxrFile(source)
        self.uploads_dir = Path(uploads_dir) if uploads_dir else None
        self.post_types = set(post_types) if post_types else None
        self.sections = dict(sections or {})
        self.stop_after = stop_after
        self._index = None
        self._placement = None

    # -- fetch ------------------------------------------------------------

    def wanted(self, item: dict) -> bool:
        post_type = item.get("post_type") or ""
        if post_type in INTERNAL_TYPES or item.get("status") in SKIPPED_STATUSES:
            return False
        return not self.post_types or post_type in self.post_types

    def fetch(self) -> Iterator[dict]:
        count = 0
        for item in self.wxr.items():
            if not self.wanted(item):
                continue
            if self.stop_after and count >= self.stop_after:
                return
            count += 1
            yield item

    def index(self) -> dict:
        """One pass over the file for what pages refer to: attachments,
        reusable blocks, and every item's permalink and parent."""
        if self._index is None:
            index = {"attachments": {}, "attachments_by_url": {}, "reusable": {}, "items": {}}
            for item in self.wxr.items():
                post_id, post_type = item.get("post_id"), item.get("post_type")
                meta = item.get("postmeta") or {}
                if post_type == "attachment":
                    attachment = {
                        "id": post_id, "url": item.get("attachment_url") or item.get("guid") or "",
                        "file": meta.get("_wp_attached_file") or "",
                        "alt": meta.get("_wp_attachment_image_alt") or "",
                        "title": item.get("title") or "",
                    }
                    index["attachments"][post_id] = attachment
                    if attachment["url"]:
                        index["attachments_by_url"][SIZE_SUFFIX.sub("", attachment["url"])] = attachment
                elif post_type == "wp_block":
                    index["reusable"][post_id] = item.get("content") or ""
                index["items"][post_id] = {
                    "post_type": post_type, "link": item.get("link") or "",
                    "post_name": item.get("post_name") or "", "post_parent": item.get("post_parent") or 0,
                    "wanted": self.wanted(item),
                }
            self._index = index
        return self._index

    # -- write ------------------------------------------------------------

    def to_bundle(self, item: dict) -> BundleDraft | None:
        builder = _WordPressBundleBuilder(self)
        components = [builder.entry(item)] + builder.content(item)

        alias = self.alias_for(item)
        post_type = item.get("post_type") or "post"
        directory, slug = self.placement.place(unquote(alias), "", post_type)

        site = self.wxr.site
        author = site["authors"].get(item.get("creator") or "", {})
        postmeta = {k: v for k, v in (item.get("postmeta") or {}).items() if not k.startswith("_")}
        meta = {
            "source": f"wordpress:{item.get('post_id')}",
            "generator": f"{self.name} {__version__}",
            "site": site.get("base_blog_url") or site.get("base_site_url") or site.get("link"),
            "post_id": item.get("post_id"),
            "post_type": post_type,
            "status": item.get("status"),
            "author": item.get("creator"),
            "author_name": author.get("display_name"),
            "date": _wp_date(item.get("post_date_gmt")) and _wp_date(item["post_date_gmt"]) + "+00:00"
                    or _wp_date(item.get("post_date")),
            "modified": _wp_date(item.get("post_modified_gmt")) and _wp_date(item["post_modified_gmt"]) + "+00:00"
                        or _wp_date(item.get("post_modified")),
            "guid": item.get("guid"),
            "link": item.get("link"),
            "post_name": item.get("post_name") or None,
            "post_parent": item.get("post_parent") or None,
            "menu_order": item.get("menu_order") or None,
            "comment_status": item.get("comment_status"),
            "ping_status": item.get("ping_status"),
            "sticky": bool(item.get("is_sticky")) or None,
            "password": item.get("post_password") or None,
            "comment_count": item.get("comment_count") or None,
            "postmeta": postmeta or None,
        }
        meta = {k: v for k, v in meta.items() if v not in (None, "")}

        draft = BundleDraft(
            slug=slug,
            title=item.get("title") or f"{post_type} {item.get('post_id')}",
            alias=alias,
            components=components,
            assets=builder.assets,
            meta=meta,
            directory=directory,
        )
        draft.markdown = drupal_markdown(draft)
        return draft

    def alias_for(self, item: dict) -> str:
        """The permalink's path, relative to the blog's own base path.

        A draft or a site with plain permalinks has `?p=22` for a link; then
        the path is built from `post_name` and its parent pages, or failing
        that `/{post_type}/{id}`.
        """
        link = urlparse(item.get("link") or "")
        path = link.path
        site = self.wxr.site
        base = urlparse(site.get("base_blog_url") or site.get("base_site_url") or "").path.rstrip("/")
        if base and (path == base or path.startswith(base + "/")):
            path = path[len(base):]
        path = path.strip("/")
        if path:
            return "/" + path.replace(" ", "%20")
        if not link.query:
            return "/"
        # Plain permalink: rebuild `/parent/child` from post names, but only if
        # every page in the chain has a name (drafts often do not).
        items = self.index()["items"]
        names, current, seen = [], item, set()
        while current is not None:
            if not current.get("post_name") or current.get("post_id") in seen:
                names = []
                break
            seen.add(current.get("post_id"))
            names.insert(0, current["post_name"])
            parent_id = current.get("post_parent") or 0
            current = {**items[parent_id], "post_id": parent_id} if parent_id in items else None
        if names:
            return "/" + "/".join(names)
        return f"/{slugify(item.get('post_type') or 'post', 'post')}/{item.get('post_id')}"

    @property
    def placement(self) -> AliasPlacement:
        if self._placement is None:
            def paths():
                items = self.index()["items"]
                wanted = [{"post_id": i, **summary} for i, summary in items.items() if summary["wanted"]]
                aliases = [unquote(self.alias_for(summary)) for summary in wanted]
                return aliases + [f"/{slugify(s['post_type'] or 'post', 'post')}/0" for s in wanted]
            self._placement = AliasPlacement(paths, self.sections)
        return self._placement

    def term_url(self, taxonomy: str, slug: str):
        """Default WordPress archive URL for a category or tag, else None."""
        base = TERM_BASES.get(taxonomy)
        if not base or not slug:
            return None
        parts, seen = [slug], {slug}
        if taxonomy == "category":
            parent = (self.wxr.site["categories"].get(slug) or {}).get("parent")
            while parent and parent not in seen:
                seen.add(parent)
                parts.insert(0, parent)
                parent = (self.wxr.site["categories"].get(parent) or {}).get("parent")
        return base + "/" + "/".join(parts)

    # -- files ------------------------------------------------------------

    def local_path_for_attachment(self, attachment):
        if not attachment:
            return None
        if attachment.get("file"):
            found = _inside(self.uploads_dir, attachment["file"])
            if found:
                return found
        return self.local_path_for_url(attachment.get("url") or "")

    def local_path_for_url(self, src: str):
        """`…/wp-content/uploads/2026/03/x-1024x768.png` to the file on disk,
        falling back to the original when the resized copy is not there."""
        path = unquote(urlparse(src or "").path)
        at = path.find(UPLOADS)
        if at < 0 or self.uploads_dir is None:
            return None
        relative = path[at + len(UPLOADS):]
        return _inside(self.uploads_dir, relative) or _inside(self.uploads_dir, SIZE_SUFFIX.sub("", relative))


# --------------------------------------------------------------------------
# 4. Command line
# --------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="wordpress-to-toadshade",
        description="Export a WordPress WXR file (Tools → Export) to Toadshade page bundles.",
    )
    parser.add_argument("content_root", help="directory to write bundles into")
    parser.add_argument("wxr_file", help="the WordPress export (.xml)")
    parser.add_argument("--uploads-dir", help="the site's wp-content/uploads directory")
    parser.add_argument("--sections",
                        help="post_type=folder pairs for items whose permalink has no hierarchy, "
                             "e.g. post=blog")
    parser.add_argument("--post-types", help="comma-separated post types to include (default: all "
                                             "except WordPress's internal types)")
    parser.add_argument("--stop-after", type=int, help="stop after this many items")
    parser.add_argument("--no-render", action="store_true",
                        help="skip writing the {slug}.html previews")
    args = parser.parse_args(argv)

    if not Path(args.wxr_file).is_file():
        parser.error(f"{args.wxr_file} is not a file")

    exporter = WordPressToToadshade(
        source=args.wxr_file,
        content_root=args.content_root,
        uploads_dir=args.uploads_dir,
        post_types=[t.strip() for t in args.post_types.split(",") if t.strip()] if args.post_types else None,
        sections=dict(
            pair.strip().split("=", 1) for pair in (args.sections or "").split(",") if "=" in pair
        ),
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

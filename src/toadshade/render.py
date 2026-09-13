"""Turn a Toadshade bundle into one double-clickable HTML page.

The whole renderer is this file, it uses nothing but the standard library, and
it reads top to bottom:

    1. load the bundle's JSON
    2. walk the component tree, emitting a <section> per component
    3. wrap the result in a page with inlined CSS
    4. write {slug}.html next to the JSON

The output looks like a plain web page — a title, images where they fall,
formatted text, a short list of details — in a generic style rather than any
site's theme (SPEC section 5). A "Show structure" switch, pure CSS, outlines
every component with its outline number, type and id, so a reviewer can still
say "section 2.1 has a typo". There is no template engine, no Markdown parser,
no script and no build step: the file opens from file:// with its images.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import re
from html.parser import HTMLParser
from pathlib import Path

# --------------------------------------------------------------------------
# 1. Little helpers
# --------------------------------------------------------------------------


def esc(value) -> str:
    """Escape anything for safe placement in HTML text."""
    return html.escape(str(value), quote=True)


def is_asset(value) -> bool:
    """An asset reference is a dict with a "$asset" key. See SPEC section 3."""
    return isinstance(value, dict) and "$asset" in value


def looks_like_image(path: str) -> bool:
    return Path(path).suffix.lower() in {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif",
    }


def is_url(value) -> bool:
    return isinstance(value, str) and re.match(r"^https?://\S+$", value) is not None


ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")


def as_moment(value):
    """An ISO 8601 date or datetime string as a datetime, else None."""
    if not isinstance(value, str) or not ISO_DATE.match(value):
        return None
    try:
        return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_time(moment) -> str:
    text = f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"
    return text + " UTC" if moment.utcoffset() == _dt.timedelta(0) else text


def format_date(value):
    """'2026-02-20T02:00:00+00:00' as 'Feb 20, 2026, 2:00 AM UTC', else None."""
    moment = as_moment(value)
    if moment is None:
        return None
    text = f"{moment:%b} {moment.day}, {moment.year}"
    return f"{text}, {format_time(moment)}" if "T" in value else text


def format_range(value):
    """A {"value": start, "end_value": end} date range as one line, else None."""
    if not isinstance(value, dict):
        return None
    start, end = as_moment(value.get("value")), as_moment(value.get("end_value"))
    if start is None:
        return None
    if end is None or end == start:
        return format_date(value["value"])
    if end.date() == start.date() and "T" in value["end_value"]:
        return f"{format_date(value['value']).removesuffix(' UTC')} – {format_time(end)}"
    return f"{format_date(value['value'])} – {format_date(value['end_value'])}"


#: "See all trails → /visit/trail-guide/all" — SPEC section 4's link shape.
LINK_TEXT = re.compile(r"^(?P<label>.+?)\s+→\s+(?P<url>\S+)$")


def link(url: str, label: str) -> str:
    return f'<a href="{esc(url)}">{esc(label)}</a>'


# --------------------------------------------------------------------------
# 2. Sanitizing a body_html prop
# --------------------------------------------------------------------------

#: Text markup worth keeping. Anything else is unwrapped: its text stays.
SAFE_TAGS = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "em",
    "i", "u", "s", "del", "sub", "sup", "small", "mark", "code", "pre", "kbd",
    "blockquote", "q", "cite", "ul", "ol", "li", "dl", "dt", "dd", "a", "img",
    "figure", "figcaption", "table", "thead", "tbody", "tfoot", "tr", "th",
    "td", "caption", "div", "span", "section", "article", "aside",
}
#: Dropped together with everything inside them.
DROPPED_TAGS = {
    "script", "style", "iframe", "object", "embed", "form", "button", "select",
    "textarea", "noscript", "template", "svg", "math", "head", "title", "video",
    "audio",
}
VOID_TAGS = {"br", "hr", "img", "input", "link", "meta", "base", "source", "wbr"}
SAFE_ATTRS = {
    "a": {"href", "title"}, "img": {"src", "alt", "title"},
    "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}, "ol": {"start"},
}


class _Sanitizer(HTMLParser):
    """Rebuild HTML from an allow-list. Links stay links; nothing is loaded
    from outside the bundle, so an image survives only if it is in assets/."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self.open: list = []
        self.dropping = 0

    def handle_starttag(self, tag, attrs):
        if tag in DROPPED_TAGS:
            self.dropping += 1
            return
        if self.dropping or tag not in SAFE_TAGS:
            return
        kept = []
        for name, value in attrs:
            value = value or ""
            if name not in SAFE_ATTRS.get(tag, ()):
                continue
            if name == "href" and re.match(r"^\s*[a-z][a-z0-9+.-]*:", value, re.I) \
                    and not re.match(r"^\s*(https?|mailto):", value, re.I):
                continue  # javascript: and friends
            kept.append(f' {name}="{esc(value)}"')
        if tag == "img":
            src = dict(attrs).get("src") or ""
            if not src.startswith("assets/") or ".." in src:
                return
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in VOID_TAGS:
            self.open.append(tag)

    def handle_endtag(self, tag):
        if tag in DROPPED_TAGS:
            self.dropping = max(0, self.dropping - 1)
            return
        if self.dropping or tag not in self.open:
            return
        while self.open:
            closing = self.open.pop()
            self.out.append(f"</{closing}>")
            if closing == tag:
                break

    def handle_data(self, data):
        if not self.dropping:
            self.out.append(esc(data))


def sanitize_html(markup: str) -> str:
    parser = _Sanitizer()
    try:
        parser.feed(markup or "")
        parser.close()
    except Exception:
        pass  # keep whatever was rebuilt before the parser gave up
    return "".join(parser.out) + "".join(f"</{t}>" for t in reversed(parser.open))


# --------------------------------------------------------------------------
# 3. Rendering one prop
# --------------------------------------------------------------------------

# Props shown as the section's own heading / body rather than as details.
HEADING_PROPS = ("heading", "title")
BODY_PROPS = ("body", "text", "description")


def render_asset(value: dict) -> str:
    """An image becomes a <figure>; anything else becomes a download link."""
    src = esc(value["$asset"])
    alt = esc(value.get("alt", ""))
    caption = value.get("title") or ""

    if looks_like_image(value["$asset"]):
        figure = f'<img src="{src}" alt="{alt}">'
        if caption:
            figure += f"<figcaption>{esc(caption)}</figcaption>"
        return f"<figure>{figure}</figure>"

    label = esc(caption or value.get("alt") or Path(value["$asset"]).name)
    return f'<a class="file" href="{src}">{label}</a>'


def is_simple_list(value) -> bool:
    """A list of scalars — the shape a reviewer expects to see as a list."""
    return isinstance(value, list) and value and all(
        isinstance(v, (str, int, float, bool)) for v in value
    )


def render_value(value, component_type: str = "") -> str:
    if is_asset(value):
        return render_asset(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    when = format_date(value) or format_range(value)
    if when:
        return esc(when)
    if is_simple_list(value):
        # Show a list as a list. A reviewer checking copy should not have to
        # read JSON to find out what the bullet points say.
        box = '<span class="box"></span>' if component_type == "checklist" else ""
        items = "".join(f"<li>{box}{render_value(v)}</li>" for v in value)
        return f'<ul class="items">{items}</ul>'
    if isinstance(value, (list, dict)):
        return f"<code>{esc(json.dumps(value, ensure_ascii=False))}</code>"
    if isinstance(value, str):
        match = LINK_TEXT.match(value)
        if match:
            return link(match["url"], match["label"])
        if is_url(value):
            return link(value, value)
    return esc(value)


def pretty(name: str) -> str:
    """`field_event_mode` as 'Event mode'."""
    words = name.removeprefix("field_").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


# --------------------------------------------------------------------------
# 4. Rendering one component, and its children
# --------------------------------------------------------------------------


def render_component(component: dict, number: str, depth: int = 0, page_title=None) -> str:
    """Emit one <section>. Recurses into slots for nested components.

    `number` is the reviewer-facing outline number: "2", then "2.1", "2.1.1".
    It is shown with the type and id when the reader switches on structure.
    """
    props = dict(component.get("props") or {})
    ctype = component.get("type", "")

    # Heading and body read as a page's heading and text, not as details.
    heading = next((props.pop(p) for p in HEADING_PROPS if p in props), None)
    body = next((props.pop(p) for p in BODY_PROPS if p in props), None)
    body_html = props.pop("body_html", None)
    props.pop("format", None)  # names body_html's text format; not content

    images = [props.pop(k) for k, v in list(props.items())
              if is_asset(v) and looks_like_image(v["$asset"])]
    if images:
        for key in ("width", "height"):  # the image's pixel size, not copy
            if isinstance(props.get(key), (int, float)):
                props.pop(key)
    video = next((v for k, v in props.items() if "video" in k.lower() and is_url(v)), None)

    level = min(depth + 2, 6)  # <h1> is the page title
    out = [f'<section class="component depth-{min(depth, 3)}">']
    out.append(
        '<p class="meta">'
        f'<span class="num">{esc(number)}</span>'
        f'<code class="type">{esc(ctype or "?")}</code>'
        f'<code class="id">{esc(component.get("id", ""))}</code>'
        + (f'<span class="label">{esc(component["label"])}</span>'
           if component.get("label") else "")
        + "</p>"
    )

    # A media item named after the page, say, would print the title twice.
    repeats_title = str(heading).strip() == str(page_title).strip()
    if heading is not None and not repeats_title:
        out.append(f"<h{level}>{esc(heading)}</h{level}>")

    for i, image in enumerate(images):
        figure = render_asset(image)
        if video and i == 0:
            figure = (f'<a class="video" href="{esc(video)}">{figure}'
                      '<span class="play" aria-hidden="true">▶</span></a>')
        out.append(figure)

    if body_html:
        out.append(f'<div class="prose">{sanitize_html(body_html)}</div>')
    elif body is not None:
        # Blank lines separate paragraphs; that is the only formatting a
        # plain body prop is allowed to carry.
        paragraphs = [p.strip() for p in str(body).split("\n\n") if p.strip()]
        out.append('<div class="prose">'
                   + "".join(f"<p>{esc(p)}</p>" for p in paragraphs) + "</div>")

    if props:
        rows = "".join(
            f"<dt>{esc(pretty(k))}</dt><dd>{render_value(v, ctype)}</dd>"
            for k, v in props.items()
        )
        out.append(f'<dl class="details">{rows}</dl>')

    # Slots: named lists of child components, rendered in place.
    for slot_name, children in (component.get("slots") or {}).items():
        out.append(f'<div class="slot"><p class="slot-name">{esc(slot_name)}</p>')
        for i, child in enumerate(children, start=1):
            out.append(render_component(child, f"{number}.{i}", depth + 1, page_title))
        out.append("</div>")

    out.append("</section>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# 5. The page wrapper
# --------------------------------------------------------------------------

CSS = """
:root { color-scheme: light; --ink: #1d2327; --muted: #646970; --line: #e2e4e7;
  --accent: #2c5f2d; --wash: #f6f7f7; }
* { box-sizing: border-box; }
body { margin: 0; font: 17px/1.65 system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
  color: var(--ink); background: #fff; }

.structure-toggle { position: absolute; opacity: 0; pointer-events: none; }
.topbar { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap;
  padding: .45rem 1.25rem; background: var(--wash); border-bottom: 1px solid var(--line);
  font-size: .78rem; color: var(--muted); }
.topbar .brand { font-weight: 600; color: var(--accent); }
.topbar .alias { font-family: ui-monospace, Menlo, monospace; }
.topbar label { margin-left: auto; cursor: pointer; user-select: none;
  border: 1px solid #c3c4c7; border-radius: 999px; padding: .1rem .7rem; background: #fff; }
.topbar label::before { content: "○ "; }
.structure-toggle:checked + .topbar label { background: var(--accent); color: #fff; border-color: var(--accent); }
.structure-toggle:checked + .topbar label::before { content: "● "; }
.structure-toggle:focus-visible + .topbar label { outline: 2px solid var(--accent); }

main { max-width: 46rem; margin: 0 auto; padding: 2.5rem 1.25rem 4rem; }
header.page h1 { font-size: 2.4rem; line-height: 1.15; margin: 0 0 1.5rem; letter-spacing: -.01em; }
h2, h3, h4, h5, h6 { line-height: 1.25; margin: 1.6rem 0 .6rem; }
h2 { font-size: 1.6rem; } h3 { font-size: 1.3rem; } h4, h5, h6 { font-size: 1.1rem; }

.component { margin: 0 0 1.25rem; }
.prose p { margin: 0 0 1rem; }
.prose a, .details a { color: var(--accent); }
.prose ul, .prose ol { padding-left: 1.4rem; margin: 0 0 1rem; }
.prose blockquote { margin: 1rem 0; padding: .1rem 1rem; border-left: 3px solid var(--line); color: var(--muted); }
.prose pre { background: var(--wash); padding: .8rem 1rem; overflow-x: auto; font-size: .85rem; border-radius: 6px; }
.prose code { font-size: .88em; }
.prose table { border-collapse: collapse; margin: 1rem 0; }
.prose th, .prose td { border: 1px solid var(--line); padding: .3rem .6rem; }

figure { margin: 1.25rem 0; }
figure img { display: block; max-width: 100%; height: auto; border-radius: 6px; }
.slot figure img { max-height: 24rem; width: auto; }
figcaption { font-size: .85rem; color: var(--muted); margin-top: .4rem; }
a.video { position: relative; display: block; width: fit-content; max-width: 100%; }
a.video .play { position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
  width: 4rem; height: 4rem; border-radius: 50%; background: rgba(0,0,0,.65); color: #fff;
  display: grid; place-items: center; font-size: 1.6rem; padding-left: .25rem; }
a.file::before { content: "⬇ "; }

.details { display: grid; grid-template-columns: max-content 1fr; gap: .25rem 1rem;
  margin: 1rem 0; padding: .75rem 1rem; background: var(--wash); border-radius: 6px; font-size: .9rem; }
.details dt { color: var(--muted); }
.details dd { margin: 0; }
.details code { font-size: .8rem; word-break: break-all; }
ul.items { margin: 0; padding-left: 1.1rem; }
.box { display: inline-block; width: .72em; height: .72em; margin-right: .45em;
  border: 1.5px solid #8c8f94; border-radius: 2px; }

/* Structure: hidden until the reader asks for it. */
.meta, .slot-name { display: none; }
.structure-toggle:checked ~ main .meta { display: flex; gap: .45rem; align-items: baseline;
  flex-wrap: wrap; margin: 0 0 .3rem; font: .72rem/1.4 ui-monospace, Menlo, monospace; color: var(--muted); }
.structure-toggle:checked ~ main .meta .num { font-weight: 700; color: var(--accent); }
.structure-toggle:checked ~ main .meta code { background: #edf2ea; padding: 0 .3rem; border-radius: 3px; }
.structure-toggle:checked ~ main .component { outline: 1px dashed #b8c4b3; outline-offset: .5rem; margin-bottom: 2rem; }
.structure-toggle:checked ~ main .slot { margin-left: 1rem; }
.structure-toggle:checked ~ main .slot-name { display: block; margin: 0 0 .5rem;
  font: .68rem ui-monospace, Menlo, monospace; letter-spacing: .06em; text-transform: uppercase; color: #8c8f94; }

footer.page { max-width: 46rem; margin: 0 auto; padding: 1rem 1.25rem 3rem;
  border-top: 1px solid var(--line); font-size: .75rem; color: var(--muted); }

@media print {
  .topbar { display: none; }
  main { padding-top: 0; }
  .component { page-break-inside: avoid; }
}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Toadshade preview</title>
<style>{css}</style>
</head>
<body>
<input type="checkbox" id="structure" class="structure-toggle">
<div class="topbar">
  <span class="brand">Toadshade preview</span>
  <code class="alias">{alias}</code>
  <label for="structure">Show structure</label>
</div>
<main>
<header class="page"><h1>{title}</h1></header>
{components}
</main>
<footer class="page">Generated from {source} · bundle <code>{slug}</code> · {count} components. A preview, not the live site: edits belong in the bundle.</footer>
</body>
</html>
"""


def render_html(bundle: dict, source_name: str = "the bundle") -> str:
    """Turn a loaded bundle dict into a complete HTML document."""
    title = bundle.get("title", bundle.get("slug", "Untitled"))
    components = bundle.get("components") or []
    body = "\n".join(
        render_component(c, str(i), 0, title) for i, c in enumerate(components, start=1)
    )

    def total(items) -> int:
        return sum(
            1 + total(child for s in (c.get("slots") or {}).values() for child in s)
            for c in items
        )
    return PAGE.format(
        title=esc(title),
        alias=esc(bundle.get("alias", "")),
        slug=esc(bundle.get("slug", "")),
        source=esc(source_name),
        css=CSS,
        components=body,
        count=total(components),
    )


# --------------------------------------------------------------------------
# 6. The bit that touches the disk
# --------------------------------------------------------------------------


def render_bundle(bundle_dir: Path) -> Path:
    """Read {slug}.json in `bundle_dir`, write {slug}.html beside it."""
    bundle_dir = Path(bundle_dir)
    slug = bundle_dir.name
    json_path = bundle_dir / f"{slug}.json"
    if not json_path.exists():
        raise FileNotFoundError(f"no {slug}.json in {bundle_dir}")

    bundle = json.loads(json_path.read_text(encoding="utf-8"))
    out_path = bundle_dir / f"{slug}.html"
    out_path.write_text(render_html(bundle, json_path.name), encoding="utf-8")
    return out_path


if __name__ == "__main__":  # `python render.py path/to/bundle`
    import sys

    for target in sys.argv[1:] or ["."]:
        print(render_bundle(Path(target)))

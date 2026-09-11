"""Turn a Toadshade bundle into one double-clickable HTML file.

The whole renderer is this file, it uses nothing but the standard library, and
it reads top to bottom:

    1. load the bundle's JSON
    2. walk the component tree, emitting a <section> per component
    3. wrap the result in a page with inlined CSS
    4. write {slug}.html next to the JSON

That is the entire program. There is no template engine, no Markdown parser,
and no build step, because the output has exactly one job: open from file://
and show a reviewer the page's content, with its images, at a readable size.
"""

from __future__ import annotations

import html
import json
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


# --------------------------------------------------------------------------
# 2. Rendering one prop
# --------------------------------------------------------------------------

# Props shown as the section's own heading / body rather than in the prop list.
HEADING_PROPS = ("heading", "title")
BODY_PROPS = ("body", "text", "description")


def render_asset(value: dict) -> str:
    """An image becomes an <img>; anything else becomes a download link."""
    src = esc(value["$asset"])
    alt = esc(value.get("alt", ""))
    caption = value.get("title") or value.get("alt") or ""

    if looks_like_image(value["$asset"]):
        figure = f'<img src="{src}" alt="{alt}">'
        if caption:
            figure += f"<figcaption>{esc(caption)}</figcaption>"
        return f"<figure>{figure}</figure>"

    label = esc(caption or value["$asset"])
    return f'<p class="file"><a href="{src}">{label}</a></p>'


def is_simple_list(value) -> bool:
    """A list of scalars — the shape a reviewer expects to see as a list."""
    return isinstance(value, list) and value and all(
        isinstance(v, (str, int, float, bool)) for v in value
    )


def render_prop(name: str, value, component_type: str = "") -> str:
    """One row in a component's property list."""
    if is_asset(value):
        rendered = render_asset(value)
    elif isinstance(value, bool):
        rendered = "yes" if value else "no"
    elif is_simple_list(value):
        # Show a list as a list. A reviewer checking copy should not have to
        # read JSON to find out what the bullet points say.
        box = '<span class="box"></span>' if component_type == "checklist" else ""
        items = "".join(f"<li>{box}{esc(v)}</li>" for v in value)
        rendered = f'<ul class="items">{items}</ul>'
    elif isinstance(value, (list, dict)):
        rendered = f"<code>{esc(json.dumps(value, ensure_ascii=False))}</code>"
    else:
        rendered = esc(value)
    return f"<dt>{esc(name)}</dt><dd>{rendered}</dd>"


# --------------------------------------------------------------------------
# 3. Rendering one component, and its children
# --------------------------------------------------------------------------


def render_component(component: dict, number: str, depth: int = 0) -> str:
    """Emit one <section>. Recurses into slots for nested components.

    `number` is the reviewer-facing outline number: "2", then "2.1", "2.1.1".
    It exists so somebody can say "section 4.2 has a typo" out loud.
    """
    props = dict(component.get("props") or {})

    # Pull the heading and body out of the props, so they can be shown as
    # prose rather than as rows in a table.
    heading = next((props.pop(p) for p in HEADING_PROPS if p in props), None)
    body = next((props.pop(p) for p in BODY_PROPS if p in props), None)

    # Images read better directly under the body than in the prop list.
    images = {k: props.pop(k) for k, v in list(props.items()) if is_asset(v)}

    level = min(depth + 2, 6)  # <h2> for top-level components, deeper inside
    out = [f'<section class="component depth-{min(depth, 3)}">']
    out.append(
        '<p class="meta">'
        f'<span class="num">{esc(number)}</span>'
        f'<code class="type">{esc(component.get("type", "?"))}</code>'
        f'<code class="id">{esc(component.get("id", ""))}</code>'
        + (f'<span class="label">{esc(component["label"])}</span>'
           if component.get("label") else "")
        + "</p>"
    )

    if heading is not None:
        out.append(f"<h{level}>{esc(heading)}</h{level}>")
    if body is not None:
        # Blank lines separate paragraphs; that is the only formatting the
        # prose layer is allowed to carry inside a single prop.
        for para in str(body).split("\n\n"):
            if para.strip():
                out.append(f"<p>{esc(para.strip())}</p>")

    for value in images.values():
        out.append(render_asset(value))

    if props:
        ctype = component.get("type", "")
        rows = "".join(render_prop(k, v, ctype) for k, v in props.items())
        out.append(f"<dl>{rows}</dl>")

    # Slots: named lists of child components. Recurse.
    for slot_name, children in (component.get("slots") or {}).items():
        out.append(f'<div class="slot"><p class="slot-name">{esc(slot_name)}</p>')
        for i, child in enumerate(children, start=1):
            out.append(render_component(child, f"{number}.{i}", depth + 1))
        out.append("</div>")

    out.append("</section>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# 4. The page wrapper
# --------------------------------------------------------------------------

CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0 auto; padding: 2.5rem 1.5rem 6rem; max-width: 48rem;
  font: 16px/1.6 Georgia, 'Times New Roman', serif;
  color: #1f2420; background: #fbfbf9;
}
header.page { border-bottom: 2px solid #2c5f2d; padding-bottom: 1.25rem; margin-bottom: 2rem; }
header.page h1 { font-size: 2rem; margin: 0 0 .35rem; color: #1f4620; }
header.page .alias { font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: .85rem; color: #5a6b58; margin: 0; }
header.page .note { font-size: .8rem; color: #7c8a7a; margin: .75rem 0 0; font-style: italic; }

.component { margin: 0 0 2rem; }
.component.depth-1, .component.depth-2, .component.depth-3 { margin-bottom: 1.25rem; }

.meta { margin: 0 0 .4rem; font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
  font-size: .72rem; color: #7c8a7a; display: flex; gap: .5rem; align-items: baseline;
  flex-wrap: wrap; }
.meta .num { font-weight: 700; color: #2c5f2d; }
.meta code { background: #eef2ea; padding: .05rem .35rem; border-radius: 3px; color: #4a5a48; }
.meta .label { font-style: italic; }

h2, h3, h4, h5, h6 { color: #1f4620; line-height: 1.25; margin: .1rem 0 .6rem; }
h2 { font-size: 1.5rem; }
h3 { font-size: 1.2rem; }
h4, h5, h6 { font-size: 1.05rem; }
p { margin: 0 0 .8rem; }

figure { margin: 1rem 0; }
/* Big enough to judge the image, capped so a proof sheet stays scannable —
   this is the limitation that made a 180px Markdown thumbnail inadequate. */
figure img { max-width: 100%; max-height: 22rem; width: auto; height: auto;
  display: block; border: 1px solid #dce1d8; }
.slot figure img { max-height: 14rem; }
figcaption { font-size: .8rem; color: #6b7a69; margin-top: .35rem; font-style: italic; }
p.file a { font-family: ui-monospace, Menlo, monospace; font-size: .85rem; }

dl { margin: .75rem 0; display: grid; grid-template-columns: minmax(6rem, 10rem) 1fr;
  gap: .3rem .9rem; font-size: .88rem; border-left: 2px solid #e3e8df; padding-left: .9rem; }
dt { font-family: ui-monospace, Menlo, monospace; font-size: .78rem; color: #6b7a69; }
dd { margin: 0; }
dd code { font-size: .78rem; word-break: break-all; }
ul.items { margin: 0; padding-left: 1.1rem; }
ul.items li { margin: 0 0 .2rem; }
.box { display: inline-block; width: .72em; height: .72em; margin-right: .45em;
  border: 1.5px solid #8a9788; border-radius: 2px; vertical-align: baseline; }

.slot { margin: .75rem 0 .75rem 1.25rem; border-left: 2px dotted #c6d0c1; padding-left: 1.25rem; }
.slot-name { margin: 0 0 .5rem; font-family: ui-monospace, Menlo, monospace;
  font-size: .7rem; letter-spacing: .06em; text-transform: uppercase; color: #97a894; }

footer.page { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #dce1d8;
  font-size: .75rem; color: #8a9788; }

@media print {
  body { background: #fff; padding: 0; max-width: none; font-size: 11pt; }
  .component { page-break-inside: avoid; }
  .meta { color: #999; }
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
<header class="page">
  <h1>{title}</h1>
  <p class="alias">{alias}</p>
  <p class="note">Content proof sheet — not a design mockup. Generated from {source}; edits belong in the bundle, not here.</p>
</header>
{components}
<footer class="page">Toadshade bundle <code>{slug}</code> · {count} components</footer>
</body>
</html>
"""


def render_html(bundle: dict, source_name: str = "the bundle") -> str:
    """Turn a loaded bundle dict into a complete HTML document."""
    components = bundle.get("components") or []
    body = "\n".join(
        render_component(c, str(i)) for i, c in enumerate(components, start=1)
    )

    def total(items) -> int:
        return sum(
            1 + total(child for s in (c.get("slots") or {}).values() for child in s)
            for c in items
        )
    return PAGE.format(
        title=esc(bundle.get("title", bundle.get("slug", "Untitled"))),
        alias=esc(bundle.get("alias", "")),
        slug=esc(bundle.get("slug", "")),
        source=esc(source_name),
        css=CSS,
        components=body,
        count=total(components),
    )


# --------------------------------------------------------------------------
# 5. The bit that touches the disk
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

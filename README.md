# Toadshade

**One page, one directory.** Prose in Markdown, structure in JSON, images
alongside, and a double-clickable HTML proof sheet generated from the two.

```
content/visit/trail-guide/
├── trail-guide.md      ← what the page says      (a human writes this)
├── trail-guide.json    ← what the page is        (an importer reads this)
├── trail-guide.html    ← what a reviewer opens   (generated)
└── assets/
    ├── trailhead.jpg
    └── boardwalk.jpg
```

No database. No manifest. No export step. The folder already is the map —
`tree` shows you the information architecture, `git diff` shows you one page,
and `mv` moves a page.

---

## Why

Content portability formats keep making the same trade. Either the format is
readable and vague — Markdown with front matter, which no importer can trust
past the first custom component — or it is precise and unreadable, like
Portable Text, whose own documentation says it is for tooling rather than for
people.

Toadshade stops trying to be one file. The prose lives in Markdown where a
person can edit it. The structure lives in JSON where a schema can check it.
And because "readable" in practice means *a non-technical reviewer can open the
thing and see the page*, the format specifies a third file: a self-contained
HTML proof sheet, generated, that opens from `file://` with its images at a
size you can actually judge.

That last file is the part most formats leave as somebody else's problem, and
it is the one that decides whether the human layer survives contact with a
real project. Ours didn't, the first time. See [SPEC.md](SPEC.md) §5.

## Install

```bash
pip install toadshade          # or: git clone && pip install -e .
```

No required dependencies. `render` and the structural half of `validate` are
standard library only, so the tool runs from a bare checkout on any machine
with Python 3.9+. Install the `schema` extra to add full JSON Schema checking:

```bash
pip install "toadshade[schema]"
```

## Use

```bash
toadshade list     content/          # show every bundle and its alias
toadshade validate content/          # errors and warnings, exit 1 on error
toadshade render   content/          # write {slug}.html next to each bundle
toadshade new      content/ pricing  # scaffold an empty bundle
```

Try it on the worked example in this repo:

```bash
toadshade validate examples -v
toadshade render examples
open examples/trail-guide/trail-guide.html
```

## The format in one screen

```json
{
  "toadshade": "0.1",
  "slug": "trail-guide",
  "title": "Trail Guide",
  "alias": "/visit/trail-guide",
  "components": [
    {
      "id": "s1",
      "type": "hero",
      "label": "Banner",
      "props": {
        "heading": "Trail Guide",
        "body": "Eleven miles of marked trail, dawn to dusk.",
        "featured_image": { "$asset": "assets/trailhead.jpg", "alt": "Trailhead signpost" }
      }
    },
    {
      "id": "s2",
      "type": "section",
      "props": { "heading": "Pick a trail" },
      "slots": {
        "content": [
          { "id": "s2a", "type": "card-grid", "slots": { "cards": [] } }
        ]
      }
    }
  ]
}
```

`props` are values. `slots` are ordered lists of child components. An
`{"$asset": "assets/x.jpg", "alt": "..."}` object is a reference to a file
inside the bundle. That is the entire model — the full rules are in
[SPEC.md](SPEC.md).

## The HTML generator

`src/toadshade/render.py` is the whole preview generator: one file, standard
library only, about 200 lines including its CSS, and it reads top to bottom —
load the JSON, walk the component tree emitting a `<section>` each, wrap it in
a page with inlined styles, write the file. It is meant to be read and changed
by whoever is using it. Run it on its own if you like:

```bash
python src/toadshade/render.py examples/trail-guide
```

## Importers and exporters

Toadshade is a format, not a pipeline.

**Into Toadshade** — `X-to-toadshade` exporters, following the shape of
Dogsheep's `X-to-sqlite` family, with the source-specific fetching kept
separate from the bundle writing so the hard part stays reusable. The contract
is in [`src/toadshade/importers/base.py`](src/toadshade/importers/base.py) and
the rationale is in [docs/importers.md](docs/importers.md).

The first one is built:

```bash
pip install "toadshade[apple-notes]"
apple-notes-to-toadshade content/          # or: apple-notes-to-sqlite --dump | ... -
```

`apple-notes-to-toadshade` is a port of Simon Willison's
[`apple-notes-to-sqlite`](https://datasette.io/tools/apple-notes-to-sqlite).
It imports his `extract_notes()` — the AppleScript driver, the streaming
reassembly, the mac_roman decoding — completely unchanged, and replaces the
one line that wrote to SQLite. Your notes come out as a folder tree of pages,
each with its lists, checklists and inline images intact, each with an HTML
proof sheet you can double-click. Nothing is uploaded.

**Out of Toadshade** — destination-side importers. The reference
implementation is
[Drupal-Canvas-Page-Migrate](https://github.com/cellear/Drupal-Canvas-Page-Migrate):
a Drupal Migrate source and destination pair that walks a content root, builds
Canvas pages, and resolves `$asset` references to media entities by SHA-256
content hash, so the same photo colocated in six bundles becomes one media
entity on import.

## Status

0.1, draft. The format is in use in one real Drupal project's lineage and is
otherwise unproven. Markdown → JSON round-tripping is the main gap; see
[SPEC.md](SPEC.md) §9.

## Prior art

Kirby, Grav and Hugo page bundles for file-per-page with colocated assets.
Gutenberg's `block.json` and Markdoc for schema-validated component trees.
Portable Text for a JSON document model. Dogsheep for the exporter family
shape. Details and honest comparisons in [docs/prior-art.md](docs/prior-art.md).

## License

MIT. See [LICENSE](LICENSE).

# The Toadshade Page Bundle Format

**Version 0.1 — draft**

A Toadshade bundle is one directory per page. The directory tree mirrors the
site's URL tree. Everything a page needs — its prose, its structure, and its
images — lives inside that directory and nowhere else.

```
content/
└── visit/
    └── trail-guide/
        ├── trail-guide.md      human layer   (authored)
        ├── trail-guide.json    machine layer (authored)
        ├── trail-guide.html    preview       (generated)
        └── assets/
            ├── trailhead.jpg
            └── boardwalk.jpg
```

There is no database, no manifest, no central index, and no export step. The
folder already is the map.

---

## 1. Design rules

These are the constraints the format is built to satisfy. Everything below is
downstream of them.

**One page, one directory.** A page is never split across files in different
places, and two pages never share a file. Moving a page is `mv`. Deleting a
page is `rm -r`. Diffing a page is one diff.

**The filesystem carries the sitemap.** Directory nesting mirrors URL nesting,
so you can read the information architecture with `tree` and no tooling at all.
The authoritative URL is still the `alias` inside the JSON — the path is a
convention for humans, not a key for machines. This keeps the tree honest when
a page's URL and its place in the hierarchy disagree.

**Assets are colocated, never centralized.** An image referenced by a page sits
in that page's `assets/`. There is no shared media library in the bundle
format. Deduplication is a *destination* concern: an importer that hashes and
dedupes on the way in (see §7) loses nothing, and a bundle that is copied
somewhere else arrives complete.

**Human layer and machine layer are separate files, not one clever file.**
Every format that has tried to be simultaneously readable and rigorous has
ended up bad at one of them. Markdown with embedded structure becomes
unreadable; JSON with embedded prose becomes unwritable. Toadshade stops
trying: the prose lives in Markdown, the structure lives in JSON, and a
generated HTML file shows you both at once.

**The reviewable artifact must open with a double-click.** Not "render it in
an editor that supports Markdown." Not "run the preview server." A file, on
disk, that opens in a browser and shows the page's content with its images at
their real size. This is `{slug}.html`, and it is generated — see §5.

---

## 2. The bundle directory

A bundle is a directory whose name is the page's **slug**. It contains:

| File | Required | Role |
|---|---|---|
| `{slug}.json` | yes | Structure, props, asset references. Authoritative for import. |
| `{slug}.md` | yes | Prose, as a human writes and reviews it. Authoritative for text. |
| `{slug}.html` | no | Generated preview. Build artifact — safe to gitignore. |
| `assets/` | no | Images and documents referenced by this page only. |

The slug must match `[a-z0-9]+(-[a-z0-9]+)*`. Both required files are named
after it, so a bundle directory is self-describing in a file listing and two
bundles never collide when flattened.

A directory that contains a `{slug}.json` matching its own name **is** a
bundle. A directory that does not is just a directory, and may contain
bundles. This is what makes the tree walkable without a manifest.

---

## 3. The machine layer — `{slug}.json`

```json
{
  "toadshade": "0.1",
  "slug": "trail-guide",
  "title": "Trail Guide",
  "alias": "/visit/trail-guide",
  "meta": {
    "source": "figma:1:234",
    "generator": "figma-to-toadshade 0.1"
  },
  "components": [
    {
      "id": "s1",
      "type": "hero",
      "label": "Banner",
      "props": {
        "heading": "Trail Guide",
        "body": "Eleven miles of marked trail through the creek bottom and up onto the ridge.",
        "featured_image": { "$asset": "assets/trailhead.jpg", "alt": "Boardwalk junction with a carved wooden signpost" }
      }
    }
  ]
}
```

### Top-level keys

| Key | Required | Meaning |
|---|---|---|
| `toadshade` | yes | Format version string. `"0.1"` today. |
| `slug` | yes | Must equal the directory name and the filename stem. |
| `title` | yes | The page's human title. |
| `alias` | yes | The page's URL path, root-relative, leading slash. The page's real identity. |
| `components` | yes | Ordered array of components. May be empty. |
| `meta` | no | Free-form provenance. Never interpreted by importers. |

### Components

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | Unique within the bundle. The join key to the Markdown layer. |
| `type` | yes | The component's machine name in the destination system. |
| `label` | no | Editor-facing name. Never rendered to the public. |
| `props` | no | Object of scalar / asset-reference values. |
| `slots` | no | Object mapping slot name → ordered array of child components. |

`props` and `slots` are **explicitly separated**. Some destination formats
infer slots structurally — any key whose value is a list of component-shaped
maps — and emitters targeting those may flatten on the way out. The bundle
itself does not rely on inference, because inference makes validation
ambiguous and makes a prop named `cards` holding a list of strings a footgun.

`id` values are opaque strings. Generators conventionally use `s1`, `s2`,
`s2a` for readability in the Markdown meta lines, but nothing depends on that.

### Asset references

Any prop value may be an **asset reference** instead of a scalar:

```json
{ "$asset": "assets/boardwalk.jpg", "alt": "Boardwalk through swamp forest" }
```

| Key | Required | Meaning |
|---|---|---|
| `$asset` | yes | Path relative to the bundle directory. Must start with `assets/`. |
| `alt` | no | Alternative text. Required for images in any accessible destination. |
| `title` | no | Advisory title / caption. |

The path is relative to the bundle directory, always forward-slashed, never
absolute, never escaping the bundle with `..`. A validator checks that the
file exists; see §6.

---

## 4. The human layer — `{slug}.md`

The Markdown file mirrors the same components in the same order, carrying the
prose a person actually reads and edits.

```markdown
# Trail Guide

*alias: `/visit/trail-guide`*

---

## 1. Trail Guide

*id: `s1` · type: `hero`*

Eleven miles of marked trail through the creek bottom and up onto the ridge.

<img src="assets/trailhead.jpg" alt="Boardwalk junction with a carved wooden signpost" width="180">

---

## 2. Pick a trail

*id: `s2` · type: `section`*

All three loops start from the same parking area and are blazed in different
colors. Conditions change fast after rain.

**cta:** See all trails → /visit/trail-guide/all
```

### Conventions

- **`# Title`** — the page title, once, at the top, followed by an italic
  `alias:` line. Everything above the first `---` is the page header.
- **`## N. Heading`** — one per top-level component, numbered so a reviewer can
  say "section 4 has a typo." The heading text is the component's primary text
  prop (`heading` if present, else `title`).
- **`### N.M Heading`** — a child component, one heading level deeper per level
  of slot nesting.
- **Italic meta line** — `*id: \`s2\` · type: \`section\`*` immediately under
  each heading. This is the join key back to the JSON, and the only part of the
  Markdown a tool needs to parse reliably.
- **Plain paragraphs** — the component's `body` prop.
- **`**key:** value`** — any other prop. `label → /url` splits into a
  label/URL pair for link-shaped props.
- **`<img src="assets/x.jpg" alt="..." width="180">`** — inline thumbnail,
  written as HTML rather than `![]()` so the preview width can be constrained.
  This is the one place raw HTML is expected in the Markdown, and the reason
  is mundane: Markdown image syntax has no width.
- **`> **NOTE:**`** — editorial commentary. Visible to reviewers, ignored by
  every tool.
- **`---`** — a rule between components. Cosmetic.

### Which file wins

The two files overlap on purpose, and the rule for conflicts is fixed:

- **JSON is authoritative for structure** — which components exist, their
  order, their types, their nesting, their non-prose props, their assets.
- **Markdown is authoritative for prose** — the text a human last edited.
- **`toadshade check` reports drift** between them rather than silently
  picking a winner.

Full automated round-tripping of Markdown edits back into the JSON is on the
roadmap, not in 0.1. Today the honest workflow is: generate both from a
source, review the HTML, and correct the JSON.

---

## 5. The preview — `{slug}.html`

`toadshade render` writes a single self-contained HTML file next to the other
two. It is a **build artifact**: never hand-edited, never authoritative, safe
to gitignore or commit as you prefer.

It is generated from `{slug}.json` alone. The JSON carries the prose inline, so
the renderer never has to parse Markdown — which is what keeps it short enough
to read in one sitting (`src/toadshade/render.py`, standard library only, no
dependencies). When the Markdown layer has drifted from the JSON,
`toadshade check` says so; the renderer does not try to reconcile them.

It must satisfy exactly one requirement: **a non-technical reviewer opens it by
double-clicking it, and sees the page's content.** Which means:

- No external stylesheet, no external script, no build step, no server. It
  opens from `file://`.
- Images referenced by relative path, so the file and its `assets/` travel
  together and the images actually appear.
- Images at a readable size — not constrained to a 180px thumbnail, which is
  the limitation that made the Markdown layer inadequate for review in the
  first place.
- Laid out like a web page: a title, images where they fall, formatted text,
  a short list of details. A reviewer should recognize the page, not decode
  a data dump.
- Component boundaries, types, and ids one switch away. A **Show structure**
  toggle (pure CSS, no script) outlines each component with its outline
  number, type, and id. A reviewer reads the copy first, and the structure is
  there when they need to point at "section 2.1".
- It prints. Reviewers print things.

The preview looks like *a* web page, not like *the* destination site. It uses
a plain, generic layout rather than the site's theme. That's close enough for
a reviewer to read the content in context — headings, images, links — without
being invited to review the brand design. It also keeps the renderer free of
any knowledge of the destination.

### Rendering conventions

The renderer reads a few prop names by convention and shows everything else
as a details list:

| Prop | Rendered as |
|---|---|
| `heading`, `title` | the component's heading, omitted when it repeats the page title |
| `body`, `text`, `description` | prose, split into paragraphs on blank lines |
| `body_html` | used instead of `body` when present, after sanitizing (see below) |
| `format` | the text format of `body_html`; not displayed |
| image asset references | figures; other assets become download links |
| ISO 8601 dates, `{value, end_value}` ranges | readable dates |
| a URL prop whose name contains `video` | a link on the component's first image, shown with a play badge |

Sanitizing `body_html`:
- Tags come from a fixed allow-list of text markup. Everything else is
  unwrapped to its text.
- Only `href`, `src`, `alt`, and `title` attributes survive, plus table spans.
- Scripts, styles, forms, embeds, and media players are dropped along with
  their contents.
- Links are kept as links, but only `http`, `https`, `mailto`, and relative
  URLs.
- An image survives only if it points into the bundle's own `assets/`.
  Nothing is ever *loaded* from outside the bundle.

---

## 6. Validation

`toadshade validate` checks, in order:

1. **Schema** — `{slug}.json` against `schema/bundle.schema.json`.
2. **Slug agreement** — directory name, filename stems, and the `slug` key all
   match.
3. **Alias shape** — root-relative, leading slash, no trailing slash except
   for the site root `/`.
4. **Unique ids** — no `id` appears twice anywhere in the component tree.
5. **Asset resolution** — every `$asset` path starts with `assets/`, stays
   inside the bundle, and names a file that exists.
6. **Alt text** — every `$asset` that looks like an image carries a non-empty
   `alt`. A warning, not an error, because some assets are downloads.
7. **Markdown join** — every `id` in the Markdown meta lines exists in the
   JSON, and vice versa. A warning: a bundle whose Markdown has drifted is
   still importable.

Errors mean the bundle will not import correctly. Warnings mean a human should
look. The exit code is non-zero only for errors.

A validator deliberately does **not** check props against the destination
system's component schemas. That check belongs to the importer, which is the
only thing that knows what `hero` means. Toadshade validates that a bundle is
a well-formed bundle; the destination validates that it is a valid page.

---

## 7. Importers and exporters

Toadshade is a format, not a pipeline. Two kinds of tool surround it:

**Exporters — `X-to-toadshade`.** Read some system, write bundles. The naming
and shape follow Dogsheep's `X-to-sqlite` family, and for the same reason:
the fetch layer and the write layer are separable, so the hard-won code that
knows how to talk to an API is reusable by anyone who wants the data in a
different shape. See `docs/importers.md` for the contract.

**Importers — destination-side.** Read bundles, create pages. The reference
implementation is
[Drupal-Canvas-Page-Migrate](https://github.com/cellear/Drupal-Canvas-Page-Migrate),
a Drupal Migrate source + destination pair that walks a content root, reads
each bundle, resolves `$asset` references to media entities by SHA-256 content
hash (so a duplicated image becomes one entity), and builds Canvas pages.

Destination-side content-hash deduplication is the intended answer to
colocated assets: the same photo may appear in the `assets/` of six bundles
and become one media entity on import. Bundles stay self-contained; the
destination stays tidy.

---

## 8. Prior art, and what is actually new here

Nothing here is unprecedented, and the parts that exist elsewhere are cited in
`docs/prior-art.md`. Briefly: file-per-page with colocated assets is Kirby,
Grav, and Hugo page bundles. Schema-validated component trees are WordPress
Gutenberg's `block.json` and Stripe's Markdoc. A JSON document model for
portable rich content is Sanity's Portable Text, which is explicit that it is
for tooling and not for humans.

The combination is the claim: a **file-per-page tree** whose **structure is
schema-checked**, whose **prose stays in Markdown**, and which **ships a
double-clickable review artifact** — so the same directory serves the
importer, the version-control diff, and the person who has to sign off on the
copy, without any of the three being second-class.

---

## 9. Roadmap

- Markdown → JSON round-tripping, so an editor's correction in the human layer
  can be promoted to the machine layer mechanically.
- A `toadshade diff` that compares two bundles semantically rather than
  textually.
- Destination profiles: an optional per-project mapping from Toadshade
  component types and props to a target system's names, so one bundle set can
  import into more than one destination.
- More exporters. The list in `docs/importers.md` is a wish list, not a
  changelog.

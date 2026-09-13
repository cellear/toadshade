# Exporters: the `X-to-toadshade` family

## The idea, and where it comes from

Simon Willison's [Dogsheep](https://dogsheep.github.io/) is a family of tools
named `X-to-sqlite` — `twitter-to-sqlite`, `github-to-sqlite`,
`healthkit-to-sqlite`, and a dozen more. Each one reads a personal-data source
and writes a SQLite database, which Datasette then browses.

Reading the source of `github-to-sqlite` shows why the family works. The
functions that talk to GitHub — `fetch_repos()`, `fetch_issues()`, pagination,
auth, rate limits — are cleanly separate from the `save_*()` functions that
call `sqlite-utils`. The genuinely difficult, tediously-earned code is the
fetching. The saving is a few lines.

That means the fetch layer is reusable by anyone who wants the same data in a
different shape. Which is the entire opportunity: **the same fetch layer that
feeds `X-to-sqlite` can feed `X-to-toadshade`**, and the two families are
complements rather than competitors. SQLite for things you want to query;
Toadshade for things you want to *read*, review, and import somewhere as pages.

## The contract

Subclass `Exporter`, implement two methods, and you have an exporter.

```python
from toadshade.importers import Exporter, BundleDraft


class MyThingToToadshade(Exporter):
    name = "mything-to-toadshade"

    def fetch(self):
        """Yield raw records. No Toadshade types in here, on purpose."""
        for record in read_the_source(self.source):
            yield record

    def to_bundle(self, record):
        """One record in, one BundleDraft out. Return None to skip."""
        return BundleDraft(
            slug=slugify(record["id"]),
            title=record["title"],
            alias=f"/things/{slugify(record['id'])}",
            components=[
                {
                    "id": "s1",
                    "type": "rich-text",
                    "props": {"heading": record["title"], "body": record["body"]},
                }
            ],
            assets={"assets/cover.jpg": record["cover_bytes"]},
            meta={"source": f"mything:{record['id']}"},
        )
```

Then:

```python
for path in MyThingToToadshade(source="export.zip", content_root="content").export():
    print(path)
```

`export()` calls `fetch()`, passes each record through `to_bundle()`, writes
the JSON, the Markdown, and the assets, and renders the HTML preview.

### Rules

**Keep `fetch()` free of Toadshade.** It should yield plain dicts and be
importable by someone who has never heard of this format. If you find yourself
importing `BundleDraft` inside `fetch()`, the split has leaked.

**Put provenance in `meta`.** `{"source": "twitter:1234567890"}` costs nothing
and makes a bundle traceable back to the record it came from. Nothing
interprets `meta`; it is for humans and for your own re-runs.

**Assets are bytes, keyed by path.** `{"assets/cover.jpg": b"..."}`. The
exporter writes them verbatim. Don't fetch them lazily at render time — a
bundle that needs the network to be complete is not a bundle.

**Write a better Markdown layer if you can.** `BundleDraft.markdown` defaults
to a serviceable generated one, but an exporter that understands its source
usually knows how to lay the prose out better than a generic walker does.

**Idempotence is your problem.** `export()` overwrites. If re-running should
preserve local edits, diff before writing or write to a staging root.

## Naming

`{source}-to-toadshade`, lowercase, hyphenated, matching the Dogsheep
convention so the family is greppable. Where a Dogsheep tool already exists for
the source, match its source name exactly — `github-to-sqlite` →
`github-to-toadshade` — so the two are obviously siblings.

These PyPI names were free as of September 2026: `twitter-to-toadshade`,
`x-to-toadshade`, `github-to-toadshade`, `takeout-to-toadshade`.

## Wish list

Not a changelog — none of these exist yet.

| Exporter | Source | Why it's interesting |
|---|---|---|
| `github-to-toadshade` | Repos, issues, gists | Issues as pages, readable offline, diffable |
| `takeout-to-toadshade` | Google Takeout | The canonical "get my data out" archive, currently unreadable |
| `twitter-to-toadshade` | Twitter/X archive | Threads become pages; the archive's JSON is not for humans |
| `wordpress-to-toadshade` | WXR export | The most common CMS migration on earth |
| `joomla-to-toadshade` | Joomla database | Because somebody has to |
| `notion-to-toadshade` | Notion export | Blocks map to components almost directly |
| `figma-to-toadshade` | `.fig` file | Already prototyped; parses offline, no API |

`apple-notes-to-toadshade` is absent from this list because it is built — see below.

The Figma one has a working ancestor: an offline `.fig` parser that walked
75,000 nodes, pulled text overrides out of component instances, extracted
image hashes, and emitted page bundles — no Figma API, no network. That code
is the origin of this format.


---

## The first one: `apple-notes-to-toadshade`

Shipped in this repo at `src/toadshade/importers/apple_notes.py`, and the
proof that the split above is real rather than aspirational.

### What was reused, and what was written

`apple-notes-to-sqlite` is about 120 lines. Nearly all of it is
`extract_notes()` and `count_notes()`: two AppleScript programs, a
`subprocess` pipeline against `osascript`, a randomly generated delimiter so
note bodies cannot collide with the field markers, streaming line-by-line
reassembly, and the discovery that `osascript` emits **mac_roman**. None of
that is obvious, and all of it had to be found by experiment.

Its writing layer is one line:

```python
db["notes"].insert(note, pk="id", replace=True)
```

So the port imports the first part unchanged —

```python
from apple_notes_to_sqlite.cli import extract_notes
```

— and replaces that single line with an HTML-to-component-tree builder and a
bundle writer. The AppleScript was not touched, read closely, or
reimplemented. That is the whole argument for the family: **the expensive half
is source-specific and reusable; the cheap half is what decides the output
format.**

### Two ways to run it

```bash
# live: reads Apple Notes directly, macOS only, prompts for permission
pip install "toadshade[apple-notes]"
apple-notes-to-toadshade content/

# piped: nothing imported, works from any dump in the same shape
apple-notes-to-sqlite --dump | apple-notes-to-toadshade content/ -

# from a saved dump, with a limit while you are experimenting
apple-notes-to-toadshade content/ notes.jsonl --stop-after 20
```

The second form matters more than it looks. `--dump` already emits
newline-delimited JSON, so the exporter has a documented input shape that
anything can produce — and you can inspect exactly what you are about to
export before a single file is written.

### What a note becomes

Each note is one bundle at `notes/<year>/<slug>/`, so the directory tree and
the URL tree agree, as SPEC §1 requires.

The note's HTML body is parsed into a component tree rather than stored as a
blob:

| In Apple Notes | In the bundle |
|---|---|
| `<h1>` … `<h6>` | opens a `section`, heading becomes its `heading` prop |
| paragraphs | the enclosing section's `body` prop, or a `note-text` component |
| `<ul>` / `<ol>` | a `note-list` component in the section's `content` slot |
| `<ul class="checklist">` | a `checklist` component, rendered with checkboxes |
| inline images (data URIs) | decoded to real bytes in `assets/`, referenced by `$asset` |
| remote images | kept as an `image_url` string, never a dangling `$asset` |

Duplicate titles get distinct slugs (`ideas`, `ideas-2`), untitled notes
become "Untitled note", and a note whose date is unparseable is filed under
`notes/undated/` rather than dropped. Provenance goes in `meta`:
`{"source": "apple-notes:x-coredata://...", "created": ..., "updated": ...}`.

### What it does not do

The upstream AppleScript does not select each note's **folder**, so folder
hierarchy is unavailable and bundles are filed by year instead. Recovering it
means extending the AppleScript — which would be a change to Simon's tool, or
a fork of that one function, and is deliberately not done here: the point of
this port is that it did not need to touch the fetch layer at all.

Attachments that Notes stores outside the HTML body are likewise not
retrieved. Inline images are.

### Privacy

Nothing is uploaded. The exporter writes to the content root you name and
makes no network calls. The tests in `tests/test_apple_notes.py` run against a
synthetic fixture in `tests/fixtures/` — no real notes were read to build
this.

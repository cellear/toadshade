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

The Figma one has a working ancestor: an offline `.fig` parser that walked
75,000 nodes, pulled text overrides out of component instances, extracted
image hashes, and emitted page bundles — no Figma API, no network. That code
is the origin of this format.

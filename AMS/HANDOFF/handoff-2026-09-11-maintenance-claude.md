# Handoff — Toadshade maintenance

**Date:** 2026-09-11
**Repo:** `~/Sites/HACKATHONS/toadshade` · https://github.com/cellear/toadshade (public, MIT)
**State at handoff:** 2 commits, 40 tests passing, `main` one commit ahead of `origin/main`
**Purpose of this file:** start a new thread cold without relitigating settled decisions

---

## What it is, in three sentences

Toadshade is a content-portability format: one page, one directory. The prose
lives in a Markdown file, the structure lives in a JSON file, images sit
alongside in `assets/`, and a generated self-contained HTML file is the thing a
non-technical reviewer double-clicks. The directory tree mirrors the URL tree,
so the folder *is* the sitemap — no database, no manifest, no export step.

It came out of a Drupal Canvas page-import pipeline built during a 2026 client
engagement. That project's human layer was a `.md` file, it required a
Markdown-rendering editor to read, and within two months it had stopped being
regenerated and was gone. The double-clickable HTML file is the direct response
to that failure, and it is the one genuinely novel thing in the format.

---

## Repo map

| Path | What it is |
|---|---|
| `SPEC.md` | The format, v0.1. Nine sections. The authority — code follows it, not the reverse. |
| `schema/bundle.schema.json` | JSON Schema (draft 2020-12) for the machine layer. |
| `src/toadshade/bundle.py` | `Bundle` (load/walk/assets), `find_bundles()` discovery. |
| `src/toadshade/validate.py` | Errors vs warnings; optional jsonschema check. |
| `src/toadshade/render.py` | The HTML proof-sheet generator. Stdlib only, ~230 lines with its CSS. |
| `src/toadshade/cli.py` | `toadshade validate / render / list / new`. |
| `src/toadshade/importers/base.py` | `Exporter` + `BundleDraft` — the `X-to-toadshade` contract. |
| `src/toadshade/importers/apple_notes.py` | First real exporter. Port of `apple-notes-to-sqlite`. |
| `docs/importers.md` | Exporter rationale, contract, wish list, the Apple Notes write-up. |
| `docs/prior-art.md` | Honest comparisons (Kirby, Gutenberg, Markdoc, Portable Text, Dogsheep, Perkeep). |
| `examples/trail-guide/` | Worked bundle: nested slots, image + non-image assets, real photos. |
| `tests/` | 40 tests. `tests/fixtures/apple-notes-dump.jsonl` is synthetic. |

---

## Invariants — do not break these without a deliberate decision

**Zero required dependencies.** `render` and the structural half of `validate`
are standard library only, so the tool runs from a bare checkout on any machine
with Python 3.9+. `jsonschema` is an optional extra and must always degrade to
a *skip*, never a crash and never a false error. (Luke's machine has
jsonschema 3.2.0, which lacks `Draft202012Validator` — this is already handled
and there is a test for it. Don't regress it.)

**`render.py` stays short and readable.** It is deliberately a single
dependency-free file that reads top to bottom, because explaining it in five
minutes is part of the pitch. Resist template engines, Markdown parsers, and
clever abstraction. If it needs to grow, grow it in plain functions.

**The preview must open from `file://`.** No external stylesheet, no external
script, no CDN, no build step, no server. Relative asset paths only. There is a
test asserting no `http://` or `https://` in the output body.

**A `$asset` reference must always resolve.** If an exporter can't pull bytes
into the bundle, it records a plain string prop (e.g. `image_url`) instead of
emitting a dangling reference. Never invent new `$`-prefixed pseudo-references
without adding them to `SPEC.md` §3 first.

**Errors vs warnings mean specific things.** Error = the bundle will not import
correctly. Warning = a human should look, but it will import. Exit code is
non-zero only for errors. Missing alt text is a warning, on purpose.

**Exporter `fetch()` stays free of Toadshade.** It yields plain dicts and
should be importable by someone who has never heard of the format. If
`BundleDraft` appears inside `fetch()`, the split has leaked and the whole
argument for the family collapses.

**Example content must never be traceable to a client.** This bit us once —
the original example used real page names and paths from the client project,
and git history had to be rewritten to remove them. Sample content is invented,
generic, and uses reserved-for-fiction contact details. The concern is what the
former client, the consultancy, or a future employer could find by searching,
not who sees it in a room.

---

## Settled decisions (don't relitigate without new information)

**Why two files instead of one clever file.** Every format that tried to be
simultaneously readable and rigorous ended up bad at one of them. Markdown with
embedded structure becomes unreadable; JSON with embedded prose becomes
unwritable. Toadshade stops trying: prose in MD, structure in JSON, and a
generated HTML file that shows both.

**Why `props` and `slots` are explicitly separated.** The original Drupal
implementation inferred slots structurally — any key whose value is a list of
component-shaped maps. That makes validation ambiguous and makes a prop named
`cards` holding a list of strings a footgun. Emitters targeting inference-based
destinations may flatten on the way out; the bundle itself does not rely on it.

**Why JSON is authoritative for import and MD for prose.** They overlap on
purpose. `validate` reports drift as a warning rather than silently picking a
winner. Full MD→JSON round-tripping is roadmap, not 0.1 — do not claim it works.

**Why the renderer reads JSON only.** The JSON carries the prose inline, so the
renderer never parses Markdown. That's what keeps it short.

**Naming.** "Toadshade" is a woodland trillium — chosen over Grist and Trillium
after checking PyPI/npm availability and cultural collisions. `X-to-toadshade`
mirrors Dogsheep's `X-to-sqlite`. Free on PyPI as of Sept 2026:
`twitter-to-toadshade`, `x-to-toadshade`, `github-to-toadshade`,
`takeout-to-toadshade`.

---

## Running it

```bash
cd ~/Sites/HACKATHONS/toadshade

PYTHONPATH=src python3 -m toadshade.cli validate examples -v
PYTHONPATH=src python3 -m toadshade.cli render examples
python3 src/toadshade/render.py examples/trail-guide     # renderer standalone

# the exporter, against the synthetic fixture (no Apple Notes needed)
PYTHONPATH=src python3 -m toadshade.importers.apple_notes /tmp/out \
    tests/fixtures/apple-notes-dump.jsonl

python3 -m pytest tests -q        # needs pytest; not installed on Luke's machine yet
```

Installed (`pip install -e .`) the entry points are `toadshade` and
`apple-notes-to-toadshade`.

---

## Known gaps, honestly stated

- **No MD→JSON round-tripping.** An editor's correction in the human layer
  can't be promoted to the machine layer mechanically. Biggest gap.
- **No destination profiles.** Component type and prop names are whatever the
  destination system calls them; one bundle set can't target two systems.
- **Apple Notes folders aren't captured** — upstream's AppleScript doesn't
  select them, so bundles are filed by year. Fixing it means forking that
  function, which would undercut the "didn't touch the fetch layer" claim.
- **One destination importer** (Drupal Canvas, separate repo), **one exporter**.
- **`toadshade diff`** (semantic rather than textual) is sketched in SPEC §9,
  not built.
- GitHub renders the example `.html` as source, not as a page. Pages would fix
  it; the counter-argument is that "clone and double-click" is the honest demo.

---

## Environment gotchas (cost time once already)

- **No global git identity** on the machine — commits need `-c user.name` /
  `-c user.email`, or set them locally in the repo.
- **`gh` is not installed.** Repo creation went through the web UI.
- **The agent sandbox can't reach `api.github.com` or SSH to GitHub.** Luke
  pushes from his own terminal; the sandbox can't verify remote state directly
  (check `git status -sb` locally instead).
- **`git` in the sandbox can't delete its own temp files** — it leaves
  `.git/index.lock`, `.git/HEAD.lock` and `tmp_obj_*` behind, which block the
  next commit. Clear them after any sandbox-side git operation.

---

## Working conventions

Luke pushes; agents don't. Ask before making changes. Be concise. Only make
suggestions when asked. Commits so far are co-authored and reference the
session URL.

---

## Open questions for the next thread

1. **Publish to PyPI?** The package is installable and named; `toadshade` on
   PyPI has not been checked or claimed.
2. **Does the exporter graduate to its own repo?** `docs/importers.md` says
   exporters are separate distributions, but `apple-notes-to-toadshade` ships
   in-tree. Fine for one; wrong for five.
3. **Second exporter, and which?** `github-to-toadshade` is the obvious next
   (public API, no auth for public data). `figma-to-toadshade` has a working
   ancestor in the original project and is the most interesting.
4. **Version the format past 0.1?** Any breaking change to the JSON shape needs
   a version bump and a note in `SPEC.md`.
5. **Tell Simon Willison?** The Apple Notes port is a genuine compliment to the
   Dogsheep design and he might find the reuse argument interesting.

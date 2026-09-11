# Changelog

## 0.1.0 — unreleased

First cut. Extracted from a Drupal Canvas page-import pipeline built in 2026,
generalized into a format with no Drupal in it.

- `SPEC.md` — the format, version 0.1.
- `schema/bundle.schema.json` — JSON Schema for the machine layer.
- `toadshade validate` — structural checks with errors and warnings; optional
  JSON Schema validation when `jsonschema` is installed.
- `toadshade render` — self-contained, double-clickable HTML proof sheet.
  Standard library only.
- `toadshade list` / `toadshade new`.
- `toadshade.importers.Exporter` — the `X-to-toadshade` contract.
- **`apple-notes-to-toadshade`** — the first working exporter. A port of
  Simon Willison's `apple-notes-to-sqlite` that imports his `extract_notes()`
  AppleScript driver unchanged and replaces only his one-line SQLite write
  with a bundle writer. Also reads his `--dump` JSONL from a file or stdin,
  so it works without importing anything.
- Worked example bundle with nested slots, image and non-image assets.

### Fixed along the way

- The default Markdown writer rendered list props as Python reprs
  (`['a', 'b']`, `False`) and put a horizontal rule after every nested child.
- The HTML preview showed scalar lists as a JSON blob; they now render as
  lists, and `checklist` components get checkboxes.

### Known gaps

- No Markdown → JSON round-tripping. The human layer can drift; `validate`
  reports it as a warning and nothing promotes edits back.
- No destination profiles — component type and prop names are whatever the
  destination system calls them.
- One reference importer (Drupal Canvas), one exporter (Apple Notes).
- The Apple Notes exporter does not capture folders — the upstream
  AppleScript does not select them — so bundles are filed by year
  instead of by the note's folder.

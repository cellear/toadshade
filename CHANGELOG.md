# Changelog

## 0.1.0 — unreleased

First cut. Extracted from a Drupal Canvas page-import pipeline built in 2026,
generalized into a format with no Drupal in it.

- `SPEC.md` — the format, version 0.1.
- `schema/bundle.schema.json` — JSON Schema for the machine layer.
- `toadshade validate` — structural checks with errors and warnings; optional
  JSON Schema validation when `jsonschema` is installed.
- `toadshade render` — self-contained, double-clickable HTML preview laid out
  like a plain web page, with a CSS-only **Show structure** switch for
  component numbers, types and ids. Sanitized `body_html`, readable dates.
  Standard library only. (Began as a proof sheet; SPEC §5 amended.)
- `toadshade list` / `toadshade new`.
- `toadshade.importers.Exporter` — the `X-to-toadshade` contract.
- **`apple-notes-to-toadshade`** — the first working exporter. A port of
  Simon Willison's `apple-notes-to-sqlite` that imports his `extract_notes()`
  AppleScript driver unchanged and replaces only his one-line SQLite write
  with a bundle writer. Also reads his `--dump` JSONL from a file or stdin,
  so it works without importing anything.
- **`drupal-to-toadshade`** — Drupal 10/11 nodes, terms and users, read
  straight from the database (MySQL via the `drupal` extra, or SQLite).
  Paragraphs and media embedded, files copied into `assets/`, bundles filed
  by path alias, with `--sections` for sites whose URLs are flat.
- **`backdrop-to-toadshade`** — Backdrop CMS 1.x nodes, terms and users, from
  the database plus the JSON config directory (`--config-dir`, or the
  `config_active` table). MySQL via the `backdrop` extra, or SQLite. Its
  fetch layer yields the Drupal exporter's record shape, so the whole Drupal
  bundle layer is reused; `drupal.py` gained injectable descriptors,
  database and builder classes (no behaviour change).
- **`wordpress-to-toadshade`** — a WordPress WXR export (Tools → Export) into
  bundles, streamed with the standard library. Gutenberg blocks become
  `wp-*` components with their attributes as props, Markdown and HTML bodies,
  and inner blocks in a slot; classic content becomes one text component.
  Featured images and image blocks resolve through attachments and
  `--uploads-dir`. Filed by permalink with the shared
  `base.AliasPlacement` (moved out of the Drupal exporter).
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

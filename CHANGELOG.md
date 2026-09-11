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
- Worked example bundle with nested slots, image and non-image assets.

### Known gaps

- No Markdown → JSON round-tripping. The human layer can drift; `validate`
  reports it as a warning and nothing promotes edits back.
- No destination profiles — component type and prop names are whatever the
  destination system calls them.
- One reference importer (Drupal Canvas), zero exporters.

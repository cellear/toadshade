"""Tests for the apple-notes-to-toadshade exporter.

Everything here runs against a synthetic fixture in the shape
`apple-notes-to-sqlite --dump` emits. No real notes are read, and nothing
here needs macOS.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from toadshade import Bundle, find_bundles, validate_bundle  # noqa: E402
from toadshade.importers.apple_notes import (  # noqa: E402
    AppleNotesToToadshade,
    blocks_to_components,
    note_year,
    parse_body,
    slugify,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "apple-notes-dump.jsonl"


def notes():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


# -- the HTML parser --------------------------------------------------------


def test_headings_and_text_become_blocks():
    blocks = parse_body("<div><h1>Title</h1></div><div>Some prose.</div>")
    assert blocks == [("heading", "Title"), ("text", "Some prose.")]


def test_list_items_are_collected():
    blocks = parse_body("<div><ul><li>one</li><li>two</li></ul></div>")
    assert len(blocks) == 1
    kind, payload = blocks[0]
    assert kind == "list"
    assert payload["items"] == ["one", "two"]
    assert payload["ordered"] is False
    assert payload["checklist"] is False


def test_ordered_and_checklist_are_distinguished():
    assert parse_body("<ol><li>a</li></ol>")[0][1]["ordered"] is True
    assert parse_body('<ul class="checklist"><li>a</li></ul>')[0][1]["checklist"] is True


def test_whitespace_is_normalized():
    blocks = parse_body("<div>lots\n   of\t  space</div>")
    assert blocks == [("text", "lots of space")]


def test_malformed_html_does_not_raise():
    assert isinstance(parse_body("<div><ul><li>unclosed"), list)
    assert parse_body("") == []
    assert parse_body(None) == []


# -- blocks into components -------------------------------------------------


def test_headings_open_sections_and_nest_their_content():
    blocks = parse_body(
        "<h1>One</h1><div>intro</div><ul><li>a</li></ul><h2>Two</h2><div>more</div>"
    )
    assets = {}
    components = blocks_to_components(blocks, assets)

    assert [c["type"] for c in components] == ["section", "section"]
    assert components[0]["props"]["heading"] == "One"
    assert components[0]["props"]["body"] == "intro"
    nested = components[0]["slots"]["content"]
    assert [c["type"] for c in nested] == ["note-list"]
    assert nested[0]["props"]["items"] == ["a"]
    assert components[1]["props"]["body"] == "more"


def test_note_without_headings_still_produces_a_component():
    components = blocks_to_components(parse_body("<div>just prose</div>"), {})
    assert len(components) == 1
    assert components[0]["type"] == "note-text"
    assert components[0]["props"]["body"] == "just prose"


def test_empty_note_produces_one_empty_component():
    components = blocks_to_components([], {})
    assert len(components) == 1 and components[0]["props"]["body"] == ""


def test_data_uri_image_becomes_a_real_asset():
    note = next(n for n in notes() if "base64" in n["body"])
    assets = {}
    components = blocks_to_components(parse_body(note["body"]), assets)

    images = [c for c in components if c["type"] == "note-image"]
    assert len(images) == 1
    ref = images[0]["props"]["image"]
    assert ref["$asset"].startswith("assets/") and ref["$asset"].endswith(".png")
    assert ref["alt"] == "Sketch of the ridge line"
    assert assets[ref["$asset"]].startswith(b"\x89PNG"), "real decoded bytes"


def test_remote_image_stays_a_string_not_a_broken_asset_ref():
    components = blocks_to_components(
        parse_body('<img src="https://example.org/x.jpg" alt="y">'), {}
    )
    image = next(c for c in components if c["type"] == "note-image")
    assert "image" not in image["props"], "must not claim an asset it cannot resolve"
    assert image["props"]["image_url"] == "https://example.org/x.jpg"


# -- slugs and dates --------------------------------------------------------


def test_slugify():
    assert slugify("Trail notes") == "trail-notes"
    assert slugify("  Café — Notes!! ") == "cafe-notes"
    assert slugify("") == "note"
    assert slugify("///") == "note"
    assert len(slugify("x" * 200)) <= 60


def test_note_year_handles_bad_dates():
    assert note_year({"created": "2026-03-14T09:21:00"}) == "2026"
    assert note_year({"created": ""}) == "undated"
    assert note_year({}) == "undated"
    assert note_year({"created": "not a date"}) == "undated"


# -- end to end -------------------------------------------------------------


def test_export_writes_valid_bundles(tmp_path):
    exporter = AppleNotesToToadshade(source=FIXTURE, content_root=tmp_path)
    written = list(exporter.export())
    assert len(written) == 4

    bundles = list(find_bundles(tmp_path))
    assert len(bundles) == 4
    for bundle in bundles:
        report = validate_bundle(bundle)
        assert report.ok, (bundle.path, report.errors)
        assert bundle.html_path.exists(), "preview should be generated"
        assert bundle.md_path.exists(), "human layer should be written"


def test_tree_mirrors_the_url_tree(tmp_path):
    list(AppleNotesToToadshade(source=FIXTURE, content_root=tmp_path).export(render=False))
    for bundle in find_bundles(tmp_path):
        relative = bundle.path.relative_to(tmp_path).as_posix()
        assert bundle.data["alias"] == "/" + relative


def test_duplicate_titles_get_distinct_bundles(tmp_path):
    list(AppleNotesToToadshade(source=FIXTURE, content_root=tmp_path).export(render=False))
    slugs = sorted(b.slug for b in find_bundles(tmp_path))
    assert "trail-notes" in slugs and "trail-notes-2" in slugs


def test_untitled_note_is_titled_and_filed_by_its_own_year(tmp_path):
    list(AppleNotesToToadshade(source=FIXTURE, content_root=tmp_path).export(render=False))
    by_alias = {b.data["alias"]: b for b in find_bundles(tmp_path)}
    untitled = next(b for a, b in by_alias.items() if a.startswith("/notes/2025/"))
    assert untitled.data["title"] == "Untitled note"


def test_provenance_is_recorded(tmp_path):
    list(AppleNotesToToadshade(source=FIXTURE, content_root=tmp_path).export(render=False))
    for bundle in find_bundles(tmp_path):
        meta = bundle.data["meta"]
        assert meta["source"].startswith("apple-notes:x-coredata://")
        assert "created" in meta and "updated" in meta


def test_stop_after(tmp_path):
    exporter = AppleNotesToToadshade(source=FIXTURE, content_root=tmp_path, stop_after=2)
    assert len(list(exporter.export(render=False))) == 2


def test_asset_bytes_land_on_disk(tmp_path):
    list(AppleNotesToToadshade(source=FIXTURE, content_root=tmp_path).export(render=False))
    pngs = list(tmp_path.rglob("assets/*.png"))
    assert len(pngs) == 1
    assert pngs[0].read_bytes().startswith(b"\x89PNG")


def test_cli_reads_stdin(tmp_path, monkeypatch, capsys):
    import io
    from toadshade.importers.apple_notes import main

    monkeypatch.setattr(sys, "stdin", io.StringIO(FIXTURE.read_text()))
    assert main([str(tmp_path), "-"]) == 0
    assert len(list(find_bundles(tmp_path))) == 4

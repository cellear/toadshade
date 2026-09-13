"""Tests for the drupal-to-toadshade exporter.

Everything here runs against `fixtures/drupal10.sql`, a synthetic SQLite
database shaped like Drupal 10's SQL storage (see `fixtures/make_drupal10.py`).
No MySQL, no PyMySQL, and no real site are needed.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from toadshade import Bundle, find_bundles, validate_bundle  # noqa: E402
from toadshade.importers.drupal import (  # noqa: E402
    DrupalDatabase,
    DrupalToToadshade,
    dedicated_table_name,
    html_to_markdown,
    main,
    php_unserialize,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SQL = FIXTURES / "drupal10.sql"
FILES = FIXTURES / "drupal-files"


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQL.read_text(encoding="utf-8"))
    yield conn
    conn.close()


@pytest.fixture
def root(tmp_path, connection):
    content = tmp_path / "content"
    list(DrupalToToadshade(connection, content, files_dir=FILES).export())
    return content


def walk(component):
    yield component
    for children in (component.get("slots") or {}).values():
        for child in children:
            yield from walk(child)


def page(root, relative) -> dict:
    return Bundle.load(root / relative).data["components"][0]


# -- PHP serialize ----------------------------------------------------------


def test_php_unserialize_reads_drupal_config_shapes():
    assert php_unserialize('a:2:{s:4:"type";s:6:"string";s:11:"cardinality";i:-1;}') == {
        "type": "string", "cardinality": -1,
    }
    assert php_unserialize('a:2:{i:0;s:1:"a";i:1;s:1:"b";}') == ["a", "b"]
    assert php_unserialize("b:1;") is True
    assert php_unserialize("N;") is None
    assert php_unserialize("d:0.5;") == 0.5
    assert php_unserialize(b'O:8:"stdClass":1:{s:1:"x";a:0:{}}') == {"x": []}


def test_php_string_lengths_are_bytes():
    assert php_unserialize('s:5:"Café";') == "Café"
    assert php_unserialize('s:4:"a";b";') == 'a";b'


# -- HTML to Markdown -------------------------------------------------------


def test_links_and_emphasis_survive():
    html = '<p>See <a href="/visit/map">the trail map</a> for <strong>closures</strong>.</p>'
    assert html_to_markdown(html) == "See [the trail map](/visit/map) for **closures**."


def test_headings_lists_and_paragraphs():
    html = "<h2>Hours</h2><ul><li>Dawn</li><li>Dusk</li></ul><p>Closed <em>never</em>.</p>"
    assert html_to_markdown(html) == "## Hours\n\n- Dawn\n- Dusk\n\nClosed *never*."


def test_nested_ordered_lists():
    html = "<ol><li>One<ul><li>a</li></ul></li><li>Two</li></ol>"
    assert html_to_markdown(html) == "1. One\n  - a\n2. Two"


def test_images_go_through_the_callback():
    html = '<img src="/x.png" alt="X">'
    assert html_to_markdown(html, image=lambda src, alt: "assets/x.png") == "![X](assets/x.png)"


def test_bad_markup_and_scripts():
    assert html_to_markdown("<p>unclosed <strong>bold") == "unclosed **bold"
    assert html_to_markdown("<p>ok</p><script>alert(1)</script>") == "ok"
    assert html_to_markdown(None) == ""


# -- reading the database ---------------------------------------------------


def test_dedicated_table_names():
    assert dedicated_table_name("node", "body") == "node__body"
    long_name = dedicated_table_name("a_rather_long_custom_entity_type_name", "field_even_longer", "uuid-1")
    assert long_name.startswith("a_rather_long_custom_entity_type_n__")
    assert len(long_name) == 34 + 2 + 10


def test_fields_follow_form_display_order(connection):
    names = [d["name"] for d in DrupalDatabase(connection).field_definitions("node", "article")]
    assert names == [
        "field_subtitle", "body", "field_image", "field_sections", "field_tags",
        "field_link", "field_attachment", "field_media", "field_rating",
        "field_featured",  # hidden on the form, so last
    ]


def test_default_language_only_and_no_anonymous_user(connection):
    db = DrupalDatabase(connection)
    nodes = list(db.fetch_entities("node"))
    assert [n["id"] for n in nodes] == [1, 2, 3, 4, 5, 6, 7]
    assert [u["id"] for u in db.fetch_entities("user")] == [1, 2]

    article = next(n for n in nodes if n["id"] == 4)
    fields = {f["name"]: f["values"] for f in article["fields"]}
    assert len(fields["body"]) == 1 and "español" not in fields["body"][0]["value"]
    assert fields["field_subtitle"] == [{"value": "Week of March 14"}], "deleted rows ignored"


# -- the export -------------------------------------------------------------


def test_every_bundle_is_valid(root):
    bundles = list(find_bundles(root))
    assert len(bundles) == 12
    for bundle in bundles:
        report = validate_bundle(bundle)
        assert report.ok, (bundle.path, report.errors)
        assert bundle.md_path.exists() and bundle.html_path.exists()


def test_tree_follows_aliases_without_nesting_bundles(root):
    placed = {b.path.relative_to(root).as_posix(): b.data["alias"] for b in find_bundles(root)}
    assert placed == {
        "home": "/",                                   # front page
        "about/about": "/about",                       # a parent of /about/team
        "about/team": "/about/team",
        "privacy-policy": "/privacy-policy",           # no sections configured
        "news/spring-bloom-report": "/news/spring-bloom-report",
        "news/spring-bloom-report-2": "/news/Spring-Bloom-Report",
        "node/5": "/node/5",                           # disabled alias ignored
        "tags/ferns": "/tags/ferns",
        "tags/wildflowers": "/tags/wildflowers",
        "taxonomy/term/3": "/taxonomy/term/3",
        "user/1": "/user/1",
        "staff/ranger-jo": "/staff/ranger-jo",
    }


def test_body_keeps_markdown_and_original_html(root):
    article = page(root, "news/spring-bloom-report")
    text = article["slots"]["body"][0]
    assert text["type"] == "text" and text["label"] == "Body"
    assert text["props"]["body"] == (
        "See [the trail map](/visit/map) for **closures**.\n\n- Trilliums\n- Mayapples"
    )
    assert text["props"]["body_html"].startswith('<p>See <a href="/visit/map">')
    assert text["props"]["format"] == "basic_html"
    assert text["props"]["summary"] == "What is flowering this week."


def test_markdown_layer_leaves_out_html(root):
    markdown = (root / "news/spring-bloom-report/spring-bloom-report.md").read_text()
    assert "[the trail map](/visit/map)" in markdown
    assert "<p>See" not in markdown and "basic_html" not in markdown
    assert "## 1. Spring Bloom Report" in markdown
    assert "### 1.1. Body" in markdown


def test_inline_images_are_copied_from_style_urls(root):
    team = page(root, "about/team")
    text = team["slots"]["body"][0]
    assert "![Creek crossing](assets/creek.png)" in text["props"]["body"]
    assert "(/sites/default/files/2026-03/not-there.png)" in text["props"]["body"]
    assert "alert" not in text["props"]["body"]
    image = text["slots"]["images"][0]
    assert image["props"]["image"] == {"$asset": "assets/creek.png", "alt": "Creek crossing"}
    assert (root / "about/team/assets/creek.png").read_bytes().startswith(b"\x89PNG")


def test_paragraphs_nest_as_slots(root):
    sections = page(root, "news/spring-bloom-report")["slots"]["field_sections"]
    assert [s["type"] for s in sections] == ["paragraph-text_block", "paragraph-card_group"]
    assert sections[0]["props"] == {"field_heading": "What's blooming"}
    assert sections[0]["slots"]["field_text"][0]["props"]["body"] == (
        "### Along the creek\n\nLook for *white* petals."
    )
    card = sections[1]["slots"]["field_cards"][0]
    assert card["type"] == "paragraph-card"
    assert card["slots"]["field_card_image"][0]["props"]["image"]["$asset"] == "assets/trailhead.png"


def test_media_is_embedded(root):
    media = page(root, "news/spring-bloom-report")["slots"]["field_media"][0]
    assert media["type"] == "media-image"
    assert media["props"]["title"] == "Boardwalk photo"
    assert "uid" not in media["props"]
    assert "thumbnail" not in media["props"], "the thumbnail is the image itself"
    image = media["slots"]["field_media_image"][0]["props"]["image"]
    assert image == {"$asset": "assets/boardwalk.png", "alt": "Boardwalk through the swamp"}


def test_remote_video_keeps_its_thumbnail(root):
    media = page(root, "news/spring-bloom-report-2")["slots"]["field_media"][0]
    assert media["type"] == "media-remote_video"
    assert media["props"]["thumbnail"] == {
        "$asset": "assets/walkthrough.png", "alt": "Boardwalk walkthrough",
    }
    assert media["props"]["field_media_oembed_video"].startswith("https://video.example.com/")
    assert (root / "news/spring-bloom-report-2/assets/walkthrough.png").exists()


def test_missing_file_is_a_string_not_a_broken_reference(root):
    attachment = page(root, "news/spring-bloom-report")["slots"]["field_attachment"][0]
    assert attachment["props"] == {
        "file_url": "public://docs/trail-map.pdf", "title": "Printable trail map",
    }


def test_a_file_used_twice_is_copied_once(root):
    assets = sorted(p.name for p in (root / "news/spring-bloom-report/assets").iterdir())
    assert assets == ["boardwalk.png", "trailhead.png"]


def test_references_become_urls(root):
    article = page(root, "news/spring-bloom-report")
    props = article["props"]
    assert props["field_tags"] == ["/tags/ferns", "/taxonomy/term/3"]
    assert props["field_link"] == "About us → /about"
    assert props["field_featured"] is True
    assert props["field_rating"] == [4, 5]
    assert props["field_subtitle"] == "Week of March 14"

    assert page(root, "taxonomy/term/3")["props"]["parent"] == ["/tags/wildflowers"]
    ferns = page(root, "tags/ferns")
    assert "parent" not in ferns["props"], "parent 0 means no parent"
    assert ferns["slots"]["description"][0]["props"]["body"] == "Shade-loving *non-flowering* plants."


def test_user_data_is_kept_in_meta(root):
    admin = Bundle.load(root / "user/1").data
    assert admin["meta"]["mail"] == "admin@example.com"
    assert admin["meta"]["pass"].startswith("$2y$")
    assert "pass" not in admin["components"][0]["props"], "account data is not page content"
    assert admin["components"][0]["props"]["roles"] == ["administrator"]
    ranger = Bundle.load(root / "staff/ranger-jo").data
    assert "access" not in ranger["meta"], "a zero timestamp means never"
    picture = ranger["components"][0]["slots"]["user_picture"][0]["props"]["image"]
    assert picture["$asset"] == "assets/ranger.png"


def test_drupal_bookkeeping_goes_to_meta_not_the_page(root):
    data = Bundle.load(root / "news/spring-bloom-report").data
    assert not {"uid", "promote", "sticky"} & set(data["components"][0]["props"])
    assert data["meta"]["uid"] == "/staff/ranger-jo"
    assert data["meta"]["promote"] == 1 and data["meta"]["sticky"] == 0


def test_sections_file_flat_urls_under_a_folder(tmp_path, connection):
    root = tmp_path / "content"
    exporter = DrupalToToadshade(connection, root, files_dir=FILES, sections={"page": "pages"})
    list(exporter.export(render=False))
    placed = {b.path.relative_to(root).as_posix() for b in find_bundles(root)}
    assert "pages/privacy-policy" in placed               # one-segment alias: moved
    assert "home" in placed                               # the front page stays put
    assert {"about/about", "about/team"} <= placed        # real hierarchy wins
    assert "news/spring-bloom-report" in placed           # other bundles untouched
    assert Bundle.load(root / "pages/privacy-policy").data["alias"] == "/privacy-policy"


def test_meta_records_provenance_and_status(root):
    article = Bundle.load(root / "news/spring-bloom-report").data["meta"]
    assert article["source"] == "drupal:node/4"
    assert article["uuid"] == "node-4" and article["revision_id"] == 14
    assert article["created"].startswith("2026-03-14T")
    assert Bundle.load(root / "node/5").data["meta"]["status"] == 0


def test_smartdate_timestamps_become_iso():
    from toadshade.importers.drupal import _BundleBuilder

    value = {"value": 1771552800, "end_value": 1771556400, "duration": 60,
             "rrule": None, "rrule_index": None, "timezone": None}
    assert _BundleBuilder.scalar("smartdate", value) == {
        "value": "2026-02-20T02:00:00+00:00", "end_value": "2026-02-20T03:00:00+00:00", "duration": 60,
    }


def test_component_ids_are_unique_per_bundle(root):
    for bundle in find_bundles(root):
        ids = [c["id"] for c in walk(bundle.data["components"][0])]
        assert len(ids) == len(set(ids))


def test_filters_and_stop_after(tmp_path, connection):
    pages = DrupalToToadshade(connection, tmp_path / "a", files_dir=FILES,
                              entity_types=["node"], bundles=["page"])
    assert len(list(pages.export(render=False))) == 4
    limited = DrupalToToadshade(connection, tmp_path / "b", files_dir=FILES, stop_after=2)
    assert len(list(limited.export(render=False))) == 2


def test_without_files_dir_nothing_dangles(tmp_path, connection):
    root = tmp_path / "content"
    list(DrupalToToadshade(connection, root).export(render=False))
    for bundle in find_bundles(root):
        assert validate_bundle(bundle).ok
    image = page(root, "news/spring-bloom-report")["slots"]["field_image"][0]["props"]
    assert image["image_url"] == "public://2026-03/trailhead.png"


def test_cli_reads_a_sqlite_url(tmp_path, connection, capsys):
    database = tmp_path / "site.db"
    disk = sqlite3.connect(database)
    connection.backup(disk)
    disk.close()

    root = tmp_path / "content"
    assert main([str(root), "--db", f"sqlite:///{database}", "--files-dir", str(FILES),
                 "--sections", "page=pages", "--no-render"]) == 0
    assert len(list(find_bundles(root))) == 12
    assert (root / "pages/privacy-policy/privacy-policy.json").exists()

    with pytest.raises(SystemExit):
        main([str(root), "--db", f"sqlite:///{database}", "--entity-types", "paragraph"])

"""Tests for the backdrop-to-toadshade exporter.

Everything here runs against `fixtures/backdrop1.sql`, a synthetic SQLite
database shaped like Backdrop CMS 1.x's SQL storage, and
`fixtures/backdrop-config/`, its active config directory (see
`fixtures/make_backdrop1.py`). No MySQL, no PyMySQL, and no real site.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from toadshade import Bundle, find_bundles, validate_bundle  # noqa: E402
from toadshade.importers.backdrop import (  # noqa: E402
    BackdropDatabase,
    BackdropToToadshade,
    _BackdropBundleBuilder,
    main,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SQL = FIXTURES / "backdrop1.sql"
CONFIG = FIXTURES / "backdrop-config"
FILES = FIXTURES / "backdrop-files"


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQL.read_text(encoding="utf-8"))
    yield conn
    conn.close()


@pytest.fixture
def db(connection):
    return BackdropDatabase(connection, CONFIG)


@pytest.fixture
def root(tmp_path, connection):
    content = tmp_path / "content"
    list(BackdropToToadshade(connection, content, config_dir=CONFIG, files_dir=FILES).export())
    return content


def walk(component):
    yield component
    for children in (component.get("slots") or {}).values():
        for child in children:
            yield from walk(child)


def page(root, relative) -> dict:
    return Bundle.load(root / relative).data["components"][0]


# -- reading the config directory -------------------------------------------


def test_fields_follow_widget_weight_and_skip_deleted_instances(db):
    names = [d["name"] for d in db.field_definitions("node", "post")]
    assert names == [
        "field_image", "body", "field_tags", "field_attachment", "field_link",
        "field_related", "field_rating", "field_event_date", "field_featured", "field_contact",
    ]
    assert [d["name"] for d in db.field_definitions("node", "page")] == ["body"], \
        "field_old_notes is deleted"


def test_backdrop_field_types_map_to_the_drupal_record_shape(db):
    by_name = {d["name"]: d for d in db.field_definitions("node", "post")}
    assert by_name["field_tags"]["type"] == "entity_reference"
    assert by_name["field_tags"]["target_type"] == "taxonomy_term"
    assert by_name["field_tags"]["storage_type"] == "taxonomy_term_reference"
    assert by_name["field_related"]["target_type"] == "node"
    assert by_name["field_link"]["type"] == "link"
    assert by_name["field_featured"]["type"] == "boolean"
    assert by_name["field_rating"]["cardinality"] == -1, "string cardinality from JSON is an int"
    assert by_name["body"]["table"] == "field_data_body"


def test_front_page_and_aliases(db):
    assert db.front_page() == "/node/1"
    assert db.alias_for("/node/3") == "/about/team"
    assert db.alias_for("/node/8", "en") == "/fern-walk", "the newest alias wins"


def test_config_can_come_from_the_config_active_table(connection):
    connection.execute("CREATE TABLE config_active (name TEXT, data TEXT, changed INTEGER)")
    for path in CONFIG.glob("*.json"):
        connection.execute("INSERT INTO config_active VALUES (?, ?, 0)",
                           (path.stem, path.read_text(encoding="utf-8")))
    db = BackdropDatabase(connection)  # no config dir
    assert db.front_page() == "/node/1"
    assert [d["name"] for d in db.field_definitions("node", "page")] == ["body"]


# -- reading the database ---------------------------------------------------


def test_current_revision_default_language_no_translations_no_anonymous(db):
    nodes = list(db.fetch_entities("node"))
    assert [n["id"] for n in nodes] == [1, 2, 3, 4, 5, 7, 8], "node 6 is a translation of 4"
    assert [u["id"] for u in db.fetch_entities("user")] == [1, 2]

    report = next(n for n in nodes if n["id"] == 4)
    assert report["label"] == "Spring Bloom Report", "not the older revision's title"
    assert report["revision_id"] == 14
    fields = {f["name"]: f["values"] for f in report["fields"]}
    assert len(fields["body"]) == 1
    assert "español" not in fields["body"][0]["value"] and "Early draft" not in fields["body"][0]["value"]
    assert [v["target_id"] for v in fields["field_tags"]] == [1, 3], "deleted row ignored"


def test_records_have_the_drupal_record_shape(db):
    report = next(n for n in db.fetch_entities("node") if n["id"] == 4)
    assert set(report) == {
        "entity_type", "bundle", "id", "revision_id", "uuid", "langcode", "label", "path",
        "alias", "front", "base", "base_references", "base_files", "fields",
    }
    image = next(f for f in report["fields"] if f["name"] == "field_image")["values"][0]
    assert image["target_id"] == 1 and image["file"]["uri"] == "public://2026-03/trailhead.png"
    link = next(f for f in report["fields"] if f["name"] == "field_link")["values"][0]
    assert link["uri"] == "node/2" and link["target"]["alias"] == "/about"


def test_field_rows_are_per_entity_type(connection):
    # field_data_* tables are shared by every entity type; a user with the
    # same id as a node must not pick up the node's values.
    connection.execute(
        "INSERT INTO field_data_field_bio VALUES ('node', 'post', 0, 2, 2, 'und', 0, 'Wrong', 'plain_text')"
    )
    db = BackdropDatabase(connection, CONFIG)
    ranger = next(u for u in db.fetch_entities("user") if u["id"] == 2)
    bio = next(f for f in ranger["fields"] if f["name"] == "field_bio")["values"]
    assert [v["value"] for v in bio] == ["<p>Jo has walked every trail twice.</p>"]


# -- the export -------------------------------------------------------------


def test_every_bundle_is_valid(root):
    bundles = list(find_bundles(root))
    assert len(bundles) == 12
    for bundle in bundles:
        report = validate_bundle(bundle)
        assert report.ok, (bundle.path, report.errors)
        assert not report.warnings, (bundle.path, report.warnings)
        assert bundle.md_path.exists() and bundle.html_path.exists()


def test_tree_follows_aliases_without_nesting_bundles(root):
    placed = {b.path.relative_to(root).as_posix(): b.data["alias"] for b in find_bundles(root)}
    assert placed == {
        "home": "/",                                     # site_frontpage node/1
        "about/about": "/about",                         # a parent of /about/team
        "about/team": "/about/team",                     # hierarchical alias
        "blog/spring-bloom-report": "/blog/spring-bloom-report",
        "node/5": "/node/5",                             # no alias
        "privacy-policy": "/privacy-policy",             # flat alias
        "fern-walk": "/fern-walk",                       # flat, newest alias
        "tags/ferns": "/tags/ferns",
        "tags/wildflowers": "/tags/wildflowers",
        "taxonomy/term/3": "/taxonomy/term/3",
        "user/1": "/user/1",
        "staff/ranger-jo": "/staff/ranger-jo",
    }


def test_sections_file_flat_urls_under_a_folder(tmp_path, connection):
    root = tmp_path / "content"
    exporter = BackdropToToadshade(connection, root, config_dir=CONFIG, files_dir=FILES,
                                   sections={"post": "blog", "node.page": "pages"})
    list(exporter.export(render=False))
    placed = {b.path.relative_to(root).as_posix() for b in find_bundles(root)}
    assert "blog/fern-walk" in placed
    assert "blog/spring-bloom-report" in placed          # already under /blog
    assert "pages/privacy-policy" in placed
    assert {"home", "about/about", "about/team"} <= placed
    assert Bundle.load(root / "blog/fern-walk").data["alias"] == "/fern-walk"


def test_body_keeps_markdown_and_original_html(root):
    text = page(root, "blog/spring-bloom-report")["slots"]["body"][0]
    assert text["type"] == "text" and text["label"] == "Body"
    assert text["props"]["body"] == (
        "See [the trail map](/visit/map) for **closures**.\n\n- Trilliums\n- Mayapples"
    )
    assert text["props"]["body_html"].startswith('<p>See <a href="/visit/map">')
    assert text["props"]["format"] == "filtered_html"
    assert text["props"]["summary"] == "What is flowering this week."


def test_markdown_layer_leaves_out_html(root):
    markdown = (root / "blog/spring-bloom-report/spring-bloom-report.md").read_text()
    assert "[the trail map](/visit/map)" in markdown
    assert "<p>See" not in markdown and "filtered_html" not in markdown
    assert "## 1. Spring Bloom Report" in markdown


def test_inline_images_are_copied_from_backdrop_style_urls(root):
    text = page(root, "about/team")["slots"]["body"][0]
    assert "![Creek crossing](assets/creek.png)" in text["props"]["body"]
    assert "(/files/2026-03/not-there.png)" in text["props"]["body"]
    assert text["slots"]["images"][0]["props"]["image"] == {
        "$asset": "assets/creek.png", "alt": "Creek crossing",
    }
    assert (root / "about/team/assets/creek.png").read_bytes().startswith(b"\x89PNG")


def test_image_and_missing_file(root):
    slots = page(root, "blog/spring-bloom-report")["slots"]
    assert slots["field_image"][0]["props"]["image"] == {
        "$asset": "assets/trailhead.png", "alt": "Trailhead sign", "title": "North trailhead",
    }
    assert slots["field_attachment"][0]["props"] == {
        "file_url": "public://docs/trail-map.pdf", "title": "Printable trail map",
    }


def test_references_links_and_scalars(root):
    props = page(root, "blog/spring-bloom-report")["props"]
    assert props["field_tags"] == ["/tags/ferns", "/taxonomy/term/3"]
    assert props["field_link"] == "About us → /about"
    assert props["field_related"] == "/fern-walk"
    assert props["field_rating"] == [4, 5]
    assert props["field_featured"] is True
    assert props["field_contact"] == "rangers@example.org"
    assert props["field_event_date"] == {
        "value": "2026-04-18T14:00:00", "end_value": "2026-04-18T16:00:00", "timezone": "UTC",
    }

    walk_props = page(root, "fern-walk")["props"]
    assert walk_props["field_link"] == "Trail notes → https://trails.example.com/ferns"
    assert walk_props["field_link_options"] == {"target": "_blank"}


def test_terms_have_descriptions_and_parents(root):
    ferns = page(root, "tags/ferns")
    assert ferns["type"] == "taxonomy_term-tags"
    assert "parent" not in ferns["props"], "parent 0 means no parent"
    assert ferns["slots"]["description"][0]["props"]["body"] == "Shade-loving *non-flowering* plants."
    assert page(root, "taxonomy/term/3")["props"]["parent"] == ["/tags/wildflowers"]
    assert "description" not in page(root, "tags/wildflowers").get("slots", {}), "empty text skipped"


def test_users_picture_signature_roles_and_account_meta(root):
    ranger = Bundle.load(root / "staff/ranger-jo").data
    component = ranger["components"][0]
    assert component["props"]["picture"] == {"$asset": "assets/ranger.png", "alt": "ranger-jo"}
    assert component["props"]["roles"] == ["editor"]
    assert component["slots"]["signature"][0]["props"]["body"] == "Ranger *Jo*"
    assert component["slots"]["field_bio"][0]["label"] == "Biography"
    assert ranger["meta"]["mail"] == "jo@example.org"
    assert "access" not in ranger["meta"], "a zero timestamp means never"
    assert not {"pass", "mail", "language", "data"} & set(component["props"])

    admin = Bundle.load(root / "user/1").data
    assert "picture" not in admin["components"][0]["props"], "picture 0 means none"
    assert admin["meta"]["data"] == {"contact": 1}, "serialized user data is decoded"
    assert admin["meta"]["pass"].startswith("$S$")


def test_meta_records_provenance_and_status(root):
    meta = Bundle.load(root / "blog/spring-bloom-report").data["meta"]
    assert meta["source"] == "backdrop:node/4"
    assert meta["generator"].startswith("backdrop-to-toadshade ")
    assert meta["revision_id"] == 14 and meta["tnid"] == 4
    assert meta["uid"] == "/staff/ranger-jo"
    assert "uuid" not in meta, "Backdrop core has no uuid columns"
    assert not {"uid", "promote", "sticky", "comment", "tnid"} & set(page(root, "blog/spring-bloom-report")["props"])
    assert Bundle.load(root / "node/5").data["meta"]["status"] == 0


def test_date_scalars():
    assert _BackdropBundleBuilder.scalar("datestamp", {"value": 1776520800, "end_value": None}) == \
        "2026-04-18T14:00:00+00:00"
    assert _BackdropBundleBuilder.scalar("date", {"value": "2026-04-18T14:00:00", "end_value": ""}) == \
        "2026-04-18T14:00:00"
    assert _BackdropBundleBuilder.scalar("number_integer", {"value": 3}) == 3


def test_component_ids_are_unique_per_bundle(root):
    for bundle in find_bundles(root):
        ids = [c["id"] for c in walk(bundle.data["components"][0])]
        assert len(ids) == len(set(ids))


def test_filters_and_stop_after(tmp_path, connection):
    posts = BackdropToToadshade(connection, tmp_path / "a", config_dir=CONFIG, files_dir=FILES,
                                entity_types=["node"], bundles=["post"])
    assert len(list(posts.export(render=False))) == 3
    limited = BackdropToToadshade(connection, tmp_path / "b", config_dir=CONFIG, stop_after=2)
    assert len(list(limited.export(render=False))) == 2


def test_without_files_dir_nothing_dangles(tmp_path, connection):
    root = tmp_path / "content"
    list(BackdropToToadshade(connection, root, config_dir=CONFIG).export(render=False))
    for bundle in find_bundles(root):
        assert validate_bundle(bundle).ok
    image = page(root, "blog/spring-bloom-report")["slots"]["field_image"][0]["props"]
    assert image["image_url"] == "public://2026-03/trailhead.png"


def test_table_prefix(tmp_path):
    import re

    # The same dump, with every table renamed as a `$database['prefix']` would.
    script = SQL.read_text(encoding="utf-8")
    prefixed = re.sub(r'(CREATE TABLE |INSERT INTO )"?([a-z_]+)"?', r'\1"bd_\2"', script)
    conn = sqlite3.connect(":memory:")
    conn.executescript(prefixed)
    root = tmp_path / "content"
    exported = list(BackdropToToadshade(conn, root, config_dir=CONFIG, files_dir=FILES,
                                        prefix="bd_").export(render=False))
    assert len(exported) == 12


def test_cli_reads_a_sqlite_url(tmp_path, connection, capsys):
    database = tmp_path / "site.db"
    disk = sqlite3.connect(database)
    connection.backup(disk)
    disk.close()

    root = tmp_path / "content"
    assert main([str(root), "--db", f"sqlite:///{database}", "--config-dir", str(CONFIG),
                 "--files-dir", str(FILES), "--sections", "post=blog", "--no-render"]) == 0
    assert len(list(find_bundles(root))) == 12
    data = json.loads((root / "blog/fern-walk/fern-walk.json").read_text())
    assert data["alias"] == "/fern-walk"

    with pytest.raises(SystemExit):
        main([str(tmp_path / "x"), "--db", f"sqlite:///{database}", "--entity-types", "paragraph"])
    with pytest.raises(SystemExit):  # no config dir and no config_active table
        main([str(tmp_path / "y"), "--db", f"sqlite:///{database}"])

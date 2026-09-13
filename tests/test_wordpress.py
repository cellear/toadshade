"""Tests for the wordpress-to-toadshade exporter.

Everything here runs against `fixtures/wordpress.wxr.xml`, a synthetic
WordPress export, and `fixtures/wordpress-uploads/` (see
`fixtures/make_wordpress_wxr.py`). No WordPress, no network.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from toadshade import Bundle, find_bundles, validate_bundle  # noqa: E402
from toadshade.importers.wordpress import (  # noqa: E402
    WordPressToToadshade,
    WxrFile,
    autop,
    block_type,
    main,
    parse_blocks,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WXR = FIXTURES / "wordpress.wxr.xml"
UPLOADS = FIXTURES / "wordpress-uploads"


@pytest.fixture
def root(tmp_path):
    content = tmp_path / "content"
    list(WordPressToToadshade(WXR, content, uploads_dir=UPLOADS, sections={"post": "blog"}).export())
    return content


def walk(component):
    yield component
    for children in (component.get("slots") or {}).values():
        for child in children:
            yield from walk(child)


def bundle(root, relative) -> dict:
    return Bundle.load(root / relative).data


# -- block grammar ----------------------------------------------------------


def test_parse_blocks_nested_void_and_namespaced():
    doc = (
        '<!-- wp:columns --><div><!-- wp:column {"width":"33%"} --><p>a</p><!-- /wp:column -->'
        "</div><!-- /wp:columns -->\n\n"
        '<!-- wp:latest-posts {"postsToShow":3} /-->'
        '<!-- wp:jetpack/markdown {"source":"x \\u002d\\u002d> y"} --><p>x</p><!-- /wp:jetpack/markdown -->'
    )
    blocks = parse_blocks(doc)
    names = [b["name"] for b in blocks]
    assert names == ["core/columns", None, "core/latest-posts", "jetpack/markdown"]
    columns = blocks[0]
    assert columns["inner_content"] == ["<div>", None, "</div>"]
    assert columns["inner_blocks"][0]["attrs"] == {"width": "33%"}
    assert columns["inner_blocks"][0]["inner_html"] == "<p>a</p>"
    assert blocks[2]["attrs"] == {"postsToShow": 3} and blocks[2]["inner_html"] == ""
    assert blocks[3]["attrs"] == {"source": "x --> y"}


def test_parse_blocks_classic_and_broken_markup():
    assert parse_blocks("<p>Just HTML</p>") == [{
        "name": None, "attrs": {}, "inner_blocks": [], "inner_html": "<p>Just HTML</p>",
        "inner_content": ["<p>Just HTML</p>"],
    }]
    unclosed = parse_blocks("<!-- wp:group --><p>never closed</p>")
    assert unclosed[0]["name"] == "core/group" and unclosed[0]["inner_html"] == "<p>never closed</p>"
    stray = parse_blocks("<p>a</p><!-- /wp:paragraph -->")
    assert [b["name"] for b in stray] == [None]
    bad_json = parse_blocks('<!-- wp:paragraph {"oops": } --><p>x</p><!-- /wp:paragraph -->')
    assert "_unparsed_attributes" in bad_json[0]["attrs"]


def test_block_types():
    assert block_type("core/paragraph") == "wp-paragraph"
    assert block_type("jetpack/markdown") == "wp-jetpack/markdown"


def test_autop():
    assert autop("One\n\nTwo\nlines\n\n<h2>Head</h2>") == "<p>One</p>\n<p>Two<br>\nlines</p>\n<h2>Head</h2>"


# -- reading the export -----------------------------------------------------


def test_wxr_items_are_plain_dicts_with_cdata_and_postmeta():
    wxr = WxrFile(WXR)
    items = {i["post_id"]: i for i in wxr.items()}
    assert len(items) == 14
    report = items[20]
    assert report["title"] == "Spring Bloom Report"
    assert report["post_type"] == "post" and report["status"] == "publish"
    assert report["creator"] == "ranger-jo"
    assert report["content"].startswith("<!-- wp:paragraph -->")
    assert report["excerpt"] == "What is flowering this week."
    assert report["postmeta"]["_thumbnail_id"] == "12"
    assert report["postmeta"]["ratings"] == [4, 5], "serialized postmeta is decoded"
    assert "akismet_result" not in report["postmeta"], "commentmeta is not postmeta"
    assert report["comment_count"] == 1
    assert {"domain": "post_tag", "nicename": "ferns", "name": "Ferns"} in report["categories"]
    assert wxr.site["base_blog_url"] == "https://woods.example.com"
    assert wxr.site["authors"]["ranger-jo"]["display_name"] == "Ranger Jo"
    assert wxr.site["categories"]["field-notes"]["parent"] == "news"


def test_invalid_control_characters_do_not_stop_the_export(tmp_path):
    dirty = tmp_path / "dirty.xml"
    dirty.write_bytes(WXR.read_bytes().replace(b"Dawn to dusk", b"Dawn\x0b to dusk"))
    items = {i["post_id"]: i for i in WxrFile(dirty).items()}
    assert "Dawn to dusk" in items[2]["content"]


# -- the export -------------------------------------------------------------


def test_every_bundle_is_valid(root):
    bundles = list(find_bundles(root))
    assert len(bundles) == 7
    for b in bundles:
        report = validate_bundle(b)
        assert report.ok, (b.path, report.errors)
        assert not report.warnings, (b.path, report.warnings)
        assert b.md_path.exists() and b.html_path.exists()


def test_tree_follows_permalinks_and_sections(root):
    placed = {b.path.relative_to(root).as_posix(): b.data["alias"] for b in find_bundles(root)}
    assert placed == {
        "about/about": "/about",                          # a parent page moves down
        "about/team": "/about/team",                      # nested with post_parent
        "privacy-policy": "/privacy-policy",              # pages are not in the section
        "blog/spring-bloom-report": "/spring-bloom-report",
        "blog/fern-walk": "/fern-walk",                   # private, still exported
        "post/22": "/post/22",                            # draft with ?p=22 and no name
        "trails/creek-loop": "/trails/creek-loop",        # custom post type
    }


def test_internal_types_and_junk_statuses_are_skipped(root):
    sources = {b.data["meta"]["post_id"] for b in find_bundles(root)}
    assert sources == {2, 3, 4, 20, 21, 22, 50}   # no attachment, revision, menu item,
                                                  # reusable block or auto-draft


def test_without_sections_flat_posts_stay_at_the_top(tmp_path):
    root = tmp_path / "content"
    list(WordPressToToadshade(WXR, root, uploads_dir=UPLOADS).export(render=False))
    assert (root / "spring-bloom-report/spring-bloom-report.json").exists()


def test_plain_permalinks_rebuild_the_page_tree(tmp_path):
    plain = tmp_path / "plain.xml"
    text = WXR.read_text(encoding="utf-8")
    for post_id, path in [(2, "about/"), (3, "about/team/")]:
        text = text.replace(f"<link>https://woods.example.com/{path}</link>",
                            f"<link>https://woods.example.com/?page_id={post_id}</link>")
    plain.write_text(text, encoding="utf-8")
    root = tmp_path / "content"
    list(WordPressToToadshade(plain, root, uploads_dir=UPLOADS).export(render=False))
    assert bundle(root, "about/team")["alias"] == "/about/team"
    assert bundle(root, "about/about")["alias"] == "/about"


def test_entry_component_has_terms_excerpt_and_featured_image(root):
    data = bundle(root, "blog/spring-bloom-report")
    entry = data["components"][0]
    assert entry["type"] == "post"
    assert entry["props"]["excerpt"] == "What is flowering this week."
    assert entry["props"]["categories"] == ["News → /category/news",
                                            "Field Notes → /category/news/field-notes"]
    assert entry["props"]["tags"] == ["Ferns → /tag/ferns", "Trilliums → /tag/trilliums"]
    assert entry["props"]["trail"] == ["Creek Loop"], "custom taxonomy: no URL to guess"
    assert entry["props"]["featured_image"] == {"$asset": "assets/creek.png",
                                                "alt": "Creek crossing at dawn"}
    assert (root / "blog/spring-bloom-report/assets/creek.png").read_bytes().startswith(b"\x89PNG")


def test_blocks_become_components_with_props_body_and_slots(root):
    components = bundle(root, "blog/spring-bloom-report")["components"]
    assert [c["type"] for c in components] == [
        "post", "wp-paragraph", "wp-list", "wp-quote", "wp-block", "wp-jetpack/markdown",
    ]
    paragraph = components[1]["props"]
    assert paragraph["body"] == "See [the trail map](https://woods.example.com/visit/map/) for **closures**."
    assert paragraph["body_html"].startswith("<p>See <a href=")
    items = components[2]["slots"]["inner_blocks"]
    assert [i["props"]["body"] for i in items] == ["- Trilliums", "- Mayapples"]
    assert "body" not in components[2]["props"], "a list's own HTML is only its wrapper"
    quote = components[3]
    assert quote["slots"]["inner_blocks"][0]["props"]["body"] == "The best week of the year."
    reusable = components[4]
    assert reusable["props"] == {"ref": 40}
    assert reusable["slots"]["inner_blocks"][0]["props"]["body"] == "Gates open at **dawn**."
    assert components[5]["props"]["source"] == "**Bring water** -- always."


def test_image_blocks_resolve_through_the_attachment(root):
    components = bundle(root, "about/team")["components"]
    image = components[2]
    assert image["type"] == "wp-image"
    assert image["props"]["image"] == {
        "$asset": "assets/trailhead.png", "alt": "Trailhead sign", "title": "North trailhead",
    }, "sized src and empty alt fall back to the attachment's file and alt text"
    assert image["props"]["id"] == 10 and image["props"]["sizeSlug"] == "large"

    column = components[3]["slots"]["inner_blocks"][1]
    missing = column["slots"]["inner_blocks"][0]["props"]
    assert missing["image_url"] == "https://woods.example.com/wp-content/uploads/2026/03/missing.png"
    assert missing["alt"] == "Gone"
    assert column["props"] == {"width": "33%"}
    assert components[4]["type"] == "wp-latest-posts"
    assert components[4]["props"] == {"postsToShow": 3, "displayPostDate": True}


def test_classic_content_is_one_text_component(root):
    components = bundle(root, "blog/fern-walk")["components"]
    assert [c["type"] for c in components] == ["post", "text"]
    text = components[1]
    assert text["props"]["body"] == (
        "First paragraph about *ferns*.\n\nSecond paragraph\nwith a line break.\n\n"
        "![Creek crossing](assets/creek.png)"
    )
    assert text["props"]["body_html"].startswith("<p>First paragraph about <em>ferns</em>.</p>")
    assert text["slots"]["images"][0]["props"]["image"] == {
        "$asset": "assets/creek.png", "alt": "Creek crossing",
    }


def test_meta_has_provenance_status_and_custom_postmeta(root):
    meta = bundle(root, "blog/spring-bloom-report")["meta"]
    assert meta["source"] == "wordpress:20"
    assert meta["generator"].startswith("wordpress-to-toadshade ")
    assert meta["author"] == "ranger-jo" and meta["author_name"] == "Ranger Jo"
    assert meta["date"] == "2026-03-14T09:00:00+00:00"
    assert meta["guid"] == "https://woods.example.com/?p=20"
    assert meta["status"] == "publish"
    assert meta["postmeta"] == {"subtitle": "Week of March 14", "ratings": [4, 5]}, \
        "underscore postmeta is internal"
    assert bundle(root, "blog/fern-walk")["meta"]["status"] == "private"
    assert bundle(root, "post/22")["meta"]["status"] == "draft"
    assert bundle(root, "about/team")["meta"]["post_parent"] == 2


def test_markdown_layer_leaves_out_html(root):
    markdown = (root / "blog/spring-bloom-report/spring-bloom-report.md").read_text()
    assert "## 2. Paragraph" in markdown
    assert "[the trail map](https://woods.example.com/visit/map/)" in markdown
    assert "<p>See" not in markdown


def test_without_uploads_dir_nothing_dangles(tmp_path):
    root = tmp_path / "content"
    list(WordPressToToadshade(WXR, root).export(render=False))
    for b in find_bundles(root):
        assert validate_bundle(b).ok
        assert not (b.path / "assets").exists()
    entry = bundle(root, "spring-bloom-report")["components"][0]["props"]
    assert entry["featured_image_url"] == "https://woods.example.com/wp-content/uploads/2026/03/creek.png"


def test_component_ids_are_unique_per_bundle(root):
    for b in find_bundles(root):
        ids = [c["id"] for top in b.data["components"] for c in walk(top)]
        assert len(ids) == len(set(ids))


def test_post_types_and_stop_after(tmp_path):
    pages = WordPressToToadshade(WXR, tmp_path / "a", post_types=["page"])
    assert len(list(pages.export(render=False))) == 3
    limited = WordPressToToadshade(WXR, tmp_path / "b", stop_after=2)
    assert len(list(limited.export(render=False))) == 2


def test_cli(tmp_path):
    root = tmp_path / "content"
    assert main([str(root), str(WXR), "--uploads-dir", str(UPLOADS), "--sections", "post=blog",
                 "--post-types", "post,page", "--no-render"]) == 0
    assert len(list(find_bundles(root))) == 6
    assert (root / "blog/fern-walk/fern-walk.json").exists()
    with pytest.raises(SystemExit):
        main([str(root), str(tmp_path / "nope.xml")])

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from toadshade import Bundle, find_bundles, render_html, validate_bundle  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "trail-guide"


def test_example_loads():
    bundle = Bundle.load(EXAMPLE)
    assert bundle.slug == "trail-guide"
    assert bundle.data["alias"] == "/visit/trail-guide"


def test_walk_visits_nested_components_in_order():
    bundle = Bundle.load(EXAMPLE)
    numbers = [number for _c, number, _d in bundle.walk()]
    assert numbers[:3] == ["1", "2", "2.1"]
    assert "2.1.3" in numbers
    ids = [c["id"] for c, _n, _d in bundle.walk()]
    assert ids == sorted(set(ids), key=ids.index), "ids must be unique"
    assert "s2a3" in ids


def test_assets_are_discovered_and_exist():
    bundle = Bundle.load(EXAMPLE)
    found = [ref["$asset"] for _c, _p, ref in bundle.assets()]
    assert "assets/trailhead.jpg" in found
    for path in found:
        assert (EXAMPLE / path).exists()


def test_example_validates_without_errors():
    report = validate_bundle(Bundle.load(EXAMPLE))
    assert report.ok, report.errors


def test_markdown_ids_match_json_ids():
    bundle = Bundle.load(EXAMPLE)
    md_ids = set(bundle.markdown_ids())
    json_ids = {c["id"] for c, _n, _d in bundle.walk()}
    assert md_ids == json_ids


def test_find_bundles_on_a_single_bundle_dir():
    assert [b.slug for b in find_bundles(EXAMPLE)] == ["trail-guide"]


def test_find_bundles_walks_a_content_root(tmp_path):
    for slug, alias in [("home", "/"), ("about", "/about")]:
        d = tmp_path / "content" / slug
        d.mkdir(parents=True)
        (d / f"{slug}.json").write_text(json.dumps({
            "toadshade": "0.1", "slug": slug, "title": slug.title(),
            "alias": alias, "components": [],
        }))
    assert sorted(b.slug for b in find_bundles(tmp_path)) == ["about", "home"]


# -- validation failures ----------------------------------------------------


def _bundle(tmp_path, data, slug="page"):
    d = tmp_path / slug
    (d / "assets").mkdir(parents=True)
    (d / f"{slug}.json").write_text(json.dumps(data))
    return Bundle.load(d)


BASE = {"toadshade": "0.1", "slug": "page", "title": "Page", "alias": "/page",
        "components": []}


def test_duplicate_ids_are_an_error(tmp_path):
    data = dict(BASE, components=[
        {"id": "a", "type": "x"},
        {"id": "a", "type": "y"},
    ])
    report = validate_bundle(_bundle(tmp_path, data))
    assert any("duplicate id" in e for e in report.errors)


def test_missing_asset_is_an_error(tmp_path):
    data = dict(BASE, components=[
        {"id": "a", "type": "x",
         "props": {"img": {"$asset": "assets/nope.jpg", "alt": "x"}}},
    ])
    report = validate_bundle(_bundle(tmp_path, data))
    assert any("does not exist" in e for e in report.errors)


def test_asset_escaping_the_bundle_is_an_error(tmp_path):
    data = dict(BASE, components=[
        {"id": "a", "type": "x",
         "props": {"img": {"$asset": "../secrets.jpg", "alt": "x"}}},
    ])
    report = validate_bundle(_bundle(tmp_path, data))
    assert any("assets/" in e for e in report.errors)


def test_bad_alias_is_an_error(tmp_path):
    report = validate_bundle(_bundle(tmp_path, dict(BASE, alias="page")))
    assert any("must start with /" in e for e in report.errors)


def test_slug_mismatch_is_an_error(tmp_path):
    report = validate_bundle(_bundle(tmp_path, dict(BASE, slug="other")))
    assert any("directory" in e for e in report.errors)


def test_missing_alt_is_a_warning_not_an_error(tmp_path):
    d = tmp_path / "page"
    (d / "assets").mkdir(parents=True)
    (d / "assets" / "a.jpg").write_bytes(b"\xff\xd8\xff")
    data = dict(BASE, components=[
        {"id": "a", "type": "x", "props": {"img": {"$asset": "assets/a.jpg"}}},
    ])
    (d / "page.json").write_text(json.dumps(data))
    report = validate_bundle(Bundle.load(d))
    assert report.ok
    assert any("alt text" in w for w in report.warnings)


# -- rendering --------------------------------------------------------------


def test_render_is_self_contained_and_shows_content():
    html = render_html(Bundle.load(EXAMPLE).data)
    assert html.startswith("<!doctype html>")
    assert "Trail Guide" in html
    assert "Blue Loop" in html                    # nested two slots deep
    assert 'src="assets/trailhead.jpg"' in html   # relative, so file:// works
    assert "trail-checklist.txt" in html          # non-image asset as a link
    assert "<style>" in html
    assert "http://" not in html and "https://" not in html.split("<style>")[1]


def test_render_escapes_html_in_content(tmp_path):
    data = dict(BASE, components=[
        {"id": "a", "type": "x", "props": {"heading": "<script>alert(1)</script>"}},
    ])
    html = render_html(data)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_writes_the_file(tmp_path):
    import shutil
    from toadshade import render_bundle

    target = tmp_path / "trail-guide"
    shutil.copytree(EXAMPLE, target)
    out = render_bundle(target)
    assert out.name == "trail-guide.html"
    assert out.read_text().lstrip().startswith("<!doctype html>")


def test_schema_check_skips_on_old_jsonschema(monkeypatch, tmp_path):
    """A pre-4.0 jsonschema must skip the schema check, not crash or lie."""
    import types

    fake = types.ModuleType("jsonschema")
    fake.__version__ = "2.6.0"
    fake.Draft3Validator = object  # what an ancient install actually exposes
    monkeypatch.setitem(sys.modules, "jsonschema", fake)

    report = validate_bundle(_bundle(tmp_path, BASE))
    assert report.ok
    assert any("too old" in s for s in report.skipped)


def test_schema_check_skips_when_jsonschema_absent(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def no_jsonschema(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("no jsonschema")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_jsonschema)
    report = validate_bundle(_bundle(tmp_path, BASE))
    assert report.ok
    assert any("pip install" in s for s in report.skipped)

"""Build the synthetic Backdrop CMS 1.x fixture the backdrop-to-toadshade tests run on.

Writes, next to this script:

    backdrop1.sql       a SQLite dump shaped like Backdrop 1.x's SQL storage
    backdrop-config/    the active config directory (JSON files)
    backdrop-files/     the public files directory those rows point at

Backdrop is a Drupal 7 fork: content lives in the database (`node`,
`field_data_{field}`, `taxonomy_term_data`, `users`, `file_managed`,
`url_alias`), but configuration lives in JSON files. The table and column
names below were checked against Backdrop's `hook_schema()` implementations
and `field_sql_storage.module` (1.35.x-dev, September 2026).

Everything is invented. The quirks follow real Backdrop storage: a field row
in another language, a deleted field row, an older revision, a node that is
the translation of another, a superseded alias, a deleted field instance, a
file missing from disk, a hierarchical alias and a flat one. Regenerate after
editing:

    python3 tests/fixtures/make_backdrop1.py
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from make_drupal10 import php, png  # noqa: E402

T0 = 1773480000  # 2026-03-14

# -- schema: base tables, as in Backdrop's hook_schema() --------------------

BASE_TABLES = """
CREATE TABLE node (nid INTEGER, vid INTEGER, type TEXT, langcode TEXT, title TEXT,
  uid INTEGER, status INTEGER, created INTEGER, changed INTEGER, scheduled INTEGER,
  comment INTEGER, promote INTEGER, sticky INTEGER, tnid INTEGER, translate INTEGER);
CREATE TABLE node_revision (nid INTEGER, vid INTEGER, uid INTEGER, title TEXT, log TEXT,
  timestamp INTEGER, status INTEGER, comment INTEGER, promote INTEGER, sticky INTEGER);
CREATE TABLE taxonomy_term_data (tid INTEGER, vocabulary TEXT, name TEXT,
  description TEXT, format TEXT, langcode TEXT, weight INTEGER);
CREATE TABLE taxonomy_term_hierarchy (tid INTEGER, parent INTEGER);
CREATE TABLE users (uid INTEGER, name TEXT, pass TEXT, mail TEXT, signature TEXT,
  signature_format TEXT, created INTEGER, changed INTEGER, access INTEGER,
  login INTEGER, status INTEGER, timezone TEXT, language TEXT, picture INTEGER,
  init TEXT, data BLOB);
CREATE TABLE users_roles (uid INTEGER, role TEXT);
CREATE TABLE file_managed (fid INTEGER, uid INTEGER, filename TEXT, uri TEXT,
  filemime TEXT, filesize INTEGER, status INTEGER, timestamp INTEGER, type TEXT);
CREATE TABLE url_alias (pid INTEGER, source TEXT, alias TEXT, langcode TEXT, auto INTEGER);
"""

#: Field columns per field type, from each module's hook_field_schema().
COLUMNS = {
    "text": ["value", "format"],
    "text_long": ["value", "format"],
    "text_with_summary": ["value", "summary", "format"],
    "image": ["fid", "alt", "title", "width", "height"],
    "file": ["fid", "display", "description"],
    "taxonomy_term_reference": ["tid"],
    "entityreference": ["target_id"],
    "link_field": ["url", "title", "attributes"],
    "number_integer": ["value"],
    "list_boolean": ["value"],
    "email": ["email"],
    "datetime": ["value", "value2", "timezone"],
}
MODULES = {
    "text": "text", "text_long": "text", "text_with_summary": "text", "image": "image",
    "file": "file", "taxonomy_term_reference": "taxonomy", "entityreference": "entityreference",
    "link_field": "link", "number_integer": "number", "list_boolean": "list",
    "email": "email", "datetime": "date",
}

# (field, type, cardinality, settings)
FIELDS = [
    ("body", "text_with_summary", 1, {}),
    ("field_image", "image", 1, {"uri_scheme": "public"}),
    ("field_tags", "taxonomy_term_reference", -1,
     {"allowed_values": [{"vocabulary": "tags", "parent": "0"}]}),
    ("field_attachment", "file", 1, {"uri_scheme": "public"}),
    ("field_link", "link_field", 1, {}),
    ("field_related", "entityreference", 1, {"target_type": "node", "handler": "base"}),
    ("field_rating", "number_integer", -1, {}),
    ("field_featured", "list_boolean", 1, {"allowed_values": {"0": "", "1": ""}}),
    ("field_event_date", "datetime", 1, {"granularity": {"year": "year"}, "todate": "optional"}),
    ("field_contact", "email", 1, {}),
    ("field_bio", "text_long", 1, {}),
]
#: (entity type, bundle, [(field, label, widget weight)]). Weights are
#: deliberately not in name order.
INSTANCES = [
    ("node", "page", [("body", "Body", 1)]),
    ("node", "post", [
        ("body", "Body", 2), ("field_image", "Image", 1), ("field_tags", "Tags", 3),
        ("field_attachment", "Trail map", 4), ("field_link", "Related link", 5),
        ("field_related", "See also", 6), ("field_rating", "Ratings", 7),
        ("field_featured", "Featured", 9), ("field_event_date", "Walk date", 8),
        ("field_contact", "Contact", 10),
    ]),
    ("user", "user", [("field_bio", "Biography", 0)]),
]


def write_config(directory: Path):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)

    def save(name, data):
        (directory / f"{name}.json").write_text(
            json.dumps({"_config_name": name, **data}, indent=4) + "\n", encoding="utf-8")

    for name, field_type, cardinality, settings in FIELDS:
        save(f"field.field.{name}", {
            "field_name": name, "type": field_type, "module": MODULES[field_type],
            "active": 1, "locked": 0, "cardinality": str(cardinality),  # a string, as saved by the UI
            "translatable": False, "deleted": 0, "entity_types": [], "settings": settings,
            "storage": {"type": "field_sql_storage", "settings": [],
                        "module": "field_sql_storage", "active": 1},
        })
    for entity_type, bundle, fields in INSTANCES:
        for name, label, weight in fields:
            save(f"field.instance.{entity_type}.{bundle}.{name}", {
                "field_name": name, "entity_type": entity_type, "bundle": bundle,
                "label": label, "description": "", "required": 0, "deleted": 0,
                "default_value": None, "default_value_function": None,
                "widget": {"weight": str(weight), "type": "default", "active": 1, "settings": {}},
                "settings": {"user_register_form": False},
                "display": {"default": {"label": "above", "type": "default", "weight": weight}},
            })
    # A field and instance deleted in the UI, awaiting purge: no table exists.
    save("field.field.field_old_notes", {
        "field_name": "field_old_notes", "type": "text_long", "module": "text", "active": 1,
        "locked": 0, "cardinality": "1", "deleted": 1, "settings": {}, "storage": {},
    })
    save("field.instance.node.page.field_old_notes", {
        "field_name": "field_old_notes", "entity_type": "node", "bundle": "page",
        "label": "Old notes", "deleted": 1, "widget": {"weight": "0"},
    })
    for bundle, label in [("page", "Page"), ("post", "Post")]:
        save(f"node.type.{bundle}", {"type": bundle, "name": label, "module": "node",
                                     "description": "", "title_label": "Title"})
    save("taxonomy.vocabulary.tags", {"name": "Tags", "machine_name": "tags",
                                      "description": "", "hierarchy": 1, "weight": 0})
    save("system.core", {"site_name": "Toadshade Woods", "site_mail": "woods@example.com",
                         "site_frontpage": "node/1", "file_public_path": "files",
                         "language_default": "en"})
    save("path.settings", {"node_pattern": "[node:content-type]/[node:title]",
                           "taxonomy_term_pattern": "[term:vocabulary]/[term:name]",
                           "user_pattern": "accounts/[user:name]"})


def insert(db, table, **row):
    db.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
               tuple(row.values()))


def field(db, name, entity_type, bundle, entity_id, values, language="und", deleted=0,
          revision_id=None, table="field_data"):
    field_type = next(t for n, t, _, _ in FIELDS if n == name)
    for delta, value in enumerate(values):
        if not isinstance(value, dict):
            value = {COLUMNS[field_type][0]: value}
        insert(db, f"{table}_{name}", entity_type=entity_type, bundle=bundle, deleted=deleted,
               entity_id=entity_id, revision_id=revision_id or entity_id, language=language,
               delta=delta, **{f"{name}_{k}": v for k, v in value.items()})


def build(db):
    db.executescript(BASE_TABLES)
    for name, field_type, _, _ in FIELDS:
        cols = ", ".join(f"{name}_{c}" for c in COLUMNS[field_type])
        for table in ("field_data", "field_revision"):
            db.execute(f"CREATE TABLE {table}_{name} (entity_type TEXT, bundle TEXT, "
                       f"deleted INTEGER, entity_id INTEGER, revision_id INTEGER, "
                       f"language TEXT, delta INTEGER, {cols})")


def content(db):
    # Files. trail-map.pdf is recorded but deliberately absent from disk.
    for fid, uri, mime in [
        (1, "public://2026-03/trailhead.png", "image/png"),
        (2, "public://docs/trail-map.pdf", "application/pdf"),
        (3, "public://pictures/ranger.png", "image/png"),
        (4, "public://2026-03/creek.png", "image/png"),
    ]:
        insert(db, "file_managed", fid=fid, uid=1, filename=uri.rsplit("/", 1)[1], uri=uri,
               filemime=mime, filesize=100, status=1, timestamp=T0,
               type="image" if mime.startswith("image/") else "document")

    # Users. uid 0 is anonymous and must not become a bundle.
    for uid, name, mail, access, picture, signature, roles in [
        (0, "", "", 0, 0, "", []),
        (1, "site-admin", "admin@example.com", T0 + 100, 0, "", ["administrator"]),
        (2, "ranger-jo", "jo@example.org", 0, 3, "<p>Ranger <em>Jo</em></p>", ["editor"]),
    ]:
        insert(db, "users", uid=uid, name=name,
               **{"pass": f"$S$fixtureonlynotarealhash{uid:02d}" if uid else ""},
               mail=mail, signature=signature, signature_format="filtered_html" if signature else None,
               created=T0 if uid else 0, changed=T0 if uid else 0, access=access, login=access,
               status=1 if uid else 0, timezone="America/New_York" if uid else None,
               language="en" if uid else "", picture=picture, init=mail,
               data=php({"contact": 1}).encode("utf-8") if uid == 1 else None)
        for role in roles:
            insert(db, "users_roles", uid=uid, role=role)
    field(db, "field_bio", "user", "user", 2,
          [{"value": "<p>Jo has walked every trail twice.</p>", "format": "filtered_html"}])

    # Taxonomy. `vocabulary` is a machine name in Backdrop, not a vid number.
    for tid, name, description, parent in [
        (1, "Ferns", "<p>Shade-loving <em>non-flowering</em> plants.</p>", 0),
        (2, "Wildflowers", "", 0),
        (3, "Trilliums", "", 2),
    ]:
        insert(db, "taxonomy_term_data", tid=tid, vocabulary="tags", name=name,
               description=description, format="filtered_html" if description else None,
               langcode="und", weight=tid)
        insert(db, "taxonomy_term_hierarchy", tid=tid, parent=parent)

    # Nodes: (nid, vid, type, langcode, title, status, uid, tnid, body)
    nodes = [
        (1, 1, "page", "und", "Welcome", 1, 1, 0,
         "<p>Welcome to the Toadshade Woods visitor pages.</p>"),
        (2, 2, "page", "und", "About", 1, 1, 0, "<h2>Hours</h2><ul><li>Dawn</li><li>Dusk</li></ul>"),
        (3, 3, "page", "und", "Team", 1, 2, 0,
         '<p>Our rangers keep the <a href="/about">woods</a> open.</p>'
         '<p><img src="/files/styles/large/public/2026-03/creek.png?itok=Xy12" alt="Creek crossing"></p>'
         '<p><img src="/files/2026-03/not-there.png" alt="Missing"></p>'
         "<script>alert('no')</script>"),
        (4, 14, "post", "en", "Spring Bloom Report", 1, 2, 4,
         '<p>See <a href="/visit/map">the trail map</a> for <strong>closures</strong>.</p>'
         "<ul><li>Trilliums</li><li>Mayapples</li></ul>"),
        (5, 5, "post", "en", "Draft: Trail Closures", 0, 1, 0, "<p>Draft.</p>"),
        # A translation node (translation module): same tnid as its source.
        (6, 6, "post", "es", "Informe de floración", 1, 2, 4, "<p>Informe en español.</p>"),
        (7, 7, "page", "und", "Privacy policy", 1, 1, 0, "<p>We collect nothing.</p>"),
        (8, 8, "post", "en", "Fern Walk", 1, 2, 0, "<p>A short loop through the ferns.</p>"),
    ]
    for nid, vid, bundle, langcode, title, status, uid, tnid, body in nodes:
        insert(db, "node", nid=nid, vid=vid, type=bundle, langcode=langcode, title=title, uid=uid,
               status=status, created=T0 + nid, changed=T0 + nid, scheduled=0, comment=1,
               promote=int(bundle == "post"), sticky=0, tnid=tnid, translate=0)
        insert(db, "node_revision", nid=nid, vid=vid, uid=uid, title=title, log="",
               timestamp=T0 + nid, status=status, comment=1, promote=int(bundle == "post"), sticky=0)
        summary = "What is flowering this week." if nid == 4 else None
        value = {"value": body, "summary": summary, "format": "filtered_html"}
        if bundle == "page":
            value.pop("summary")
        field(db, "body", "node", bundle, nid, [value], revision_id=vid)
        field(db, "body", "node", bundle, nid, [value], revision_id=vid, table="field_revision")

    # Node 4 has an older revision (vid 4), which must not be exported.
    insert(db, "node_revision", nid=4, vid=4, uid=2, title="Spring Bloom (early draft)", log="",
           timestamp=T0, status=0, comment=1, promote=1, sticky=0)
    field(db, "body", "node", "post", 4,
          [{"value": "<p>Early draft.</p>", "summary": None, "format": "filtered_html"}],
          revision_id=4, table="field_revision")
    # ...a field row in another language on the same node (field translation)...
    field(db, "body", "node", "post", 4,
          [{"value": "<p>Informe en español.</p>", "summary": None, "format": "filtered_html"}],
          language="es", revision_id=14)
    # ...and a deleted tag value, awaiting field purge.
    field(db, "field_tags", "node", "post", 4, [{"tid": 2}], deleted=1, revision_id=14)

    field(db, "field_image", "node", "post", 4,
          [{"fid": 1, "alt": "Trailhead sign", "title": "North trailhead", "width": 8, "height": 8}],
          revision_id=14)
    field(db, "field_tags", "node", "post", 4, [{"tid": 1}, {"tid": 3}], revision_id=14)
    field(db, "field_attachment", "node", "post", 4,
          [{"fid": 2, "display": 1, "description": "Printable trail map"}], revision_id=14)
    field(db, "field_link", "node", "post", 4,
          [{"url": "node/2", "title": "About us", "attributes": "a:0:{}"}], revision_id=14)
    field(db, "field_related", "node", "post", 4, [{"target_id": 8}], revision_id=14)
    field(db, "field_rating", "node", "post", 4, [4, 5], revision_id=14)
    field(db, "field_featured", "node", "post", 4, [1], revision_id=14)
    field(db, "field_event_date", "node", "post", 4,
          [{"value": "2026-04-18 14:00:00", "value2": "2026-04-18 16:00:00", "timezone": "UTC"}],
          revision_id=14)
    field(db, "field_contact", "node", "post", 4, ["rangers@example.org"], revision_id=14)
    field(db, "field_link", "node", "post", 8,
          [{"url": "https://trails.example.com/ferns", "title": "Trail notes",
            "attributes": 'a:1:{s:6:"target";s:6:"_blank";}'}])

    # Aliases. Backdrop stores `source` and `alias` without a leading slash.
    for pid, source, alias, langcode in [
        (1, "node/1", "welcome", "und"),
        (2, "node/2", "about", "und"),
        (3, "node/3", "about/team", "und"),                  # hierarchical
        (4, "node/4", "blog/spring-bloom-report", "en"),
        (5, "node/6", "blog/informe-de-floracion", "es"),
        (6, "node/7", "privacy-policy", "und"),              # flat
        (7, "node/8", "old-fern-walk", "en"),                # superseded by pid 8
        (8, "node/8", "fern-walk", "en"),
        (9, "taxonomy/term/1", "tags/ferns", "und"),
        (10, "taxonomy/term/2", "tags/wildflowers", "und"),
        (11, "user/2", "staff/ranger-jo", "und"),
    ]:
        insert(db, "url_alias", pid=pid, source=source, alias=alias, langcode=langcode, auto=0)


def main():
    db = sqlite3.connect(":memory:")
    build(db)
    content(db)
    db.commit()
    (HERE / "backdrop1.sql").write_text("\n".join(db.iterdump()) + "\n", encoding="utf-8")
    write_config(HERE / "backdrop-config")

    files = HERE / "backdrop-files"
    for relative, rgb in [
        ("2026-03/trailhead.png", (44, 95, 45)),
        ("2026-03/creek.png", (70, 120, 160)),
        ("pictures/ranger.png", (200, 170, 60)),
    ]:
        path = files / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png(rgb))
    print(f"wrote {HERE / 'backdrop1.sql'}, {HERE / 'backdrop-config'} and {files}")


if __name__ == "__main__":
    main()

"""Build the synthetic Drupal 10 fixture the drupal-to-toadshade tests run on.

Writes, next to this script:

    drupal10.sql      a SQLite dump shaped like Drupal 10's SQL storage
    drupal-files/     the public files directory those rows point at

Everything is invented. The table and column names, the serialized `config`
rows, and the quirks (a translation row, a deleted field value, a disabled
alias, a file that is missing from disk) follow real Drupal 10 storage; the
woods, rangers, and trails do not exist. Regenerate after editing:

    python3 tests/fixtures/make_drupal10.py
"""

import sqlite3
import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
T0 = 1773480000  # 2026-03-14


def php(value) -> str:
    """PHP serialize(), for writing Drupal's `config` table."""
    if value is None:
        return "N;"
    if isinstance(value, bool):
        return f"b:{int(value)};"
    if isinstance(value, int):
        return f"i:{value};"
    if isinstance(value, float):
        return f"d:{value};"
    if isinstance(value, str):
        return f's:{len(value.encode("utf-8"))}:"{value}";'
    if isinstance(value, (list, tuple)):
        value = dict(enumerate(value))
    return f"a:{len(value)}:{{" + "".join(php(k) + php(v) for k, v in value.items()) + "}"


def png(rgb, size=8) -> bytes:
    """A small solid-colour PNG, so image assets are real image files."""
    row = b"\x00" + bytes(rgb) * size
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size))
            + chunk(b"IEND", b""))


# -- schema -----------------------------------------------------------------

BASE_TABLES = """
CREATE TABLE config (collection TEXT, name TEXT, data BLOB);
CREATE TABLE node (nid INTEGER, vid INTEGER, type TEXT, uuid TEXT, langcode TEXT);
CREATE TABLE node_field_data (nid INTEGER, vid INTEGER, type TEXT, langcode TEXT,
  status INTEGER, uid INTEGER, title TEXT, created INTEGER, changed INTEGER,
  promote INTEGER, sticky INTEGER, default_langcode INTEGER,
  revision_translation_affected INTEGER);
CREATE TABLE taxonomy_term_data (tid INTEGER, revision_id INTEGER, vid TEXT, uuid TEXT, langcode TEXT);
CREATE TABLE taxonomy_term_field_data (tid INTEGER, revision_id INTEGER, vid TEXT,
  langcode TEXT, status INTEGER, name TEXT, description__value TEXT,
  description__format TEXT, weight INTEGER, changed INTEGER,
  default_langcode INTEGER, revision_translation_affected INTEGER);
CREATE TABLE users (uid INTEGER, uuid TEXT, langcode TEXT);
CREATE TABLE users_field_data (uid INTEGER, langcode TEXT, preferred_langcode TEXT,
  preferred_admin_langcode TEXT, name TEXT, pass TEXT, mail TEXT, timezone TEXT,
  status INTEGER, created INTEGER, changed INTEGER, access INTEGER, login INTEGER,
  init TEXT, default_langcode INTEGER);
CREATE TABLE paragraphs_item (id INTEGER, revision_id INTEGER, type TEXT, uuid TEXT, langcode TEXT);
CREATE TABLE paragraphs_item_field_data (id INTEGER, revision_id INTEGER, type TEXT,
  langcode TEXT, status INTEGER, created INTEGER, parent_id TEXT, parent_type TEXT,
  parent_field_name TEXT, behavior_settings BLOB, default_langcode INTEGER,
  revision_translation_affected INTEGER);
CREATE TABLE media (mid INTEGER, vid INTEGER, bundle TEXT, uuid TEXT, langcode TEXT);
CREATE TABLE media_field_data (mid INTEGER, vid INTEGER, bundle TEXT, langcode TEXT,
  status INTEGER, uid INTEGER, name TEXT, thumbnail__target_id INTEGER,
  thumbnail__alt TEXT, thumbnail__title TEXT, thumbnail__width INTEGER,
  thumbnail__height INTEGER, created INTEGER, changed INTEGER,
  default_langcode INTEGER, revision_translation_affected INTEGER);
CREATE TABLE file_managed (fid INTEGER, uuid TEXT, langcode TEXT, uid INTEGER,
  filename TEXT, uri TEXT, filemime TEXT, filesize INTEGER, status INTEGER,
  created INTEGER, changed INTEGER);
CREATE TABLE path_alias (id INTEGER, revision_id INTEGER, uuid TEXT, langcode TEXT,
  path TEXT, alias TEXT, status INTEGER);
"""

COLUMNS = {
    "string": ["value"], "boolean": ["value"], "integer": ["value"],
    "text_long": ["value", "format"], "text_with_summary": ["value", "summary", "format"],
    "image": ["target_id", "alt", "title", "width", "height"],
    "file": ["target_id", "display", "description"],
    "entity_reference": ["target_id"],
    "entity_reference_revisions": ["target_id", "target_revision_id"],
    "link": ["uri", "title", "options"],
}

# (entity type, field, type, cardinality, target type)
STORAGES = [
    ("node", "body", "text_with_summary", 1, None),
    ("node", "field_subtitle", "string", 1, None),
    ("node", "field_featured", "boolean", 1, None),
    ("node", "field_image", "image", 1, None),
    ("node", "field_attachment", "file", 1, None),
    ("node", "field_tags", "entity_reference", -1, "taxonomy_term"),
    ("node", "field_sections", "entity_reference_revisions", -1, "paragraph"),
    ("node", "field_link", "link", 1, None),
    ("node", "field_media", "entity_reference", 1, "media"),
    ("node", "field_rating", "integer", -1, None),
    ("paragraph", "field_heading", "string", 1, None),
    ("paragraph", "field_text", "text_long", 1, None),
    ("paragraph", "field_cards", "entity_reference_revisions", -1, "paragraph"),
    ("paragraph", "field_card_image", "image", 1, None),
    ("media", "field_media_image", "image", 1, None),
    ("media", "field_media_oembed_video", "string", 1, None),
    ("user", "user_picture", "image", 1, None),
]

# (entity type, bundle, [(field, label)]) — list order is form-display order.
INSTANCES = [
    ("node", "page", [("body", "Body")]),
    ("node", "article", [
        ("field_subtitle", "Subtitle"), ("body", "Body"), ("field_image", "Image"),
        ("field_sections", "Sections"), ("field_tags", "Tags"),
        ("field_link", "Related link"), ("field_attachment", "Trail map"),
        ("field_media", "Featured media"), ("field_rating", "Ratings"),
    ]),
    ("paragraph", "text_block", [("field_heading", "Heading"), ("field_text", "Text")]),
    ("paragraph", "card_group", [("field_heading", "Heading"), ("field_cards", "Cards")]),
    ("paragraph", "card", [("field_heading", "Heading"), ("field_card_image", "Image")]),
    ("media", "image", [("field_media_image", "Image")]),
    ("media", "remote_video", [("field_media_oembed_video", "Video URL")]),
    ("user", "user", [("user_picture", "Picture")]),
]
#: On the article bundle but hidden from its form display — sorts last.
HIDDEN = {("node", "article"): [("field_featured", "Featured")]}


def build(db):
    db.executescript(BASE_TABLES)
    types = {}
    for entity_type, name, field_type, cardinality, target in STORAGES:
        types[(entity_type, name)] = field_type
        cols = ", ".join(f"{name}_{c}" for c in COLUMNS[field_type])
        db.execute(f"CREATE TABLE {entity_type}__{name} (bundle TEXT, deleted INTEGER, "
                   f"entity_id INTEGER, revision_id INTEGER, langcode TEXT, delta INTEGER, {cols})")
        config(db, f"field.storage.{entity_type}.{name}", {
            "uuid": f"storage-{entity_type}-{name}", "langcode": "en", "status": True,
            "id": f"{entity_type}.{name}", "field_name": name, "entity_type": entity_type,
            "type": field_type, "cardinality": cardinality,
            "settings": {"target_type": target} if target else {},
            "module": "core", "locked": False, "translatable": True,
        })
    db.execute("CREATE TABLE taxonomy_term__parent (bundle TEXT, deleted INTEGER, entity_id INTEGER, "
               "revision_id INTEGER, langcode TEXT, delta INTEGER, parent_target_id INTEGER)")
    db.execute("CREATE TABLE user__roles (bundle TEXT, deleted INTEGER, entity_id INTEGER, "
               "langcode TEXT, delta INTEGER, roles_target_id TEXT)")

    for entity_type, bundle, fields in INSTANCES:
        content = {"title": {"type": "string_textfield", "weight": -5, "region": "content"}}
        for weight, (name, label) in enumerate(fields):
            content[name] = {"type": "default", "weight": weight, "region": "content", "settings": []}
        for name, label in fields + HIDDEN.get((entity_type, bundle), []):
            config(db, f"field.field.{entity_type}.{bundle}.{name}", {
                "uuid": f"field-{entity_type}-{bundle}-{name}", "id": f"{entity_type}.{bundle}.{name}",
                "field_name": name, "entity_type": entity_type, "bundle": bundle,
                "label": label, "required": False, "field_type": types[(entity_type, name)],
            })
        config(db, f"core.entity_form_display.{entity_type}.{bundle}.default", {
            "targetEntityType": entity_type, "bundle": bundle, "mode": "default",
            "content": content, "hidden": {n: True for n, _ in HIDDEN.get((entity_type, bundle), [])},
        })
    config(db, "system.site", {"name": "Toadshade Woods", "mail": "woods@example.com",
                               "page": {"403": "", "404": "", "front": "/node/1"}})
    return types


def config(db, name, data):
    db.execute("INSERT INTO config VALUES ('', ?, ?)", (name, php(data).encode("utf-8")))


def insert(db, table, **row):
    db.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
               tuple(row.values()))


def field(db, types, entity_type, bundle, name, entity_id, values, langcode="en", deleted=0):
    for delta, value in enumerate(values):
        if not isinstance(value, dict):
            value = {"value": value}
        insert(db, f"{entity_type}__{name}", bundle=bundle, deleted=deleted, entity_id=entity_id,
               revision_id=entity_id, langcode=langcode, delta=delta,
               **{f"{name}_{k}": v for k, v in value.items()})


def content(db, types):
    # Files. trail-map.pdf is recorded but deliberately absent from disk.
    for fid, uri, mime in [
        (1, "public://2026-03/trailhead.png", "image/png"),
        (2, "public://docs/trail-map.pdf", "application/pdf"),
        (3, "public://pictures/ranger.png", "image/png"),
        (4, "public://2026-03/boardwalk.png", "image/png"),
        (5, "public://2026-03/creek.png", "image/png"),
        (6, "public://oembed_thumbnails/walkthrough.png", "image/png"),
    ]:
        insert(db, "file_managed", fid=fid, uuid=f"file-{fid}", langcode="en", uid=1,
               filename=uri.rsplit("/", 1)[1], uri=uri, filemime=mime, filesize=100,
               status=1, created=T0, changed=T0)

    # Users. uid 0 is anonymous and must not become a bundle.
    for uid, name, mail, access, roles in [
        (0, "", None, 0, []),
        (1, "site-admin", "admin@example.com", T0 + 100, ["administrator"]),
        (2, "ranger-jo", "jo@example.org", 0, ["editor"]),
    ]:
        insert(db, "users", uid=uid, uuid=f"user-{uid}", langcode="en")
        insert(db, "users_field_data", uid=uid, langcode="en", preferred_langcode="en",
               preferred_admin_langcode=None, name=name,
               **{"pass": f"$2y$10$fixtureonlynotarealhash{uid:02d}" if uid else None},
               mail=mail, timezone="America/New_York", status=1 if uid else 0,
               created=T0, changed=T0, access=access, login=access, init=mail,
               default_langcode=1)
        for delta, role in enumerate(roles):
            insert(db, "user__roles", bundle="user", deleted=0, entity_id=uid, langcode="en",
                   delta=delta, roles_target_id=role)
    field(db, types, "user", "user", "user_picture", 2,
          [{"target_id": 3, "alt": "Ranger Jo at the trailhead", "title": None, "width": 8, "height": 8}])

    # Taxonomy.
    for tid, name, description, parent in [
        (1, "Ferns", "<p>Shade-loving <em>non-flowering</em> plants.</p>", 0),
        (2, "Wildflowers", None, 0),
        (3, "Trilliums", None, 2),
    ]:
        insert(db, "taxonomy_term_data", tid=tid, revision_id=tid, vid="tags", uuid=f"term-{tid}", langcode="en")
        insert(db, "taxonomy_term_field_data", tid=tid, revision_id=tid, vid="tags", langcode="en",
               status=1, name=name, description__value=description,
               description__format="basic_html" if description else None, weight=tid,
               changed=T0, default_langcode=1, revision_translation_affected=1)
        insert(db, "taxonomy_term__parent", bundle="tags", deleted=0, entity_id=tid,
               revision_id=tid, langcode="en", delta=0, parent_target_id=parent)

    # Media.
    insert(db, "media", mid=1, vid=1, bundle="image", uuid="media-1", langcode="en")
    insert(db, "media_field_data", mid=1, vid=1, bundle="image", langcode="en", status=1, uid=1,
           name="Boardwalk photo", thumbnail__target_id=4, thumbnail__alt="Boardwalk",
           thumbnail__title=None, thumbnail__width=8, thumbnail__height=8,
           created=T0, changed=T0, default_langcode=1, revision_translation_affected=1)
    field(db, types, "media", "image", "field_media_image", 1,
          [{"target_id": 4, "alt": "Boardwalk through the swamp", "title": None, "width": 8, "height": 8}])
    # A remote video: its only local file is the thumbnail Drupal fetched.
    insert(db, "media", mid=2, vid=2, bundle="remote_video", uuid="media-2", langcode="en")
    insert(db, "media_field_data", mid=2, vid=2, bundle="remote_video", langcode="en", status=1, uid=1,
           name="Boardwalk walkthrough", thumbnail__target_id=6, thumbnail__alt="",
           thumbnail__title=None, thumbnail__width=8, thumbnail__height=8,
           created=T0, changed=T0, default_langcode=1, revision_translation_affected=1)
    field(db, types, "media", "remote_video", "field_media_oembed_video", 2,
          ["https://video.example.com/watch/boardwalk-walkthrough"])

    # Paragraphs: a text block, and a card group holding one card.
    for pid, ptype, parent_type, parent_id in [
        (1, "text_block", "node", 4), (2, "card_group", "node", 4), (3, "card", "paragraph", 2),
    ]:
        insert(db, "paragraphs_item", id=pid, revision_id=pid, type=ptype, uuid=f"paragraph-{pid}", langcode="en")
        insert(db, "paragraphs_item_field_data", id=pid, revision_id=pid, type=ptype, langcode="en",
               status=1, created=T0, parent_id=str(parent_id), parent_type=parent_type,
               parent_field_name="field_sections" if parent_type == "node" else "field_cards",
               behavior_settings=b"a:0:{}", default_langcode=1, revision_translation_affected=1)
    field(db, types, "paragraph", "text_block", "field_heading", 1, ["What's blooming"])
    field(db, types, "paragraph", "text_block", "field_text", 1,
          [{"value": "<h3>Along the creek</h3><p>Look for <em>white</em> petals.</p>", "format": "basic_html"}])
    field(db, types, "paragraph", "card_group", "field_heading", 2, ["Trail cards"])
    field(db, types, "paragraph", "card_group", "field_cards", 2, [{"target_id": 3, "target_revision_id": 3}])
    field(db, types, "paragraph", "card", "field_heading", 3, ["Trilliums"])
    field(db, types, "paragraph", "card", "field_card_image", 3,
          [{"target_id": 1, "alt": "Trillium bloom", "title": None, "width": 8, "height": 8}])

    # Nodes.
    nodes = [
        (1, "page", "Welcome", 1, 1, "<p>Welcome to the Toadshade Woods visitor pages.</p>"),
        (2, "page", "About", 1, 1, "<h2>Hours</h2><ul><li>Dawn</li><li>Dusk</li></ul>"),
        (3, "page", "Team", 1, 2,
         '<p>Our rangers keep the <a href="/about">woods</a> open.</p>'
         '<p><img src="/sites/default/files/styles/large/public/2026-03/creek.png?itok=Xy12" alt="Creek crossing"></p>'
         '<p><img src="/sites/default/files/2026-03/not-there.png" alt="Missing"></p>'
         "<script>alert('no')</script>"),
        (4, "article", "Spring Bloom Report", 1, 2,
         '<p>See <a href="/visit/map">the trail map</a> for <strong>closures</strong>.</p>'
         "<ul><li>Trilliums</li><li>Mayapples</li></ul>"),
        (5, "article", "Draft: Trail Closures", 0, 1, "<p>Draft.</p>"),
        (6, "article", "Spring bloom report (duplicate)", 1, 1, "<p>Posted twice.</p>"),
        (7, "page", "Privacy policy", 1, 1, "<p>We collect nothing.</p>"),
    ]
    for nid, bundle, title, status, uid, body in nodes:
        insert(db, "node", nid=nid, vid=nid + 10, type=bundle, uuid=f"node-{nid}", langcode="en")
        insert(db, "node_field_data", nid=nid, vid=nid + 10, type=bundle, langcode="en", status=status,
               uid=uid, title=title, created=T0 + nid, changed=T0 + nid, promote=int(bundle == "article"),
               sticky=0, default_langcode=1, revision_translation_affected=1)
        summary = "What is flowering this week." if nid == 4 else None
        field(db, types, "node", bundle, "body", nid,
              [{"value": body, "summary": summary, "format": "basic_html"}])

    # Node 4 has a Spanish translation, which the exporter must ignore.
    insert(db, "node_field_data", nid=4, vid=14, type="article", langcode="es", status=1, uid=2,
           title="Informe de floración", created=T0 + 4, changed=T0 + 4, promote=1, sticky=0,
           default_langcode=0, revision_translation_affected=1)
    field(db, types, "node", "article", "body", 4,
          [{"value": "<p>Informe en español.</p>", "summary": None, "format": "basic_html"}], langcode="es")

    field(db, types, "node", "article", "field_subtitle", 4, ["Old subtitle"], deleted=1)
    field(db, types, "node", "article", "field_subtitle", 4, ["Week of March 14"])
    field(db, types, "node", "article", "field_featured", 4, [1])
    field(db, types, "node", "article", "field_image", 4,
          [{"target_id": 1, "alt": "Trailhead sign", "title": "North trailhead", "width": 8, "height": 8}])
    field(db, types, "node", "article", "field_attachment", 4,
          [{"target_id": 2, "display": 1, "description": "Printable trail map"}])
    field(db, types, "node", "article", "field_tags", 4, [{"target_id": 1}, {"target_id": 3}])
    field(db, types, "node", "article", "field_sections", 4,
          [{"target_id": 1, "target_revision_id": 1}, {"target_id": 2, "target_revision_id": 2}])
    field(db, types, "node", "article", "field_link", 4,
          [{"uri": "internal:/node/2", "title": "About us", "options": "a:0:{}"}])
    field(db, types, "node", "article", "field_media", 4, [{"target_id": 1}])
    field(db, types, "node", "article", "field_rating", 4, [4, 5])
    field(db, types, "node", "article", "field_media", 5, [{"target_id": 1}])
    field(db, types, "node", "article", "field_media", 6, [{"target_id": 2}])

    # Aliases. The disabled one must not be used.
    for i, (path, alias, status) in enumerate([
        ("/node/1", "/welcome", 1),
        ("/node/2", "/about", 1),
        ("/node/3", "/about/team", 1),
        ("/node/4", "/news/spring-bloom-report", 1),
        ("/node/5", "/should-not-appear", 0),
        ("/node/6", "/news/Spring-Bloom-Report", 1),
        ("/node/7", "/privacy-policy", 1),
        ("/taxonomy/term/1", "/tags/ferns", 1),
        ("/taxonomy/term/2", "/tags/wildflowers", 1),
        ("/user/2", "/staff/ranger-jo", 1),
    ], start=1):
        insert(db, "path_alias", id=i, revision_id=i, uuid=f"alias-{i}", langcode="en",
               path=path, alias=alias, status=status)


def main():
    db = sqlite3.connect(":memory:")
    types = build(db)
    content(db, types)
    db.commit()
    (HERE / "drupal10.sql").write_text("\n".join(db.iterdump()) + "\n", encoding="utf-8")

    files = HERE / "drupal-files"
    for relative, rgb in [
        ("2026-03/trailhead.png", (44, 95, 45)),
        ("2026-03/boardwalk.png", (139, 110, 78)),
        ("2026-03/creek.png", (70, 120, 160)),
        ("pictures/ranger.png", (200, 170, 60)),
        ("oembed_thumbnails/walkthrough.png", (90, 90, 90)),
    ]:
        path = files / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png(rgb))
    print(f"wrote {HERE / 'drupal10.sql'} and {files}")


if __name__ == "__main__":
    main()

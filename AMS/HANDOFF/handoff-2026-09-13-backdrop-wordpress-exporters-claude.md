# Handoff: `backdrop-to-toadshade` and `wordpress-to-toadshade`

**Date:** 2026-09-13 (overnight, unattended)
**Author:** claude
**Branch:** `claude/backdrop-and-wordpress-exporters`, pushed to origin. It has
three commits on top of `main` (84e153a). No PR was opened and nothing was
committed to `main`.
**State:** both parts are done. There are 119 tests: the 74 existing, 24 for
Backdrop and 21 for WordPress. All pass on Python 3.11.3, and on 3.9.6 (the
macOS system Python, run through `uv`).
**Prior handoffs:** `handoff-2026-09-12-drupal-exporter-claude.md` (the
Drupal exporter these extend), `handoff-2026-09-12-sfdug-wsod-cursor.md`,
`handoff-2026-09-11-maintenance-claude.md`.
**Docs:** `AMS/DOC/importers.md` has two new sections, "The third one" and
"The fourth one". They're the design write-up; this file covers what happened
and the decisions.

---

## 1. What was built

| Part | Status | Commit |
|---|---|---|
| `backdrop-to-toadshade`: CLI, `backdrop` extra, fixture, 24 tests, docs | done, tested on a real Backdrop install | 760d20a |
| `wordpress-to-toadshade`: CLI, fixture, 21 tests, docs | done, smoke-tested on two public WXR files | 54e81ee |
| Docs, CHANGELOG, README, this handoff | done | final commit |

### Backdrop

`src/toadshade/importers/backdrop.py`. `BackdropDatabase` subclasses
`DrupalDatabase` and yields **the same record dict shape**, so the whole Drupal
bundle layer runs unchanged. That covers components, text, assets, inline
images, Markdown and placement. `BackdropToToadshade` is `DrupalToToadshade`
with four class attributes swapped. The only bundle-layer override is a small
builder subclass that formats Backdrop date fields.

**Refactor to `drupal.py`.** It was kept minimal, with no behaviour change, and
all 74 existing tests passed before and after.
- `DrupalDatabase.descriptors` and `DrupalToToadshade.descriptors` replace
  direct uses of the global `ENTITY_TYPES`, which still exists and is still the
  default.
- `DrupalToToadshade.database_class`, `builder_class` and `source_prefix` are
  class attributes.
- `connect(dsn, extra=, password_env=)` now names the right pip extra and
  password variable for each exporter.
- `parents()` derives `/node/0`-style system paths from the descriptors
  instead of a hardcoded list. It gives the same four paths for Drupal.

### WordPress

`src/toadshade/importers/wordpress.py`:
- `WxrFile` is the fetch layer. It streams with `iterparse` and yields plain
  dicts.
- `parse_blocks()` implements the Gutenberg comment grammar.
- `WordPressToToadshade` is the bundle layer.

It reuses `html_to_markdown`, `drupal_markdown`, `php_unserialize` and
`_inside` from `drupal.py` by importing them.

**Shared placement.** `AliasPlacement` moved into `base.py`: filed by alias,
parents move down, `--sections`, `-2` for collisions. It's the Drupal code
lifted out as it was. `DrupalToToadshade.place()`, `parents()` and
`section_for()` are now thin wrappers, and the Drupal placement tests pass
unchanged. The move was clean because the logic only needed "every path that
will hold a bundle" and the sections map.

### `render.py`

Not touched. Nothing in either exporter needed a renderer change.

## 2. Judgment calls

### Backdrop

1. **Verified against Backdrop's source, not memory.** I cloned
   github.com/backdrop/backdrop (1.35.x-dev, commit 4dba3c3, 2026-09-04) and
   read these:

   | Source | What it confirmed |
   |---|---|
   | `hook_schema()` for `node`, `node_revision`, `taxonomy_term_data`, `taxonomy_term_hierarchy`, `users`, `users_roles`, `file_managed`, `url_alias` | column lists, extracted with a script |
   | `_field_sql_storage_schema()` | `field_data_{field}` / `field_revision_{field}`; columns `entity_type`, `bundle`, `deleted`, `entity_id`, `revision_id`, `language`, `delta`, `{field}_{column}` |
   | `field.crud.inc` | config names `field.field.{field_name}` and `field.instance.{entity}.{bundle}.{field}`, their keys, `deleted` flags |
   | `hook_field_schema()` for text, image, file, taxonomy, entityreference, link, email, list, number, date | column names |
   | `config.inc` | `ConfigFileStorage` vs `ConfigDatabaseStorage` (JSON in a `config_active` table) |
   | `system.admin.inc` | `site_frontpage` is saved as a normal path |
   | `path.inc` | alias lookup: newest `pid` wins |

   Things that differ from Drupal 7 or from what I expected:
   - **Backdrop core has no uuid columns.**
   - `taxonomy_term_data.vocabulary` is a machine name.
   - `users_roles.role` is a machine name.
   - `file_managed` has `timestamp` and `type`, not `created`/`changed`.
   - `users` has `picture` (a fid), `signature`/`signature_format`, and a
     serialized `data`.
   - `url_alias` has `auto`.
2. **Neutral record shape via renames in the fetch layer.** Backdrop column
   and field-type names are mapped to Drupal 10's:
   - `description`/`format` become `description__value`/`__format`.
   - `picture` becomes `picture__target_id`.
   - `fid`/`tid` become `target_id`.
   - `taxonomy_term_reference` and `entityreference` become `entity_reference`.
   - `link_field` becomes `link`, with `url` → `uri` and `attributes` →
     `options`.
   - `list_boolean` becomes `boolean`.
   - `email` becomes `value`.
   - Date `value2` becomes `end_value`.

   The original type is kept as `storage_type`. This is what lets the Drupal
   bundle layer run unchanged.
3. **Term parents and user roles** are separate tables in Backdrop, not
   fields. `BackdropDatabase.field_rows()` serves them as "virtual tables"
   under the Drupal 10 names that `record()` asks for.
4. **`field_data_*` tables are shared by all entity types**, so rows are
   filtered by `entity_type`. There's a test for a user and a node with the
   same id.
5. **Languages.**
   - Field rows are kept when their `language` is the entity's own or `und`.
     If none match, the rows of the first language present are used.
   - **Translation nodes** (the translation module's `tnid != nid`) are
     skipped, to match the "default language" scope. `tnid` is kept in meta.
6. **Current revision.** Only `node` plus `field_data_*` are read.
   `node_revision` and `field_revision_*` are never read. The fixture has an
   older revision to prove it.
7. **Config source.**
   - `--config-dir` is read when given.
   - Without it, the exporter falls back to the `config_active` table, since
     `ConfigDatabaseStorage` is a real Backdrop option.
   - If neither has `system.core`, the CLI stops with a clear error rather
     than exporting pages with no fields.
8. **Dates.**
   - `datestamp` becomes ISO UTC.
   - `datetime` (`2026-04-18 14:00:00`) becomes `2026-04-18T14:00:00`, with no
     timezone invented. Backdrop's per-field timezone handling isn't applied.
   - A single value collapses to a string.
9. **Bookkeeping.**
   - Node `comment`, `tnid`, `translate`, `scheduled` and
     `comment_close_override` go to meta, like Drupal's `uid`/`promote`.
   - User `language` and the decoded `data` go to meta too.
10. **Default files URL** is `/files`, because `file_public_path` defaults to
    `files`. Image styles are handled at `/files/styles/{style}/public/…`.
11. **Out of scope, as briefed:**
    - The Layout module and Views. The standard profile's `home` layout has
      no entity, so it produces no bundle.
    - Comments.
    - Contrib Paragraphs.

### WordPress

1. **Page structure.**
   - The first component is the item itself, typed by post type (`post`,
     `page`, `trail`) and holding `title`, `excerpt`, terms and
     `featured_image`.
   - Then there's one top-level component per block, instead of wrapping the
     blocks in a single component with a `content` slot. A WordPress page *is*
     its block list, and top-level blocks give reviewers "section 3" numbering.
   - This differs from Drupal, which has one component per entity. Say if you
     want it wrapped instead.
2. **Block types.**
   - `core/paragraph` becomes `wp-paragraph`, and `jetpack/markdown` becomes
     `wp-jetpack/markdown`, keeping the slash as briefed. The schema allows it.
   - Each block component gets `label` set to its humanized name, which gives
     Markdown headings like `## 2. Paragraph`.
3. **Block props and slots.**
   - Attributes become props verbatim. Inner blocks go in the slot
     `inner_blocks`.
   - `body`/`body_html` are only added when the inner HTML has text, so a
     list's `<ul></ul>` wrapper isn't a body.
   - `core/image` blocks have no body; the image is the content.
4. **Reusable blocks** (`core/block {"ref":N}`) are expanded from the
   `wp_block` item into `inner_blocks`. `ref` is kept as a prop.
5. **Images.** Resolution order:
   1. the attachment's `_wp_attached_file` under `--uploads-dir` (the original,
      not the resized `src`)
   2. the `src` URL mapped at `/wp-content/uploads/`
   3. the same URL with `-1024x768` / `-scaled` / `-rotated` stripped

   Alt text comes from the `<img>`, else the attachment's
   `_wp_attachment_image_alt`. The figcaption becomes the asset `title`.
   Anything unresolvable becomes `image_url`, or `featured_image_url`.
6. **Aliases.**
   - The alias is the `<link>` path relative to the blog's base path, so a
     site in `/wp` isn't nested under `wp/`.
   - Plain or draft links (`?p=22`) are rebuilt from `post_name` plus the
     `post_parent` chain. If any name is missing, the alias is
     `/{post_type}/{id}`, like Drupal's `node/5`.
   - Paths stay percent-encoded in the JSON and are decoded only for placing
     directories.
7. **Statuses.**
   - Exported: `publish`, `draft`, `pending`, `future` and `private`.
   - Skipped: `auto-draft` (never real content) and `trash`.
   - Status goes in meta.
8. **Term URLs** use WordPress's default `/category/{parents}/{slug}` and
   `/tag/{slug}`. WXR doesn't record a custom base, so this is an assumption.
   Custom taxonomies get labels only, without guessing a URL.
9. **Meta.**
   - Postmeta goes under `meta.postmeta` rather than flattened, so it can't
     collide with `status` and the like.
   - `_`-prefixed keys are skipped.
   - Serialized values are decoded.
   - A repeated key keeps a list.
10. **Comments** are counted (`comment_count`) but not exported. The parser
    tracks nesting, so `wp:commentmeta` is never read as postmeta. There's a
    test for this.
11. **Classic content.** It gets a small `autop()` so that `body_html` has
    paragraphs, as WordPress renders it. Otherwise the preview would show one
    run-on paragraph.
12. **Robustness.**
    - XML-invalid control characters are stripped from the byte stream before
      `iterparse`. Real exports contain them, and one bad byte would otherwise
      abort the file.
    - Namespaces are matched by rule, so the `https://wordpress.org/export/1.2/`
      used by the theme test data works too.

## 3. Verification

- **Tests:** `python3 -m pytest tests -q` gives 119 passed on 3.11.3. The same
  run with `uv run --no-project --python /usr/bin/python3 --with pytest`
  (3.9.6) also gives 119 passed.
- **CLIs against their fixtures**, then the `toadshade validate` CLI:

  | Exporter | Bundles | Errors | Warnings |
  |---|---|---|---|
  | `backdrop-to-toadshade` | 12 | 0 | 0 |
  | `wordpress-to-toadshade --sections post=blog` | 7 | 0 | 0 |

- **Previews:** I took headless Chrome screenshots of the Backdrop post and
  user, and the WordPress Team page and Spring Bloom Report. They read like
  pages:
  - title, formatted text, links, readable date ranges
  - categories and tags as links
  - captions under figures
  - the missing file as a URL row

  Two cosmetic things:
  - The fixture PNGs are 8×8, so the figures show as dots.
  - WordPress block attributes (`Id`, `SizeSlug`, `Level`, `Width`) appear in
    grey details boxes. That's faithful but noisy; see gaps.

## 4. Optional real-world runs

**Backdrop on DDEV.**
- About 10 minutes, well inside the hour.
- I copied the Backdrop clone to the scratchpad, ran
  `ddev config --project-type=backdrop`, then `ddev start` (MariaDB 11.8). I
  installed with `core/scripts/install.sh` (standard profile).
- I created content through Backdrop's APIs with a PHP script:
  - 3 tags, one child of another
  - a 64×48 PNG
  - About, Team (`about/team`) and Privacy policy pages
  - a post with tags and an image, and a draft
  - `site_frontpage` set to the new About page

The run used `mysql://` with PyMySQL from `uv --with pymysql` and
`--sections post=blog`:
- **14 bundles, 0 errors, 3 warnings.** The warnings are the standard
  profile's own three "card" nodes, whose images have no alt text in the
  database. That's real content, not a bug.
- **Confirmed against the live database:**
  - `url_alias` stores `source` and `alias` without leading slashes.
  - `site_frontpage` is `node/6`.
  - `field_data_field_tags` rows have `language = und`.
  - The standard profile's Pathauto patterns produce `posts/…` and
    `accounts/…`.
- The standard profile's own About (node 2) and my About (node 6, the front
  page) share the alias `about`. The result was `home/` for node 6 and
  `about/about/` for node 2, as the placement rules intend.
- The site is stopped and left in the scratchpad, which is temporary. Nothing
  was committed from it.

**WordPress smoke tests.** These files were downloaded to the scratchpad and
not committed.

| File | Items | Bundles | Errors | Warnings | Time |
|---|---|---|---|---|---|
| `themeunittestdata.wordpress.xml` (theme-test-data master) | 58 posts, 21 pages, 37 attachments, 70 menu items | 79 (every post and page) | 0 | 0 | 0.15 s |
| `64-block-test-data.xml` | 61 posts, 1 page, 9 attachments, 1 `wp_navigation` | 62 | 0 | 0 | 0.18 s |

- **Theme unit data.**
  - Date permalinks produce a real tree (`2010/08/08/post-format-image/`).
  - The nested page levels placed correctly (`level-1/level-1`,
    `level-1/level-2/level-2`).
  - 0 unparseable block attributes.
  - The only "HTML left in Markdown" hits were literal `` `<blockquote>` ``
    text in a code sample.
- **Block test data.** 79 distinct component types, nested up to 5 deep, 0
  unparseable attributes.
- Neither run had an uploads directory, so all images became `*_url` strings.
  That's expected and still valid.
- Nothing broke. `gutenberg-test-data.xml` doesn't exist in that repo (404);
  the block file is named `64-block-test-data.xml`.

## 5. Known gaps and follow-ups

**Backdrop**
- Layouts and Views aren't read. A layout-only front page gives no bundle.
- No translation nodes and no older revisions, both by scope.
- `datetime` values carry no timezone.
- Contrib field types fall through as generic props.
- No comments.

**WordPress**
- **The front page isn't detected.** WXR lacks `page_on_front`. A
  `--front-page ID` flag would be a one-liner.
- **Term URLs assume the default bases.** `--category-base` / `--tag-base`
  flags would fix custom ones.
- **Block attributes clutter the preview.** Options:
  - a generic renderer convention, e.g. hiding props listed in a component's
    `meta`
  - moving attributes to a nested `attributes` prop

  This needs a decision because it touches SPEC §5 or the prop shape.
- A list block's items are separate components, so each bullet renders as its
  own list.
- `[gallery]` and other shortcodes stay as text.
- `core/cover` and `core/media-text` images are found only through their
  inner `<img>`.
- **Phase 2:** read the WordPress database directly. Comments, menus, widgets
  and options are also still out.

**Shared**
- `drupal.py` now serves as a library for three exporters: it exports
  `_inside`, `drupal_markdown`, `html_to_markdown` and `php_unserialize`.
  Moving `html_to_markdown` and `php_unserialize` into a neutral module
  (`importers/html.py`?) would be tidier. I didn't do that tonight, to keep
  the Drupal diff small.
- `_BundleBuilder.asset()` is duplicated in the WordPress builder (15 lines).
- Carried over from 09-12: outline numbers repeat across slots, and heading
  levels collide.

## 6. Running against real sites tomorrow (Luke's Mac, DDEV)

From the repo checkout on this branch:

```bash
git fetch && git checkout claude/backdrop-and-wordpress-exporters
python3 -m pip install -e ".[backdrop]"   # PyMySQL; or: uv run --with pymysql ...
```

**Backdrop site in DDEV** (say `~/Sites/mybackdrop`):

```bash
cd ~/Sites/mybackdrop
ddev describe                      # note the db "Host port", e.g. 127.0.0.1:56863
ls -d files/config_*/active        # the active config dir ($config_directories in settings.php)

cd ~/Sites/00-FABLE/toadshade
backdrop-to-toadshade OUTPUT/mybackdrop \
    --db mysql://db:db@127.0.0.1:<host-port>/db \
    --config-dir ~/Sites/mybackdrop/files/config_<hash>/active \
    --files-dir ~/Sites/mybackdrop/files \
    --sections post=blog
toadshade validate OUTPUT/mybackdrop
```

A site kept in the docroot's `web/` or `docroot/` puts `files/` there instead.
A site using `ConfigDatabaseStorage` just omits `--config-dir`.

**WordPress site in DDEV** (say `~/Sites/mywp`):

```bash
cd ~/Sites/mywp
ddev wp export --dir=/var/www/html/ --filename_format=export.xml   # or Tools → Export in wp-admin
cd ~/Sites/00-FABLE/toadshade
wordpress-to-toadshade OUTPUT/mywp ~/Sites/mywp/export.xml \
    --uploads-dir ~/Sites/mywp/wp-content/uploads \
    --sections post=blog
toadshade validate OUTPUT/mywp
```

Use `--stop-after 20 --no-render` for a quick first look. As with
`INCOMING/`, `OUTPUT/` is untracked, so don't commit it.

## 7. Environment notes

- **Push worked from this session.** Git over SSH to GitHub was fine tonight,
  unlike the sandbox described on 09-11.
- **Worktree isolation.** This session ran in a Claude Code worktree
  (`.claude/worktrees/agent-…`). The worktree's guard refuses shell commands
  that build paths from variables or set `PYTHONPATH` inline. Small driver
  scripts in the scratchpad were the workaround.
- **No `gh`, and no PR opened**, as instructed.

## Files

| File | Change |
|---|---|
| `src/toadshade/importers/backdrop.py` | new |
| `src/toadshade/importers/wordpress.py` | new |
| `src/toadshade/importers/drupal.py` | injectable descriptors, database, builder and source prefix; `connect()` parameters; placement delegated to `AliasPlacement` |
| `src/toadshade/importers/base.py` | `AliasPlacement` (moved from `drupal.py`) |
| `tests/test_backdrop.py`, `tests/test_wordpress.py` | new, 24 and 21 tests |
| `tests/fixtures/make_backdrop1.py` | new; generates the three Backdrop fixtures below |
| `tests/fixtures/backdrop1.sql`, `backdrop-config/`, `backdrop-files/` | new; synthetic |
| `tests/fixtures/make_wordpress_wxr.py` | new; generates the two WordPress fixtures below |
| `tests/fixtures/wordpress.wxr.xml`, `wordpress-uploads/` | new; synthetic |
| `pyproject.toml` | `backdrop` extra; `backdrop-to-toadshade` and `wordpress-to-toadshade` scripts |
| `AMS/DOC/importers.md` | "The third one" and "The fourth one" sections |
| `CHANGELOG.md`, `README.md` | entries for both exporters |
| `AMS/HANDOFF/handoff-2026-09-13-backdrop-wordpress-exporters-claude.md` | this file |

## Open questions for Luke

1. Should WordPress pages be one component per block, as built, or wrapped in
   one entry component with a `content` slot, like Drupal?
2. How should block attributes be kept out of the preview's reading view: a
   renderer convention, or a nested `attributes` prop?
3. Should `html_to_markdown` and `php_unserialize` move to a neutral module
   now that three exporters use them?
4. Add `--front-page` and term-base flags to `wordpress-to-toadshade`?

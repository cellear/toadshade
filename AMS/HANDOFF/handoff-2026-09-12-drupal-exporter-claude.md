# Handoff: agent-handoff setup + `drupal-to-toadshade`

**Date:** 2026-09-12
**Author:** claude
**State:** two commits on branch `drupal-to-toadshade`, not merged or pushed
(Luke pushes). 74 tests passing (the 40 existing + 34 new).
**Prior handoff:** `handoff-2026-09-11-maintenance-claude.md`
**Concurrent handoff:** `handoff-2026-09-12-sfdug-wsod-cursor.md`, a Cursor
session that repaired the sample site's database (see section 3).

---

## 1. Handoff protocol set up

- `AMS/AGENT.md` is the protocol from https://github.com/cellear/agent-handoff
  (v1.1), with its paths changed to `AMS/HANDOFF/` and `AMS/DOC/`.
- Root `CLAUDE.md` holds one line pointing at it.
- Renames:
  - `AMS/HANDOFFS/` → `AMS/HANDOFF/`
  - `AMS/docs/` → `AMS/DOC/`
  - the 09-11 handoff → `handoff-2026-09-11-maintenance-claude.md`

**Deferred by Luke:** moving `docs/` into `AMS/` broke links in:
- `README.md` (lines 130, 166)
- `SPEC.md` (lines 300, 319, 342)
- the `apple_notes.py` docstring
- the 09-11 handoff's repo map
- `pyproject.toml`: its sdist `include` still lists `docs`

Also still undecided: whether project docs belong in `AMS/DOC/` at all.

## 2. `drupal-to-toadshade` built

Plan: `~/.claude/plans/let-s-do-that-later-mossy-falcon.md`. The design is
written up in `AMS/DOC/importers.md`, section "The second one".

**Decisions from Luke**
- **Target:** Drupal 10/11, read directly from the database (MySQL/MariaDB
  via the optional `toadshade[drupal]` extra).
- **Scope:** all fielded entities, with nodes, terms and users first.
- **Location:** in-tree.
- **Tree:** filed by path alias.
- **Revisions and languages:** default revision, default language.
- **User data:** keep everything, including `mail` and the `pass` hash. Luke
  handles PII separately.
- **Body text:** Markdown in `body` plus the untouched HTML in `body_html`.
- **Canvas pages:** backlog.

**How it works:** fetch layer `DrupalDatabase` → plain dicts → bundle layer
`DrupalToToadshade`. Paragraphs and media are embedded as child components.
References to nodes, terms and users become URL strings.

**Changes from the plan**
- Child components go in slots named after their field (e.g.
  `field_sections`), not a single `content` slot. That's closer to Drupal and
  better for re-import.
- `connect()` also accepts `sqlite:///` URLs. The tests and CLI test use them.

**Verified**
- `python3 -m pytest tests -q`: 66 passed.
- The CLI run against the fixture wrote 11 bundles, and `toadshade validate`
  reported 0 errors and 0 warnings.

**Verified on a real site:** Luke's sample site, a DDEV Drupal CMS 11 site
(MySQL 8) at `INCOMING/drupal_sample_sfdug`.
- **Connection:**
  `--db mysql://db:db@127.0.0.1:49348/db --files-dir INCOMING/drupal_sample_sfdug/web/sites/default/files`.
  The host port comes from `ddev describe` and changes when DDEV restarts.
- **Result:** 87 bundles (79 sessions, 5 people, 2 pages, 1 user) in under a
  second. `toadshade validate` reported 0 errors and 1 warning, a real image on
  the site with no alt text.
- **Checked against the database:**
  - 78 text fields converted with no leftover HTML tags.
  - No missing files.
  - The front page (`/home`, an alias) became `home/`.
  - Four of the five people have no fields in the database either.
  - The site has no Canvas pages and no taxonomy terms.
- **Fix made:** Smart Date (`smartdate`) start and end values were raw Unix
  timestamps. They're now converted to ISO dates, with a test added.
- **Housekeeping:** `INCOMING/` is untracked but not git-ignored. Don't commit
  it. Output went to the session scratchpad, not the repo.

## 3. Luke's feedback on the SFDUG output, and what changed

The export was copied to `OUTPUT/sfdug/`. Like `INCOMING/`, `OUTPUT/` is
untracked and not git-ignored.

**1. "The folders should follow the URL tree."** They did, but every SFDUG
alias is one level deep (Pathauto `/[node:title]`), and the menus only link
to Views listings. Luke chose section folders:
- New `--sections session=meetings,person=people` option. It applies only to
  one-level aliases that aren't parents; the JSON `alias` is unchanged.
- The SFDUG export is now `meetings/` (79), `people/` (5), `home`,
  `privacy-policy`, `user/1`.

**2. "The previews should look more like the real pages."** Luke chose a
page-like layout. That meant **amending SPEC §5**, which had said "a proof
sheet, not a mockup". The new §5 adds a "Rendering conventions" table and the
sanitizing rules.

`render.py` was rewritten, still stdlib-only and read top to bottom:
- a generic page layout
- a CSS-only **Show structure** switch that reveals outline numbers, types
  and ids
- `body_html` rendered through an allow-list sanitizer; nothing loads from
  outside the bundle, and images must be in `assets/`
- ISO dates and `{value, end_value}` ranges formatted
- a video URL prop turns the component's image into a play card
- the page title is never repeated

The file grew from about 290 to about 430 lines. That strains the "stays
short" invariant, so decide whether it's acceptable.

**Exporter changes supporting the new look**
- Bookkeeping columns moved from props to `meta`: `uid`, `promote`, `sticky`,
  term `weight`, and user `mail`, `pass`, `init`, `timezone`, `access`,
  `login`, and language preferences.
- Media thumbnails became assets, except when the thumbnail is the item's own
  image.

**Fixture:** added a remote-video media item and a one-level page
(`/privacy-policy`). It now produces 12 bundles.

**Docs:** README, CHANGELOG and the `pyproject.toml` description now say
"preview" rather than "proof sheet".

**Verified**
- 74 tests pass.
- The SFDUG re-export has 0 errors and 1 warning.
- Headless Chrome screenshots of a session, a person and the home page read
  as web pages.

**Luke's verdict:** "It looks fine now." Committed.

**Sample site repaired meanwhile:** a Cursor session fixed a WSOD on the
sample site. It removed modules that had been deleted from the code (CRM,
Name, Event, Registration, Mailchimp) from `core.extension`, along with their
config. See its handoff. The exporter isn't affected: it reads field
definitions only for the entity types it exports, never "every table". Ghost
tables left in MySQL are harmless to it. If Luke re-adds `drupal/crm` or
`drupal/name` to exercise contrib field types, `crm_contact` would need an
`ENTITY_TYPES` entry, and `name` fields would export as dict props.

**Open**
- Reference props are bare URLs, so speakers show as `/kristen-pol`. They
  could use the SPEC's `Label → /url` shape to show "Kristen Pol".
- Links to other bundles (`/kristen-pol`) don't resolve from `file://`.
- `INCOMING/` and `OUTPUT/` are untracked and not git-ignored. Luke hasn't
  decided whether to ignore them.

## Files

| File | Change |
|---|---|
| `AMS/AGENT.md`, `CLAUDE.md` | new; the handoff protocol (commit 1) |
| `docs/` → `AMS/DOC/`, 09-11 handoff | moved (commit 1) |
| `src/toadshade/importers/drupal.py` | new |
| `src/toadshade/importers/base.py` | `slugify` moved here |
| `src/toadshade/importers/apple_notes.py` | imports and re-exports `slugify` |
| `src/toadshade/render.py` | rewritten as a page-like preview |
| `SPEC.md` | §5 amended: page-like preview, rendering conventions, sanitizing |
| `README.md`, `CHANGELOG.md` | "preview" wording; Drupal exporter entry |
| `pyproject.toml` | `drupal` extra, `drupal-to-toadshade` script, description |
| `examples/trail-guide/trail-guide.html` | regenerated with the new renderer |
| `tests/test_drupal.py` | new |
| `tests/test_bundle.py` | tests for sanitizing, the structure switch, dates, and title repeats |
| `tests/fixtures/make_drupal10.py` | new; generates the two below |
| `tests/fixtures/drupal10.sql`, `tests/fixtures/drupal-files/` | new; synthetic content only |
| `AMS/DOC/importers.md` | Drupal section |
| `AMS/HANDOFF/handoff-2026-09-12-*` | this handoff and Cursor's WSOD handoff |

## Known issues and follow-ups

1. **Outline numbers repeat across slots.** In `base.markdown_for` and
   `render.py`, each slot restarts at `.1`. A component with several slots
   gets several siblings numbered `1.1`, which breaks "section 1.1 has a
   typo". This existed before, but Apple Notes only ever has one slot. The fix
   belongs in both files and should be a deliberate change.
2. ~~**`body_html` in the preview.**~~ Fixed in section 3 above: it's now
   rendered as sanitized HTML.
3. **Heading levels collide.** Headings inside a converted body (e.g.
   `### Along the creek`) use the same levels as the Markdown outline
   headings.
4. **Unsupported content:**
   - Link `options` are kept only when non-empty.
   - Layout Builder sections aren't read.
   - `canvas_page` is on the backlog.
   - No Drupal 7.
5. **Open questions carried over from 09-11:** PyPI, whether exporters get
   their own repos, and format versioning.

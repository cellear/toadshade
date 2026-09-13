# Handoff: SFDUG sample site WSOD

**Date:** 2026-09-12
**Author:** cursor
**State:** homepage is up again. Database-only cleanup; nothing committed.
**Prior handoff:** `handoff-2026-09-12-drupal-exporter-claude.md`

---

## What happened

`https://drupal-sample-sfdug.ddev.site/` returned "The website encountered an unexpected error." Watchdog:

```
PluginNotFoundException: Unable to determine class for field type 'name'
found in the 'field.storage.crm_contact.full_name' configuration
```

The active database still had Drupal CRM / Name Field / Event / Registration / Mailchimp marked enabled in `core.extension`, plus all of their config. Those modules are not in this project's `composer.json` or `web/modules/contrib`, and they are not in `config/sync`.

Likely leftover from a Drupal CMS installer recipe set that was applied to this DB, then the code was rebuilt without those packages.

## What we did

Used DDEV (`ddev drush`, `ddev` on PATH).

1. Removed the missing modules from `core.extension` and `system.schema`:
   `address`, `crm`, `event`, `inline_entity_form`, `mailchimp`, `mailchimp_lists`, `mailchimp_signup`, `name`, `primary_entity_reference`, `registration`, `registration_confirmation`.
2. Deleted the orphaned config rows from the `config` table (CRM field storages, Name formats, registration views, etc.). Entity load via Drush would have hit the same missing-plugin exception.
3. Uninstalled leftover core modules that were only in the DB: `datetime_range`, `telephone`.
4. `ddev drush cache:rebuild`.

Verified: `curl` → HTTP 200; browser title `Home | SFDUG` with meeting/archive content.

## Remaining config drift (not blocking)

`ddev drush config:status` still reports:

- `canvas.folder.2e38eeb3-…` Different
- `canvas.folder.67c2f056-…` Different
- `system.site` Different (active mail is `admin@example.com`; sync is `noreply@sfdug.org`)

Ghost-module **tables** may still be in MySQL. Harmless for front-end, noisy for a Drupal→Toadshade import if the importer walks every table.

## Open questions

- Re-require `drupal/crm` + `drupal/name` if this sample is supposed to exercise contrib field types, or leave them out and treat this as a clean SFDUG site.
- Whether to `drush config:import` the three remaining diffs.

## Files

None in the repo. Database only.

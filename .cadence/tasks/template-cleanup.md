# Template Cleanup Workboard

Status: active
Last updated: 2026-04-20
Primary area: repository-wide

## Scope

Remove leftover FastAPI full-stack template code, routes, assets, and branding
that are unrelated to the Bilibili ingestion product.

## Active milestone

Phase 2: finish schema cleanup and audit the remaining shared operator/auth
surfaces.

## Work items

| ID | Status | Task | Notes |
| --- | --- | --- | --- |
| TMP-001 | done | Remove template `items` CRUD surface | Deleted backend `/items` route, related frontend route/components/tests, and dependent runtime/test references |
| TMP-002 | done | Remove template branding from primary docs and UI chrome | Rewrote top-level/frontend README, changed page titles/footer/logo, renamed root package metadata, replaced favicon |
| TMP-003 | done | Collapse dormant template DB artifacts into one baseline schema | Historical Alembic revisions were removed and replaced with `20260420_01_initialize_current_schema.py`; older local DBs should be reset once |
| TMP-004 | pending | Audit remaining auth/admin/settings surfaces for project-specific wording | Auth and user-management remain useful, but some copy/flows may still be template-shaped |
| TMP-005 | done | Remove unused Redis scaffolding | Current runtime is DB-backed; deleted unused `REDIS_URL` config plus local Compose Redis service and env wiring |

## Current guidance

- Treat `items` as removed product scope. Do not reintroduce generic CRUD pages.
- Keep authentication, user settings, and admin pages unless they are clearly no
  longer needed by the operator console.
- Prefer deleting dead assets and code paths outright instead of leaving unused
  generated client surfaces or screenshots behind.
- Treat the new Alembic baseline as the source of truth for fresh local DB
  bootstrap. If the pre-production schema is squashed again later, do it
  intentionally from an empty local database rather than resurrecting the old
  revision chain.

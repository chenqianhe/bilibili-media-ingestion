# Template Cleanup Handoff

Last updated: 2026-04-20

## Current state

- The generic template `items` CRUD surface is gone from backend routes,
  frontend routes, tests, and generated client output.
- The operator console sidebar no longer links to any generic CRUD page, and the
  generated TanStack route tree no longer contains `/_layout/items`.
- Top-level repository branding no longer presents the repo as the FastAPI
  template:
  - `README.md` and `frontend/README.md` are project-specific
  - root `package.json` is renamed to `bili-media-ingestion-service`
  - login/signup/recover/reset/admin/settings page titles are project-specific
  - the shared footer and logo are project-specific
  - old FastAPI logo assets and template screenshots have been removed
- Authentication, user settings, admin user management, password recovery, and
  the local `private` user-creation route are still present. They were kept
  intentionally because they still support testing and operator access flows.
- The pre-production schema history has been squashed into a single Alembic
  baseline at `backend/app/alembic/versions/20260420_01_initialize_current_schema.py`.
  The old template-era revisions are gone; any local DB created from that old
  chain should be reset once instead of upgraded in place.
- The repository no longer carries Redis as an active runtime dependency.
  Worker orchestration is DB-backed, so the local Compose Redis service and
  `REDIS_URL` config were removed instead of being kept as unused scaffolding.

## Where to continue

1. Review auth/admin/settings UX copy and routes to decide whether all of them
   still belong in the operator product or should be simplified further.
2. If the generated frontend client is kept long-term, keep using OpenAPI
   regeneration after backend cleanup so deleted routes do not linger in
   `frontend/src/client/`.
3. If the schema is squashed again before production, do it from an empty local
   database and keep the reset expectation explicit in docs instead of trying to
   preserve this pre-production baseline history.

## Validation checklist

- Regenerate `frontend/openapi.json` from `backend/` after backend route changes.
- Run `npm --workspace frontend run generate-client` after schema changes.
- Run targeted backend auth/users/ingest/videos tests after removing shared
  models or API routes.
- For future schema squashes, verify a fresh empty Postgres DB with
  `alembic upgrade head` before handing off.
- Run `npm --workspace frontend run build` so route-tree regeneration and dead
  import cleanup happen together.
- Keep `git diff --check` clean.

## Known boundaries

- The repository now assumes fresh local DB bootstrap from the single
  `20260420_01` Alembic baseline. Older local databases using removed revision
  ids are intentionally out of migration support and should be recreated.
- The repo still contains generic authentication and user-management code from
  the template base, but these flows are currently functional and still used by
  tests and operator access.

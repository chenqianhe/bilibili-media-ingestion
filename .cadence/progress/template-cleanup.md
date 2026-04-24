# Template Cleanup Progress Log

## 2026-04-20

- Removed the template `items` product surface from both backend and frontend:
  - deleted `backend/app/api/routes/items.py`
  - removed `Item` runtime models and CRUD helpers from `backend/app/models.py`
    and `backend/app/crud.py`
  - removed user-delete coupling to `Item` rows from
    `backend/app/api/routes/users.py`
  - removed item test fixtures and route regressions from `backend/tests/`
  - deleted the frontend `/items` route, item CRUD components, pending state,
    and Playwright spec
- Regenerated the frontend OpenAPI client after removing the `/items` route, so
  `frontend/src/client/` no longer exposes item CRUD types or service methods.
- Refreshed the operator-console route tree through a frontend production build,
  removing the deleted `/items` route from generated router output.
- Replaced remaining top-level template branding:
  - rewrote `README.md` and `frontend/README.md`
  - renamed the root package metadata in `package.json`
  - removed `FastAPI Template` page titles from auth/admin/settings routes
  - replaced the shared footer and logo branding with project-specific UI
  - replaced the old favicon with a local SVG mark and removed unused FastAPI
    logo assets plus template screenshots under `img/`
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run python -c "import app.main, json; print(json.dumps(app.main.app.openapi()))" > ../frontend/openapi.json`
  - `npm --workspace frontend run generate-client`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/api/main.py app/api/routes/users.py app/core/db.py app/crud.py app/models.py tests/conftest.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/routes/test_login.py tests/api/routes/test_users.py tests/api/routes/test_ingest.py tests/api/routes/test_videos.py -q`
  - `npm --workspace frontend run build`
  - `git diff --check`
- Latest result:
  - `52 passed` on the auth/users/ingest/videos backend route slice after removing the template `items` surface
  - The frontend production build passes after regenerating the client and route tree; the existing large vendor chunk warning remains
  - Runtime template residue is now largely limited to follow-up product-surface decisions in auth/admin/settings

- Collapsed the pre-production Alembic history into a single baseline migration:
  - removed the old template-era and feature-by-feature revision chain under `backend/app/alembic/versions/`
  - generated `backend/app/alembic/versions/20260420_01_initialize_current_schema.py` against the current SQLModel metadata
  - preserved important cascade and partial-index behavior in the squashed baseline, including the ready/uploaded source-asset SHA256 dedupe index and auxiliary-data indexes
  - updated the local-development guidance so fresh databases continue to use `alembic upgrade head`, while existing local DBs from the removed revision chain are expected to be dropped and recreated once
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/alembic/env.py app/alembic/versions/20260420_01_initialize_current_schema.py`
  - `env APP_ENV=test POSTGRES_DB=bili_ingest_migration_baseline UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head`
  - `env APP_ENV=test POSTGRES_DB=bili_ingest_migration_baseline UV_CACHE_DIR=/tmp/uv-cache uv run alembic current`
- Latest result:
  - Fresh empty Postgres databases now bootstrap cleanly from the single `20260420_01` baseline
  - Older local databases stamped with removed revision ids are no longer expected to upgrade in place; reset them instead

- Removed the last unused Redis scaffolding from the active runtime surface:
  - deleted the dead `REDIS_URL` setting from `backend/app/core/config.py`
  - removed the tracked `REDIS_URL` template entry from the root `.env`
  - removed the local `redis` service plus now-unused `REDIS_URL` environment
    injection from `compose.override.yml`
  - updated `development.md` so the documented local stack no longer claims a
    Redis dependency that the runtime does not actually use
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check backend/app/core/config.py`
  - `docker compose config -q`
  - `git diff --check`
- Latest result:
  - The project no longer carries Redis as a fake runtime dependency
  - Future Redis adoption, if it ever happens, can be reintroduced against a
    concrete need such as caching, distributed locks, or pub/sub

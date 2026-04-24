# Bilibili Ingestion Handoff

Last updated: 2026-04-20

## Current state

- Job submission, rights review, audit events, asset listing, and app-signed download descriptors are already wired through FastAPI.
- Metadata execution now lives in `backend/app/services/metadata_ingest.py` and consumes normalized provider output from `backend/app/crawler/bilibili_metadata.py`.
- A real metadata worker now exists at `backend/app/workers/metadata_ingest.py`; it claims unstarted `pending` / pre-metadata `blocked_by_policy` jobs from Postgres and runs the metadata orchestration layer.
- A real public metadata adapter now exists at `backend/app/crawler/bilibili_metadata_http.py`; it uses Bilibili HTTP endpoints, retries transient failures, and classifies fetch errors into stable `error_code` values.
- A real source download worker now exists at `backend/app/workers/download_ingest.py`; it claims `metadata_ready` jobs with `finished_at IS NULL`, re-validates rights, downloads source files, and creates `media_assets` rows with local staging metadata.
- The default downloader adapter now lives at `backend/app/downloader/yt_dlp_adapter.py`; compose builds install `yt-dlp` into the backend image for `download-worker`, and the adapter now supports raw cookie headers, Netscape cookie files, and `--cookies-from-browser`.
- The public metadata adapter now also accepts an optional `BILIBILI_COOKIE_HEADER`, so metadata fetches can reuse the same authenticated session when anonymous requests are rate-limited or gated.
- A real source upload worker now exists at `backend/app/workers/upload_ingest.py`; it claims `source_downloaded` jobs, uploads `downloaded` assets to object storage, verifies them with `HeadObject`, and advances jobs to `source_uploaded`.
- The default object-storage client now lives at `backend/app/uploader/s3_multipart.py`; it uses botocore multipart upload, aborts incomplete uploads, deletes objects on verification mismatch, and reuses previously uploaded identical source assets.
- A real media-processing worker now exists at `backend/app/workers/media_processing.py`; it claims `source_uploaded` jobs, downloads uploaded source objects from storage, probes them with `ffprobe`, creates normalized MP4 + thumbnail derivatives, and advances jobs to `completed`.
- The default media-processing adapter now lives at `backend/app/processor/ffmpeg.py`; it wraps `ffprobe`/`ffmpeg` CLI execution behind a narrow interface for orchestration tests.
- A real live object-storage smoke test now exists at `backend/tests/services/test_live_object_storage_smoke.py`; it is gated behind `RUN_LIVE_S3_SMOKE=1` and standard `S3_*` env vars (with `ENDPOINT_URL` / `ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` / `BUCKET_NAME` fallbacks).
- Live validation has already been run successfully against a SeaweedFS S3 endpoint on `2026-04-20`.
- Root `.env.local` is now a gitignored optional override for backend settings and compose env files; local SeaweedFS/MinIO switches can live there without editing tracked `.env`.
- Local compose now mounts `backend/.secrets` into `download-worker` at `/app/backend/.secrets`, so cookie files can be supplied without baking them into the image.
- Host-based pytest now sets `APP_ENV=test` in `backend/tests/conftest.py` before importing app settings, so `backend/app/core/config.py` loads tracked root `.env.test` defaults plus optional `.env.test.local` overrides for direct `uv run pytest` executions.
- A profiled `test-db` service now exists in `compose.override.yml`; it binds Postgres to `localhost:55432` for host-based ingestion validation without changing the main local app stack's `db:5432` wiring.
- Metadata/download/upload/processing workers now reclaim stale in-progress jobs after stage-specific timeout settings. Shared reclaim logic lives in `backend/app/workers/stale_reclaim.py`, and worker regressions cover both stale recovery and skipping fresh in-progress jobs.
- `backend/tests/services/test_live_object_storage_smoke.py` now falls back to app settings, so `RUN_LIVE_S3_SMOKE` and standard `S3_*` values in `.env.local` are picked up automatically.
- `backend/app/uploader/s3_multipart.py` had a real protocol bug fixed on `2026-04-20`: botocore clients do not support `download_file()`, so downloads now stream via `get_object()`.
- `botocore` now lives in `backend/pyproject.toml` / `uv.lock`, so local `uv sync` environments can run upload/processing storage code without manual `pip install botocore`.
- `compose.yml` and `compose.override.yml` now include a `metadata-worker` service using the backend image.
- `compose.yml` and `compose.override.yml` now also include a `download-worker` service using the backend image.
- `compose.yml` and `compose.override.yml` now also include an `upload-worker` service using the backend image.
- `compose.yml` and `compose.override.yml` now also include a `processing-worker` service using the backend image.
- `backend/Dockerfile` now installs `ffmpeg`, so the backend image contains both `ffmpeg` and `ffprobe`.
- `create_hls=true` no longer fails the job. `backend/app/processor/ffmpeg.py` and `backend/app/services/media_processing.py` now emit a `proxy_mp4`, HLS media playlist(s), HLS segment rows, and an HLS master manifest when the option is enabled.
- `backend/app/api/routes/media.py` now supports:
  - `POST /media/assets/{asset_id}/playback-url` for signed playback URLs
  - `GET /media/assets/{asset_id}/playback` for proxied byte delivery
  - HLS manifest rewriting so nested playlists/segments resolve through signed playback URLs instead of raw storage keys
- Metadata ingest now optionally fetches comments, danmaku, and subtitles through `backend/app/crawler/bilibili_auxiliary_http.py`, persists them into `video_comments`, `video_danmaku`, and `video_subtitles`, and records fetch counts in `ingest_jobs.progress["auxiliary"]`.
- Comment crawls now also write an explicit completeness summary into `ingest_jobs.progress["auxiliary"]["comments"]`: `expected_count`, `fetched_count`, `fallback_used`, and `partial` are stable fields alongside the existing comment-image counters.
- Danmaku crawls are no longer limited to the current XML pool. `backend/app/crawler/bilibili_auxiliary_http.py` now queries history-month indexes, fetches per-day history protobuf segments when available, merges them with the current XML snapshot, and writes an explicit completeness summary into `ingest_jobs.progress["auxiliary"]["danmaku"]`.
- `backend/app/crawler/bilibili_danmaku_proto.py` now provides the protobuf decoder for Bilibili history segments; no extra runtime dependency was added for this.
- `video_danmaku` has been rekeyed to an internal UUID primary key in `backend/app/ingest_models.py`; the provider-facing ID now lives in nullable `danmaku_id`, and rows also carry `source` plus `history_date` columns so coverage can be reasoned about later.
- The read surface is no longer comments-only. `backend/app/api/routes/videos.py` now supports:
  - `/videos/` for video catalog browsing
  - `/videos/{bvid}` for video detail reads
  - `/videos/{bvid}/comments` with optional `root` and `parent` filters plus a route-level `completeness` object
  - `/videos/{bvid}/comment-images` with optional `rpid`, `root`, `parent`, and `storage_status` filters plus route-level comments completeness
  - `/videos/{bvid}/danmaku` with optional `cid`, `source`, and `history_date` filters plus pagination and a route-level `completeness` object
  - `/videos/{bvid}/subtitles` with optional `cid` and `lang` filters plus a route-level `completeness` object
- `backend/app/api/routes/ingest.py` now supports `GET /ingest/jobs` so the operator UI can browse recent jobs without knowing job ids in advance.
- `fetch_subtitles` is enabled by default again now that subtitle crawling exists, and `download-worker` now skips `metadata_ready` jobs whose `options.download_video` is false.
- `backend/app/services/rights.py` now auto-resumes eligible `blocked_by_policy` jobs during rights review approval:
  - pre-metadata jobs return to `pending`
  - metadata/download-blocked jobs return to `metadata_ready`
  - matching `videos.rights_status` rows are refreshed for both bvid-specific and owner-level approvals
- The targeted ingestion test suite and the live object-storage smoke now both pass against the tracked host-pytest defaults in `.env.test` on `localhost:55432`, without temporary `POSTGRES_*` overrides.
- The repository now keeps a single squashed Alembic baseline at `backend/app/alembic/versions/20260420_01_initialize_current_schema.py` for the current pre-production schema. Older local databases that still point at the removed revision chain should be reset once instead of upgraded in place.
- A first external PostgreSQL instance has now been initialized successfully from that `20260420_01` baseline and reports `alembic current -> 20260420_01 (head)`. Treat that baseline as fixed history from here; future schema work should add normal forward Alembic revisions.
- The tracked root `.env` file is now grouped as a project-specific deployment template, and `backend/README.md` now contains a production environment checklist. Real secrets should still live outside git.
- The frontend is no longer a template shell for this workflow. It now exposes:
  - an ingest operations dashboard at `frontend/src/routes/_layout/index.tsx`
  - a dedicated video browser at `frontend/src/routes/_layout/videos.tsx`
  - manual typed API bindings in `frontend/src/lib/ingestionApi.ts`
  - shared playback/status components in `frontend/src/components/Ingestion/`
  - a data-workspace-oriented `/videos` layout that prioritizes completeness, filters, ingest health, and raw payload inspection over large playback chrome
  - a dedicated `Comment Images` tab inside `/videos` for storage-outcome filtering, image inventory browsing, and per-comment error/asset inspection

## Where to continue

1. Continue the remaining browse/query work after comment-image inventory: tighter danmaku filters when specific analyst workflows emerge, comment-image export/drill-down only if operators ask for it, and possibly ingest-job drill-down shortcuts from the video read surface.
2. Decide whether subtitle completeness should stay availability-style or gain a stronger provider-backed contract comparable to comments/danmaku.
3. If playback distribution needs to move beyond internal proxying, consider direct object-storage presign flows or a dedicated streaming edge path. The current proxy/HLS implementation is app-backed rather than storage-presigned.
4. If frontend build ergonomics become a concern, the main remaining frontend cleanup is chunking/code-splitting; the current operator console builds cleanly but still emits the large-vendor-chunk warning.

## Validation checklist

- Run targeted backend tests for bvid parsing, rights policy, storage keys, ingest routes, and metadata ingest.
- Run `docker compose config -q` after editing compose/env wiring so `.env.local` interpolation and override syntax fail fast.
- Keep `git diff --check` clean.
- Update `.cadence/progress/bilibili-ingestion.md` with actual command results before handing off.
- For host-based pytest runs, bring up the dedicated local test DB with `docker compose --profile test up -d test-db` if nothing is already listening on `localhost:55432`.
- Run host-based DB-mutating suites serially; the current fixtures clear shared tables in the same `localhost:55432` database.
- If a local DB predates the squashed baseline, recreate it before relying on `alembic upgrade head`.
- For any non-local environment that is already initialized from `20260420_01`, do not squash or rewrite migration history again; add a new Alembic revision for every schema change.
- Before using the default Compose stack with an external PostgreSQL instance, remove or parameterize the current `POSTGRES_SERVER=db` override in `compose.yml`; right now that wiring is still local-db-oriented.
- Run `bun run build` from the repository root after touching the operator console; it delegates to the `frontend` workspace build. The first Vite-backed build after adding routes refreshes `frontend/src/routeTree.gen.ts` before plain `tsc` passes. The agent environment used an equivalent Vite/npm invocation once only because Bun was not installed there; Bun is the repo-standard workflow now.
- Current known-good test command:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_media_processing.py tests/api/routes/test_media.py tests/api/routes/test_videos.py tests/api/routes/test_ingest.py -q`
- Current known-good live object-storage smoke command:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_live_object_storage_smoke.py -q`

## Known boundaries

- Media signed URLs for download still resolve to an application descriptor endpoint instead of object-storage presigned URLs. Playback URLs now proxy bytes through the app and rewrite HLS manifests, but they are still app-backed rather than direct storage presigns.
- Rights approval is enforced for video download decisions, and metadata/download/upload/processing workers are now connected. Live object-storage E2E coverage now exists, but it is opt-in and credentialed rather than part of the default local suite.
- The repository `.env` still points at `localhost:5432` for general non-pytest local runs. Host-based pytest now switches to `.env.test` automatically, but other bare-metal commands still use `.env` unless you export overrides.
- Historical per-feature Alembic revisions are intentionally gone. That keeps the repo aligned with the merged pre-production schema, but it also means old local databases cannot rely on in-place migration from the removed revision ids.
- The baseline is no longer just a local convenience: at least one external PostgreSQL environment is already stamped at `20260420_01`, so future schema evolution must be strictly forward-only via new revisions.
- The default Compose deployment wiring is not yet external-Postgres-ready because backend/prestart/worker services still override `POSTGRES_SERVER` to `db` for the bundled local container flow.
- Stale reclaim is timeout-based only. There is still no heartbeat/lease renewal while a job is actively running, so extremely long legitimate downloads/uploads/processing runs could still be double-claimed if you set the stage timeout too low.
- Comments/danmaku/subtitle crawling now runs during metadata ingestion and persists into PostgreSQL. Those read routes now expose a route-level `completeness` object derived from the latest relevant ingest job, but there are still no object-storage sidecar assets for auxiliary fetch outputs.
- Comment-image inventory is now independently browseable in both the API and `/videos`, but it still relies on the source image URL for thumbnail display; there is no dedicated image proxy/presign path beyond the existing linked `comment_image` asset metadata.
- Subtitle completeness is currently availability-style only: the route now exposes crawl source job + stored track counts/languages, but there is still no provider-backed `expected_count`/`partial` signal equivalent to comments or danmaku.
- Local placeholder secrets still emit warnings (`SECRET_KEY`, `POSTGRES_PASSWORD`, `FIRST_SUPERUSER_PASSWORD`), and JWT signing still warns until `MEDIA_SIGNING_SECRET` is set to a >=32-byte key.
- Bare-metal local runs of `python -m app.workers.download_ingest` still require a `yt-dlp` binary in `PATH`; the compose worker gets it from the backend image build.
- `YT_DLP_COOKIES_FROM_BROWSER` is mainly useful for bare-metal worker runs. The compose `download-worker` cannot see host browser profiles unless you add extra mounts yourself; for containerized runs prefer `BILIBILI_COOKIE_HEADER` or a cookie file under `backend/.secrets/`.
- Bare-metal local runs of `python -m app.workers.upload_ingest` and `python -m app.workers.media_processing` now get `botocore` from the backend dependency set after `uv sync`, but media processing still requires external `ffmpeg` / `ffprobe` binaries in `PATH`.
- The operator console currently uses the app proxy and native browser HLS support only. Browsers without native HLS fall back to proxy MP4; there is no `hls.js` client integration yet.

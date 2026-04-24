# Bilibili Ingestion Progress Log

## 2026-04-19

- Added the ingestion domain schema, migration, rights service, ingest submission endpoints, media asset routes, storage key helpers, audit trail, and initial tests.
- Added an executable metadata ingestion service that upserts uploader/video/page/stat records and advances job state based on authorization and requested download behavior.
- Established `.cadence/` as the repository-level handoff surface with workboard, progress log, and continuation notes.
- Fixed two execution-path issues uncovered during validation:
  - metadata test fixtures were reusing a constant `aid`, colliding with the unique constraint on `videos.aid`
  - metadata failure handling needed an explicit `session.rollback()` before marking the job failed
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/crawler/bilibili_metadata.py app/services/metadata_ingest.py tests/services/test_metadata_ingest.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache POSTGRES_SERVER=localhost POSTGRES_PORT=55432 POSTGRES_DB=app POSTGRES_USER=postgres POSTGRES_PASSWORD=changethis uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/api/routes/test_ingest.py -q`
  - `python -m compileall app tests`
  - `git diff --check`
- Latest result: `14 passed` on the targeted ingestion suite. Warnings remain for placeholder secrets (`SECRET_KEY`, `POSTGRES_PASSWORD`, `FIRST_SUPERUSER_PASSWORD`) and the short JWT signing fallback when `MEDIA_SIGNING_SECRET` is unset.

- Wired a real DB-backed metadata worker entrypoint in `backend/app/workers/metadata_ingest.py`; it claims unstarted metadata jobs with `FOR UPDATE SKIP LOCKED`, runs `process_metadata_ingest_job`, and is exposed as a standalone `python -m app.workers.metadata_ingest` process.
- Added a real Bilibili HTTP metadata adapter in `backend/app/crawler/bilibili_metadata_http.py` with retry for transient upstream failures, optional tag fetch, normalized page/stat/uploader parsing, and typed failure classification.
- Extended metadata execution failure handling to preserve provider-specific `error_code` values and increment `retry_count`.
- Added compose services named `metadata-worker` in both `compose.yml` and `compose.override.yml` so metadata ingestion can run as an actual sidecar worker in local/prod stacks.
- Added tests covering:
  - HTTP provider normalization and tag-endpoint degradation
  - metadata failure classification
  - metadata worker priority order and skipping already-started blocked jobs
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/crawler/bilibili_metadata.py app/crawler/bilibili_metadata_http.py app/services/metadata_ingest.py app/workers/metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_metadata_ingest.py`
  - `python -m compileall app tests`
  - `env UV_CACHE_DIR=/tmp/uv-cache POSTGRES_SERVER=localhost POSTGRES_PORT=55432 POSTGRES_DB=app POSTGRES_USER=postgres POSTGRES_PASSWORD=changethis uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/api/routes/test_ingest.py -q`
  - `git diff --check`
- Latest result: `20 passed` on the expanded ingestion suite. The pytest command required unsandboxed access to the local Postgres instance on `localhost:55432`; warnings remain for placeholder secrets and the short JWT signing fallback when `MEDIA_SIGNING_SECRET` is unset.

- Added source download orchestration in `backend/app/services/download_ingest.py`; it re-checks rights before download, stages files under `INGEST_TMP_DIR/jobs/<job_id>/source`, computes SHA256/size, creates `media_assets` rows, and advances jobs to `source_downloaded`.
- Added a DB-backed download worker entrypoint in `backend/app/workers/download_ingest.py` that claims `metadata_ready` jobs with `finished_at IS NULL`.
- Added a default `yt-dlp` CLI adapter in `backend/app/downloader/yt_dlp_adapter.py`; adapter failures are classified and rolled into ingest job `error_code` / `retry_count`.
- Added a `download-worker` service to both compose files and updated `backend/Dockerfile` to install `yt-dlp` inside the backend image so the compose worker has the binary it needs.
- Added download-stage tests covering:
  - source asset persistence
  - split audio/video source stream rows
  - policy re-blocking before download
  - rollback + workspace cleanup on failure
  - priority ordering for the download worker
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/downloader/base.py app/downloader/yt_dlp_adapter.py app/services/download_ingest.py app/workers/download_ingest.py tests/services/test_download_ingest.py`
  - `python -m compileall app tests`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run python -m app.workers.download_ingest --help`
  - `env UV_CACHE_DIR=/tmp/uv-cache POSTGRES_SERVER=localhost POSTGRES_PORT=55432 POSTGRES_DB=app POSTGRES_USER=postgres POSTGRES_PASSWORD=changethis uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_download_ingest.py tests/api/routes/test_ingest.py -q`
  - `git diff --check`
- Latest result: `25 passed` on the expanded ingestion suite. The pytest command still required unsandboxed access to the local Postgres instance on `localhost:55432`; warnings remain for placeholder secrets and the short JWT signing fallback when `MEDIA_SIGNING_SECRET` is unset.

- Added object-storage upload orchestration in `backend/app/services/upload_ingest.py`; it claims `downloaded` assets for a `source_downloaded` job, uploads them to object storage, verifies them via `HeadObject`, updates `media_assets`, and advances jobs to `source_uploaded`.
- Added a botocore-backed multipart storage client in `backend/app/uploader/s3_multipart.py`; it streams parts from disk, aborts incomplete uploads on failure, deletes remote objects after post-complete verification mismatch, and reuses already-uploaded identical source objects to avoid the `sha256 + asset_type` uniqueness collision.
- Added a DB-backed upload worker entrypoint in `backend/app/workers/upload_ingest.py` and a matching `upload-worker` compose service for local/prod stacks.
- Updated `backend/Dockerfile` to install `botocore` in the backend image so the compose upload worker can talk to MinIO/S3.
- Added upload-stage tests covering:
  - successful source upload and staging cleanup
  - rollback on storage verification failure
  - upload worker priority ordering
  - missing local source file handling
  - duplicate source object reuse
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/uploader/base.py app/uploader/s3_multipart.py app/services/upload_ingest.py app/workers/upload_ingest.py tests/services/test_upload_ingest.py`
  - `python -m compileall app tests`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run python -m app.workers.upload_ingest --help`
  - `env UV_CACHE_DIR=/tmp/uv-cache POSTGRES_SERVER=localhost POSTGRES_PORT=55432 POSTGRES_DB=app POSTGRES_USER=postgres POSTGRES_PASSWORD=changethis uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/api/routes/test_ingest.py -q`
  - `git diff --check`
- Latest result: `30 passed` on the expanded ingestion suite. The pytest command still required unsandboxed access to the local Postgres instance on `localhost:55432`; warnings remain for placeholder secrets and the short JWT signing fallback when `MEDIA_SIGNING_SECRET` is unset.

- Added media-processing orchestration in `backend/app/services/media_processing.py`; it claims `source_uploaded` jobs, downloads uploaded source objects back from storage, probes them with `ffprobe`, creates normalized MP4 + thumbnail derivatives, reuses existing identical derivatives by `sha256 + asset_type`, and advances jobs to `completed`.
- Added an FFmpeg-backed processing adapter in `backend/app/processor/ffmpeg.py` with `ffprobe` parsing, normalized MP4 generation, and thumbnail extraction.
- Added a DB-backed processing worker entrypoint in `backend/app/workers/media_processing.py`, plus a `processing-worker` service in both compose files.
- Extended the storage client contract in `backend/app/uploader/base.py` / `backend/app/uploader/s3_multipart.py` with object download + delete support so processing can work from uploaded source objects and clean up derivative uploads on failure.
- Updated `backend/Dockerfile` to install `ffmpeg` inside the backend image so the compose processing worker has both `ffmpeg` and `ffprobe`.
- Added media-processing tests covering:
  - successful source probing plus normalized MP4/thumbnail creation
  - split video/audio source handling
  - rollback + remote cleanup on derivative upload failure
  - processing worker priority ordering
  - derivative reuse
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check backend/app/processor backend/app/services/media_processing.py backend/app/workers/media_processing.py backend/app/uploader/base.py backend/app/uploader/s3_multipart.py backend/tests/services/test_media_processing.py`
  - `python -m compileall backend/app backend/tests`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run python -m app.workers.media_processing --help`
  - `env UV_CACHE_DIR=/tmp/uv-cache POSTGRES_SERVER=localhost POSTGRES_PORT=55432 POSTGRES_DB=app POSTGRES_USER=postgres POSTGRES_PASSWORD=changethis uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/services/test_media_processing.py tests/api/routes/test_ingest.py -q`
  - `git diff --check`
- Latest result: `35 passed` on the expanded ingestion suite. The pytest command still required unsandboxed access to the local Postgres instance on `localhost:55432`; warnings remain for placeholder secrets plus the short JWT signing fallback when `MEDIA_SIGNING_SECRET` is unset.

## 2026-04-20

- Added `botocore` to `backend/pyproject.toml` and refreshed `uv.lock` so local `uv sync` environments can run real upload/processing storage operations without relying on ad hoc package installs.
- Fixed a real object-storage bug in `backend/app/uploader/s3_multipart.py`: botocore clients do not expose `download_file()`, so the implementation now streams object bodies via `get_object()` into the destination path and verifies downloaded size.
- Added regression tests in `backend/tests/services/test_s3_multipart.py` for streamed downloads and size-mismatch verification.
- Added a gated live smoke test in `backend/tests/services/test_live_object_storage_smoke.py` that exercises:
  - real Postgres job state transitions
  - real source upload to S3-compatible object storage
  - real `ffprobe`/`ffmpeg` media processing
  - derivative upload/download verification
  - remote cleanup after the run
- Validated the new smoke test against a user-provided SeaweedFS S3 endpoint on `2026-04-20`; the end-to-end upload + processing path passed.
- Added optional root `.env.local` support across backend settings and compose env files, so local S3 credentials can override tracked defaults without editing `.env`.
- Wrote the current SeaweedFS S3 credentials into the gitignored root `.env.local`, updated local compose overrides to honor `.env.local` before falling back to bundled MinIO defaults, and taught the live smoke test to reuse app settings for `RUN_LIVE_S3_SMOKE` / `S3_*` lookups.
- Updated `backend/Dockerfile` to stop manually re-installing `botocore`, since it is now part of the backend dependency set. `yt-dlp` remains installed as an extra image step.
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/core/config.py tests/services/test_live_object_storage_smoke.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run python -c "from app.core.config import settings; print(settings.S3_ENDPOINT_URL, settings.S3_BUCKET, settings.RUN_LIVE_S3_SMOKE)"`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run python -c "from tests.services.test_live_object_storage_smoke import _live_smoke_enabled, _bucket_name; print(_live_smoke_enabled(), _bucket_name())"`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/uploader/s3_multipart.py tests/services/test_s3_multipart.py tests/services/test_live_object_storage_smoke.py app/processor/ffmpeg.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_s3_multipart.py -q`
  - `RUN_LIVE_S3_SMOKE=1 S3_ENDPOINT_URL=<endpoint> S3_ACCESS_KEY=<key> S3_SECRET_KEY=<secret> S3_BUCKET=<bucket> env UV_CACHE_DIR=/tmp/uv-cache POSTGRES_SERVER=localhost POSTGRES_PORT=55432 POSTGRES_DB=app POSTGRES_USER=postgres POSTGRES_PASSWORD=changethis uv run pytest tests/services/test_live_object_storage_smoke.py -q`
  - `env UV_CACHE_DIR=/tmp/uv-cache POSTGRES_SERVER=localhost POSTGRES_PORT=55432 POSTGRES_DB=app POSTGRES_USER=postgres POSTGRES_PASSWORD=changethis uv run pytest tests/services/test_upload_ingest.py tests/services/test_media_processing.py tests/services/test_s3_multipart.py -q`
  - `python -m compileall app tests`
  - `docker compose config -q`
  - `git diff --check`
- Latest result:
  - `.env.local` now resolves to the live SeaweedFS endpoint for backend settings and local compose services
  - The live smoke helper now resolves `RUN_LIVE_S3_SMOKE` / `S3_BUCKET` from `.env.local` without shell exports
  - `1 passed` for the live SeaweedFS smoke test
  - `12 passed` for the upload/media-processing/storage-client regression slice
  - Placeholder secret warnings remain, and JWT signing still warns until `MEDIA_SIGNING_SECRET` is set to a >=32-byte key.

- Normalized host-based test DB bootstrap for ingestion validation:
  - `backend/app/core/config.py` now resolves env files by absolute repository path and appends `.env.test` / `.env.test.local` when `APP_ENV=test`
  - `backend/tests/conftest.py` now sets `APP_ENV=test` before importing app settings, so direct `uv run pytest` invocations no longer need temporary `POSTGRES_*` exports
  - Added tracked root `.env.test` for the dedicated local Postgres defaults and gitignored `.env.test.local` for contributor-specific overrides
  - Added a profiled `test-db` service to `compose.override.yml` bound to `localhost:55432`
  - Documented the host-based test DB flow in `backend/README.md`
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run python -c "import os; os.environ.setdefault('APP_ENV', 'test'); from app.core.config import settings; print(settings.POSTGRES_SERVER, settings.POSTGRES_PORT, settings.S3_ENDPOINT_URL, settings.RUN_LIVE_S3_SMOKE)"`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/core/config.py tests/conftest.py`
  - `python -m compileall backend/app backend/tests`
  - `docker compose config -q`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/services/test_media_processing.py tests/api/routes/test_ingest.py -q`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_live_object_storage_smoke.py -q`
  - `git diff --check`
- Latest result:
  - Host-based ingestion validation now defaults to the tracked `.env.test` Postgres settings on `localhost:55432` without manual `POSTGRES_*` overrides
  - `35 passed` for the targeted ingestion suite
  - `1 passed` for the live SeaweedFS smoke test
  - The new `test-db` compose service could not be started in this workspace during validation because `localhost:55432` was already occupied by an existing Docker listener, but the dedicated DB listener on that port was available and the serial pytest reruns succeeded against it
  - Running the host-based ingestion suite and the live smoke in parallel against the shared `localhost:55432` database produced false negatives (`login` 400s and a `StaleDataError`); keep those host-based DB-mutating suites serial until test isolation changes

- Added stale worker reclaim across metadata/download/upload/media processing:
  - Added `backend/app/workers/stale_reclaim.py` for shared stale detection, `progress.last_transition_at` parsing, and reclaim bookkeeping
  - Metadata/download/upload/processing workers now consider their in-progress statuses (`metadata_fetching`, `downloading`, `uploading_source`, `processing_media`) reclaimable after stage-specific timeout settings in `backend/app/core/config.py`
  - Metadata start handling now refreshes `progress.last_transition_at` on each restart attempt, so reclaimed metadata jobs do not immediately look stale again
  - Added regression coverage that verifies each worker reclaims stale in-progress jobs and skips fresh in-progress jobs in favor of queued work
  - Tightened worker-test priority bands so session-scoped test data from earlier pipeline stages does not interfere with later-stage worker claim assertions
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/core/config.py app/services/metadata_ingest.py app/workers/stale_reclaim.py app/workers/metadata_ingest.py app/workers/download_ingest.py app/workers/upload_ingest.py app/workers/media_processing.py tests/services/test_metadata_ingest.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/services/test_media_processing.py`
  - `python -m compileall backend/app backend/tests`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_metadata_ingest.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/services/test_media_processing.py -q`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/services/test_media_processing.py tests/api/routes/test_ingest.py -q`
  - `git diff --check`
- Latest result:
  - `30 passed` on the metadata/download/upload/media-processing service+worker slice
  - `43 passed` on the targeted ingestion suite after adding stale reclaim coverage
  - Reclaim now works without schema changes, but it is timeout-based only; there is still no worker heartbeat or lease extension during long-running jobs

- Added authenticated Bilibili access wiring for metadata/download flows:
  - `backend/app/core/config.py` now exposes optional `BILIBILI_COOKIE_HEADER`, `YT_DLP_COOKIES_FILE`, and `YT_DLP_COOKIES_FROM_BROWSER` settings
  - `backend/app/crawler/bilibili_metadata_http.py` now applies the optional cookie header to Bilibili metadata HTTP requests, even when a custom `httpx.Client` is injected for tests
  - `backend/app/downloader/yt_dlp_adapter.py` now forwards raw cookie headers to `yt-dlp --add-header`, supports Netscape cookie files via `--cookies`, and supports browser sync via `--cookies-from-browser` with cookie-file precedence
  - Local compose now mounts `backend/.secrets` into `download-worker`, and `backend/README.md` documents how to provide authenticated cookie headers or cookie files without baking secrets into the image
  - Added regression coverage in `backend/tests/services/test_bilibili_metadata_http.py` and new `backend/tests/services/test_yt_dlp_adapter.py` for metadata cookie injection plus `yt-dlp` auth-argument construction
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/core/config.py app/crawler/bilibili_metadata_http.py app/downloader/yt_dlp_adapter.py tests/services/test_bilibili_metadata_http.py tests/services/test_yt_dlp_adapter.py`
  - `python -m compileall backend/app backend/tests`
  - `docker compose config -q`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_yt_dlp_adapter.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/services/test_media_processing.py tests/api/routes/test_ingest.py -q`
  - `git diff --check`
- Latest result:
  - `46 passed` on the targeted ingestion suite after adding authenticated metadata/download coverage
  - Public videos still work without cookies, but authenticated metadata/download runs can now be supplied via env-backed cookie headers, cookie files, or host-side browser sync
  - `fetch_comments`, `fetch_danmaku`, and `fetch_subtitles` remain unimplemented, and the live SeaweedFS smoke test was not rerun in this turn

- Closed the half-implemented auxiliary fetch contract at ingest submission:
  - `backend/app/ingest_models.py` now defaults `fetch_subtitles` to `false`, matching the still-unimplemented state of comments/danmaku/subtitle crawling
  - `backend/app/services/ingest.py` now rejects `fetch_comments`, `fetch_danmaku`, and `fetch_subtitles` with HTTP 422 before any ingest job or idempotency record is created
  - `backend/tests/api/routes/test_ingest.py` now covers both the default-disabled option payload and explicit rejection of unsupported auxiliary fetch flags
  - `backend/README.md` plus `.cadence/tasks/bilibili-ingestion-mvp.md` / `.cadence/handoffs/bilibili-ingestion.md` now describe the explicit rejection contract and move the next recommended implementation target to blocked-job resume
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/ingest_models.py app/services/ingest.py tests/api/routes/test_ingest.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/routes/test_ingest.py -q`
  - `python -m compileall backend/app backend/tests`
  - `git diff --check`
- Latest result:
  - `8 passed` on the ingest route regression slice, including the new unsupported-option rejection coverage
  - Omitted ingest options now persist `fetch_comments=false`, `fetch_danmaku=false`, and `fetch_subtitles=false`
  - Explicit comment/danmaku/subtitle fetch requests now fail fast with HTTP 422 instead of creating inert jobs
  - Placeholder secret warnings and short JWT signing warnings remain in the local test environment

- Implemented real auxiliary ingestion for comments/danmaku/subtitles:
  - Added `backend/app/crawler/bilibili_auxiliary.py` and `backend/app/crawler/bilibili_auxiliary_http.py` for Bilibili comment pagination, danmaku XML parsing, and subtitle track fetching with the existing cookie-header auth path
  - Added `backend/app/services/auxiliary_ingest.py` and wired `backend/app/services/metadata_ingest.py` / `backend/app/workers/metadata_ingest.py` so requested auxiliary fetches now run during metadata ingestion and persist into `video_comments`, `video_danmaku`, and `video_subtitles`
  - Restored `fetch_subtitles=True` in `backend/app/ingest_models.py`, removed the temporary HTTP 422 ingest-option rejection from `backend/app/services/ingest.py`, and updated route regressions to assert option persistence instead of rejection
  - Tightened `backend/app/workers/download_ingest.py` plus `backend/app/services/download_ingest.py` so only `download_video=true` jobs enter the download stage
  - Added regression coverage in `backend/tests/services/test_bilibili_auxiliary_http.py`, expanded `backend/tests/services/test_metadata_ingest.py` for auxiliary persistence/refresh/failure handling, and added a download-worker guard regression in `backend/tests/services/test_download_ingest.py`
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/crawler/bilibili_auxiliary.py app/crawler/bilibili_auxiliary_http.py app/services/auxiliary_ingest.py app/services/metadata_ingest.py app/services/ingest.py app/services/download_ingest.py app/workers/metadata_ingest.py app/workers/download_ingest.py tests/services/test_bilibili_auxiliary_http.py tests/services/test_metadata_ingest.py tests/services/test_download_ingest.py tests/api/routes/test_ingest.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_bilibili_auxiliary_http.py tests/services/test_metadata_ingest.py tests/services/test_download_ingest.py tests/api/routes/test_ingest.py -q`
  - `python -m compileall backend/app backend/tests`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_bvid_parser.py tests/services/test_storage_keys.py tests/services/test_rights_policy.py tests/services/test_metadata_ingest.py tests/services/test_bilibili_metadata_http.py tests/services/test_bilibili_auxiliary_http.py tests/services/test_yt_dlp_adapter.py tests/services/test_download_ingest.py tests/services/test_upload_ingest.py tests/services/test_media_processing.py tests/api/routes/test_ingest.py -q`
- Latest result:
  - `32 passed` on the focused auxiliary/metadata/download/route slice
  - `58 passed` on the expanded targeted ingestion suite after adding auxiliary crawler coverage
  - `fetch_comments`, `fetch_danmaku`, and `fetch_subtitles` now execute instead of being rejected; subtitle fetch is default-on again
  - A broader directory-level `ruff check app/crawler app/services app/workers ...` still reports pre-existing import-order issues in `backend/app/services/bilibili.py`, `backend/app/services/rights.py`, and `backend/app/services/storage_keys.py`, outside the files changed in this turn
  - Placeholder secret warnings and short JWT signing warnings remain in the local test environment; the live SeaweedFS smoke test was not rerun in this turn

- Made comment-fetch completeness explicit in auxiliary ingest progress:
  - Added `BilibiliCommentFetchResult` / `BilibiliCommentFetchSummary` in `backend/app/crawler/bilibili_auxiliary.py` so comment crawlers return both persisted comment rows and a normalized completeness summary
  - `backend/app/crawler/bilibili_auxiliary_http.py` now records `expected_count` from `/x/v2/reply/count`, `fetched_count`, whether legacy fallback was used, and whether the crawl is known-partial
  - `backend/app/services/auxiliary_ingest.py` now persists those comment completeness fields into `ingest_jobs.progress["auxiliary"]["comments"]` alongside the existing image upload counters
  - Updated `backend/tests/services/test_bilibili_auxiliary_http.py` and `backend/tests/services/test_metadata_ingest.py` to cover both WBI success and legacy fallback/partial progress persistence
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/crawler/bilibili_auxiliary.py app/crawler/bilibili_auxiliary_http.py app/services/auxiliary_ingest.py tests/services/test_bilibili_auxiliary_http.py tests/services/test_metadata_ingest.py`
  - `python -m compileall backend/app/crawler/bilibili_auxiliary.py backend/app/crawler/bilibili_auxiliary_http.py backend/app/services/auxiliary_ingest.py backend/tests/services/test_bilibili_auxiliary_http.py backend/tests/services/test_metadata_ingest.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_bilibili_auxiliary_http.py tests/services/test_metadata_ingest.py -q`
  - `git diff --check`
- Latest result:
  - `17 passed` on the comment auxiliary + metadata ingest slice after adding completeness summaries
  - `ingest_jobs.progress["auxiliary"]["comments"]` now carries stable `expected_count`, `fetched_count`, `fallback_used`, and `partial` fields in addition to `count` and image counters
  - Placeholder secret warnings (`SECRET_KEY`, `POSTGRES_PASSWORD`, `FIRST_SUPERUSER_PASSWORD`) remain unchanged in the local test environment

- Closed the danmaku history completeness gap on the write path:
  - Added `backend/app/crawler/bilibili_danmaku_proto.py` plus history-aware logic in `backend/app/crawler/bilibili_auxiliary_http.py` so danmaku fetches can read monthly history indexes, download daily history segments, merge them with the current XML snapshot, and return a structured completeness summary
  - Extended `backend/app/crawler/bilibili_auxiliary.py` / `backend/app/services/auxiliary_ingest.py` so danmaku ingest now records `source`, `history_used`, `snapshot_used`, `expected_days_count`, `fetched_days_count`, `partial`, per-page summaries, and duplicate counts in `ingest_jobs.progress["auxiliary"]["danmaku"]`
  - Rekeyed `video_danmaku` away from global provider ids: `backend/app/ingest_models.py` now uses an internal UUID primary key plus nullable `danmaku_id`, and new Alembic revisions `7f0f47d5e91a_add_danmaku_history_columns.py` / `8c3042c1f5c0_rekey_video_danmaku_with_internal_ids.py` carry the schema forward
  - Added regressions in `backend/tests/services/test_bilibili_auxiliary_http.py` for history+snapshot merge and snapshot fallback, plus `backend/tests/services/test_metadata_ingest.py` coverage for persisted danmaku completeness and cross-source deduplication
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/crawler/bilibili_auxiliary.py app/crawler/bilibili_danmaku_proto.py app/crawler/bilibili_auxiliary_http.py app/services/auxiliary_ingest.py app/ingest_models.py app/alembic/versions/7f0f47d5e91a_add_danmaku_history_columns.py app/alembic/versions/8c3042c1f5c0_rekey_video_danmaku_with_internal_ids.py tests/services/test_bilibili_auxiliary_http.py tests/services/test_metadata_ingest.py`
  - `python -m compileall backend/app/crawler/bilibili_auxiliary.py backend/app/crawler/bilibili_danmaku_proto.py backend/app/crawler/bilibili_auxiliary_http.py backend/app/services/auxiliary_ingest.py backend/app/ingest_models.py backend/app/alembic/versions/7f0f47d5e91a_add_danmaku_history_columns.py backend/app/alembic/versions/8c3042c1f5c0_rekey_video_danmaku_with_internal_ids.py backend/tests/services/test_bilibili_auxiliary_http.py backend/tests/services/test_metadata_ingest.py`
  - `env APP_ENV=test UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_bilibili_auxiliary_http.py tests/services/test_metadata_ingest.py -q`
  - `git diff --check`
- Latest result:
  - `19 passed` on the danmaku auxiliary + metadata ingest slice after adding history coverage, completeness summaries, and danmaku row rekeying
  - `video_danmaku` rows now preserve `source` and `history_date`, while provider ids live in `danmaku_id` instead of the table primary key
  - History-index access and DB-mutating validation still required unsandboxed local Postgres access; placeholder secret warnings remain unchanged in the local test environment

- Expanded the auxiliary read surface for comments and danmaku:
  - Added `VideoDanmakuEntryPublic` / `VideoDanmakuEntriesPublic` in `backend/app/ingest_models.py`
  - Extended `backend/app/api/routes/videos.py` so `/videos/{bvid}/comments` now supports `root` and `parent` filters without breaking comment-image hydration
  - Added `/videos/{bvid}/danmaku` in `backend/app/api/routes/videos.py` with `cid`, `source`, `history_date`, `limit`, and `offset` query support
  - Added route regressions in `backend/tests/api/routes/test_videos.py` covering filtered comment threads and danmaku row queries
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/api/routes/videos.py app/ingest_models.py tests/api/routes/test_videos.py`
  - `python -m compileall backend/app/api/routes/videos.py backend/app/ingest_models.py backend/tests/api/routes/test_videos.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/routes/test_videos.py -q`
  - `git diff --check`
- Latest result:
  - `2 passed` on the dedicated video-route regression slice after adding auxiliary query filters
  - Auxiliary rows are now queryable directly, but completeness metadata still lives on ingest job progress rather than the read routes themselves
  - Placeholder secret warnings plus short JWT signing warnings remain unchanged in the local test environment

- Implemented playback-ready derivatives plus the first operator web surface:
  - Extended `backend/app/processor/ffmpeg.py` / `backend/app/services/media_processing.py` so `create_hls=true` now emits a `proxy_mp4`, HLS media playlists, HLS segments, and an HLS master manifest instead of failing the job
  - Added signed playback URLs plus actual proxied playback in `backend/app/api/routes/media.py`; HLS manifests are rewritten on the fly so child playlists/segments resolve through signed `/media/assets/{asset_id}/playback` URLs
  - Added browse-oriented backend read APIs in `backend/app/api/routes/ingest.py` and `backend/app/api/routes/videos.py`: recent ingest-job listing, video catalog/detail listing, and subtitle reads now exist alongside the existing assets/comments/danmaku routes
  - Added regressions in `backend/tests/services/test_media_processing.py`, new `backend/tests/api/routes/test_media.py`, and expanded `backend/tests/api/routes/test_videos.py` / `backend/tests/api/routes/test_ingest.py`
  - Replaced the placeholder frontend dashboard with an ingestion console in `frontend/src/routes/_layout/index.tsx`, added a dedicated `/videos` browser in `frontend/src/routes/_layout/videos.tsx`, and wired both to the backend with `frontend/src/lib/ingestionApi.ts`, `frontend/src/components/Ingestion/StatusBadge.tsx`, `frontend/src/components/Ingestion/JsonPreview.tsx`, and `frontend/src/components/Ingestion/VideoPlayback.tsx`
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check backend/app/processor/base.py backend/app/processor/ffmpeg.py backend/app/services/media_processing.py backend/app/services/signed_urls.py backend/app/api/deps.py backend/app/api/routes/media.py backend/app/api/routes/videos.py backend/app/api/routes/ingest.py backend/app/ingest_models.py backend/tests/services/test_media_processing.py backend/tests/api/routes/test_media.py backend/tests/api/routes/test_videos.py backend/tests/api/routes/test_ingest.py`
  - `python -m compileall backend/app/processor/base.py backend/app/processor/ffmpeg.py backend/app/services/media_processing.py backend/app/services/signed_urls.py backend/app/api/deps.py backend/app/api/routes/media.py backend/app/api/routes/videos.py backend/app/api/routes/ingest.py backend/app/ingest_models.py backend/tests/services/test_media_processing.py backend/tests/api/routes/test_media.py backend/tests/api/routes/test_videos.py backend/tests/api/routes/test_ingest.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_media_processing.py tests/api/routes/test_media.py tests/api/routes/test_videos.py tests/api/routes/test_ingest.py -q`
  - `bun run build` is the documented frontend workspace build; in the agent environment Bun was unavailable, so the equivalent Vite-backed build path was validated instead
- Latest result:
  - `24 passed` on the targeted media-processing + media/videos/ingest route slice after adding HLS/proxy playback coverage and browse APIs
  - The frontend build path now passes for the operator console; use `bun run build`, and expect the first Vite-backed build after adding routes to regenerate `frontend/src/routeTree.gen.ts` through the TanStack router plugin
  - Current frontend build still warns that the main vendor chunk exceeds 500 kB after minification; this is a build-time optimization concern, not a correctness blocker
  - Completeness is still discoverable via ingest job detail rather than embedded directly into comments/danmaku/subtitle read responses

- Exposed auxiliary completeness directly on read routes and reshaped `/videos` into a data workspace:
  - Added typed route-level completeness models in `backend/app/ingest_models.py` and derived them in `backend/app/api/routes/videos.py` from the latest relevant ingest-job auxiliary progress for comments, danmaku, and subtitles
  - Expanded `backend/tests/api/routes/test_videos.py` so the comments/danmaku/subtitles read routes now regress both filter behavior and completeness payloads
  - Updated `frontend/src/lib/ingestionApi.ts` to understand the new completeness objects and rewrote `frontend/src/routes/_layout/videos.tsx` around data inspection: overview, ingest health, completeness cards, route filters, result tables, and raw JSON panes now take priority over playback
  - Kept playback available only as a smaller validation preview in the assets tab, while `frontend/src/components/Ingestion/StatusBadge.tsx` now also handles `complete` / `partial` data-health labels cleanly
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/api/routes/videos.py app/ingest_models.py tests/api/routes/test_videos.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/routes/test_videos.py -q`
  - `npm run build`
  - `git diff --check -- backend/app/api/routes/videos.py backend/app/ingest_models.py backend/tests/api/routes/test_videos.py frontend/src/lib/ingestionApi.ts frontend/src/routes/_layout/videos.tsx frontend/src/components/Ingestion/StatusBadge.tsx`
- Latest result:
  - `4 passed` on the dedicated video-route regression slice after surfacing completeness directly on comments/danmaku/subtitles responses
  - `/videos` now behaves like a data control surface instead of a playback-first page: operators can inspect completeness, filter rows, view latest ingest health, and inspect raw payloads without jumping through job detail first
  - The frontend production build still passes in the current environment through the npm/Vite-equivalent path because Bun is unavailable in the agent runtime; Bun remains the documented repo-standard workflow
  - Remaining known gaps after this round are blocked-job auto-resume, lack of auxiliary object-storage sidecars, and the absence of an `hls.js` fallback for non-native HLS browsers

- Closed the blocked-by-policy operability gap after rights approval:
  - `backend/app/services/rights.py` now syncs matching video `rights_status` rows more broadly and resumes eligible blocked ingest jobs immediately when a content right is reviewed as `approved`
  - Resume semantics are explicit: jobs blocked before metadata return to `pending`, while jobs blocked after metadata/download checks return to `metadata_ready` so the download worker can continue without refetching metadata
  - Added service regressions in `backend/tests/services/test_rights_policy.py` for both pre-metadata and metadata-ready resume paths, including owner-level rights approval
  - Added API coverage in `backend/tests/api/routes/test_ingest.py` so the route-level rights review flow now proves a previously blocked ingest job is resumed automatically
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/services/rights.py tests/services/test_rights_policy.py tests/api/routes/test_ingest.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/services/test_rights_policy.py tests/services/test_download_ingest.py tests/services/test_metadata_ingest.py tests/api/routes/test_ingest.py -q`
  - `git diff --check -- backend/app/services/rights.py backend/tests/services/test_rights_policy.py backend/tests/api/routes/test_ingest.py`
- Latest result:
  - `36 passed` on the rights/download/metadata/ingest regression slice after adding auto-resume behavior
  - Rights approval no longer leaves matching `blocked_by_policy` jobs stranded; the approval transaction itself now returns them to the correct runnable queue state
  - Remaining known gaps after this round are finer auxiliary browse surfaces, lack of auxiliary object-storage sidecars, subtitle completeness still being availability-style only, and the absence of an `hls.js` fallback for non-native HLS browsers

- Added a dedicated comment-image browse surface across backend and frontend:
  - Added `VideoCommentContextPublic`, `VideoCommentImageEntryPublic`, and `VideoCommentImagesPublic` in `backend/app/ingest_models.py`
  - Extended `backend/app/api/routes/videos.py` with `GET /videos/{bvid}/comment-images`, supporting `rpid`, `root`, `parent`, `storage_status`, `limit`, and `offset` filters while reusing the latest comments completeness summary
  - Added route regressions in `backend/tests/api/routes/test_videos.py` covering comment-image inventory ordering, comment-context hydration, `ready/failed/skipped` filtering, and thread-level `rpid/root/parent` filtering
  - Updated `frontend/src/lib/ingestionApi.ts` with typed comment-image bindings and expanded `frontend/src/routes/_layout/videos.tsx` with a dedicated `Comment Images` tab for image inventory, storage-outcome filtering, comment-context cards, and operator-oriented error/asset inspection
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/api/routes/videos.py app/ingest_models.py tests/api/routes/test_videos.py`
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/routes/test_videos.py -q`
  - `bun run build` was attempted first per repo guidance, but Bun is unavailable in the agent runtime (`zsh:1: command not found: bun`)
  - `npm --workspace frontend run build`
  - `git diff --check`
- Latest result:
  - `5 passed` on the dedicated video-route regression slice after adding comment-image inventory coverage
  - `/videos/{bvid}/comment-images` now gives operators a direct image inventory with comment context, storage status, failure text, and linked asset metadata instead of requiring manual correlation from `/comments`
  - `/videos` now has an independent `Comment Images` browse surface rather than only inline image thumbnails inside comment cards
  - The npm/Vite-equivalent frontend build passed and refreshed the TanStack route output; the build still warns about the existing >500 kB vendor chunk, but there are no new correctness issues

- Squashed the pre-production Alembic history into a single baseline migration
  at `backend/app/alembic/versions/20260420_01_initialize_current_schema.py`,
  superseding the feature-by-feature revision files referenced earlier in this
  log.
- Validation completed:
  - `env UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/alembic/env.py app/alembic/versions/20260420_01_initialize_current_schema.py`
  - `env APP_ENV=test POSTGRES_DB=bili_ingest_migration_baseline UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head`
  - `env APP_ENV=test POSTGRES_DB=bili_ingest_migration_baseline UV_CACHE_DIR=/tmp/uv-cache uv run alembic current`
- Latest result:
  - Fresh empty local Postgres databases now bootstrap from the single
    `20260420_01` baseline
  - Existing local databases stamped with the removed revision chain should be
    dropped and recreated once rather than upgraded in place

- Initialized the first external PostgreSQL instance from the squashed
  baseline, confirming the MVP schema is ready to leave the local-only phase:
  - inspected the target database and confirmed it only contained extension
    tables, with no `alembic_version` row and no application schema yet
  - ran `alembic upgrade head` against that database and verified
    `20260420_01 (head)`
  - confirmed the migrated schema now exposes the full application table set on
    top of the pre-existing extension tables
- Validation completed:
  - `psql ... -c "select current_database(), current_user;"`
  - `env POSTGRES_SERVER=... POSTGRES_PORT=... POSTGRES_DB=... POSTGRES_USER=... POSTGRES_PASSWORD=... UV_CACHE_DIR=/tmp/uv-cache uv run alembic upgrade head`
  - `psql ... -c "select version_num from alembic_version;"`
  - `env POSTGRES_SERVER=... POSTGRES_PORT=... POSTGRES_DB=... POSTGRES_USER=... POSTGRES_PASSWORD=... UV_CACHE_DIR=/tmp/uv-cache uv run alembic current`
- Latest result:
  - The first non-local PostgreSQL environment is now stamped at
    `20260420_01 (head)`
  - From this point forward, schema changes should use normal new Alembic
    revisions instead of another pre-production history rewrite

- Organized the production environment-variable surface for deployment handoff:
  - regrouped the tracked root `.env` file around public routing, auth/bootstrap,
    PostgreSQL, object storage, Bilibili auth, and operations variables
  - replaced leftover template project metadata in `.env` with
    `Bilibili Media Ingestion Service`
  - added a backend README checklist that distinguishes required non-local
    variables, full-media-pipeline variables, and optional ops variables
  - documented the current Compose caveat that `compose.yml` still hardcodes
    `POSTGRES_SERVER=db` for containerized services
- Validation completed:
  - `git diff --check -- .env backend/README.md`
- Latest result:
  - The repository now has a project-specific production env checklist instead
    of relying on template `.env` comments plus scattered code defaults
  - External PostgreSQL is initialized, but the default Compose production path
    still needs DB-host wiring changes before it can use that external database
    directly

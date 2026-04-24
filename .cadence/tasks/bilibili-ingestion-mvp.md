# Bilibili Ingestion MVP Workboard

Status: active
Last updated: 2026-04-20
Primary area: `backend/`, `frontend/`

## Scope

Build a compliance-first ingestion backbone for BVID/link submissions:

- rights-aware ingest job creation
- metadata persistence to PostgreSQL
- media asset indexing for object storage
- signed access path for private media delivery
- worker-friendly service boundaries for future downloader/media/index stages

## Active milestone

Phase 2.4: data-control semantics and ingest operability.

## Work items

| ID | Status | Task | Notes |
| --- | --- | --- | --- |
| ING-001 | done | Domain schema and Alembic migration for ingestion entities | `content_rights`, `ingest_jobs`, `videos`, `video_pages`, `media_assets`, audit, comments, danmaku, subtitles, stat snapshots |
| ING-002 | done | Rights service and ingest submission API | Job idempotency, rights review, audit trail, blocked-by-policy handling |
| ING-003 | done | Video asset list and app-signed access descriptor | Current signed URL resolves to app download descriptor, not direct S3 presign |
| ING-004 | done | Metadata ingest execution service | Upserts uploader/video/pages/stat snapshots and advances job status |
| ING-005 | done | File-based collaboration surface | `.cadence/README.md`, workboard, progress log, handoff notes are in repo and should be updated every session |
| ING-006 | done | Worker wiring for metadata execution | Added DB-backed metadata worker entrypoint at `backend/app/workers/metadata_ingest.py` and compose services for local/prod |
| ING-007 | done | Real Bilibili metadata adapter | Added HTTP-based Bilibili provider at `backend/app/crawler/bilibili_metadata_http.py` with retry, response normalization, and failure classification |
| ING-008 | done | Downloader execution path | Added DB-backed download worker, `yt-dlp` adapter, tmp workspace staging, checksuming, and source asset row creation |
| ING-009 | done | S3 multipart upload implementation | Added upload worker, botocore-backed multipart upload, `HeadObject` verification, abort/delete cleanup, and duplicate-object reuse path |
| ING-010 | done | Media processing stage | ffprobe-backed source introspection plus normalized MP4 + thumbnail derivatives landed; live SeaweedFS S3 smoke test passed |
| ING-011 | done | Normalize local test DB bootstrap | Added tracked root `.env.test`, optional `.env.test.local`, host-pytest bootstrap in `backend/tests/conftest.py`, and a profiled `test-db` compose service on `localhost:55432` |
| ING-012 | done | Reclaim stale worker jobs | Metadata/download/upload/processing workers now reclaim stale in-progress jobs using `progress.last_transition_at`, stage-specific timeout settings, and targeted regression coverage |
| ING-013 | done | Authenticated Bilibili access wiring | Added optional `BILIBILI_COOKIE_HEADER`, `YT_DLP_COOKIES_FILE`, and `YT_DLP_COOKIES_FROM_BROWSER` support for metadata/download flows, plus local compose cookie-file mounting and regression coverage |
| ING-014 | done | Reject unsupported auxiliary fetch options at submit time | `fetch_comments`, `fetch_danmaku`, and `fetch_subtitles` now default to `false` and return HTTP 422 when explicitly requested until dedicated crawlers exist |
| ING-015 | done | Implement auxiliary comments/danmaku/subtitle ingestion | Metadata worker now fetches requested comments, danmaku, and subtitles via Bilibili HTTP APIs, stores them in PostgreSQL, restores `fetch_subtitles=true` by default, and keeps download worker scoped to `download_video=true` jobs |
| ING-016 | done | Persist comment fetch completeness in job progress | Comment crawls now write `expected_count`, `fetched_count`, `fallback_used`, and `partial` into `ingest_jobs.progress["auxiliary"]["comments"]` alongside image stats |
| ING-017 | done | Add danmaku history coverage and completeness tracking | Danmaku now merges history-day segments with the current snapshot, persists source/date semantics, surfaces completeness in job progress, and uses internal row ids instead of global provider ids |
| ING-018 | done | Expand auxiliary read APIs | `/videos/{bvid}/comments` now supports `root` / `parent` filters, and `/videos/{bvid}/danmaku` now supports `cid` / `source` / `history_date` filters with pagination |
| ING-019 | done | Expose completeness directly on auxiliary read routes | `/videos/{bvid}/comments|danmaku|subtitles` now return a route-level `completeness` object derived from the latest relevant ingest job progress |
| ING-020 | done | Implement HLS/proxy playback path | Media processing now emits `proxy_mp4` plus HLS master/playlist/segment assets when `create_hls=true`, and `/media/assets/{asset_id}/playback` now proxies bytes and rewrites HLS manifests into signed child URLs |
| ING-021 | done | Add browse-oriented video/job read APIs | `/ingest/jobs`, `/videos/`, `/videos/{bvid}`, and `/videos/{bvid}/subtitles` now support the operator web console and video detail drill-downs |
| ING-022 | done | Build an operator web console | Frontend dashboard now submits ingest jobs, reviews recent jobs/videos, shows pending rights for superusers, and the `/videos` page browses assets/comments/danmaku/subtitles with direct playback |
| ING-023 | done | Auto-resume blocked jobs after rights approval | Rights review approval now resumes eligible `blocked_by_policy` jobs immediately: pre-metadata jobs go back to `pending`, metadata/download-blocked jobs go back to `metadata_ready` |
| ING-024 | in_progress | Add finer auxiliary browse surfaces | Start with comment-image inventory via a dedicated read API and `/videos` browse surface; richer danmaku filters and deeper job drill-down can follow |

## Current guidance

- Treat `backend/app/services/metadata_ingest.py` as the canonical orchestration layer for metadata jobs.
- Keep external fetchers and downloaders behind narrow interfaces so failures do not leak into API routes.
- Run metadata execution via `python -m app.workers.metadata_ingest` or the `metadata-worker` compose service; do not call provider code from API routes.
- Run source download execution via `python -m app.workers.download_ingest` or the `download-worker` compose service; source files are staged under `INGEST_TMP_DIR/jobs/<job_id>/source`.
- Authenticated Bilibili fetch/download runs can use `BILIBILI_COOKIE_HEADER` for both metadata + `yt-dlp`, `YT_DLP_COOKIES_FILE` for Netscape cookie files, or `YT_DLP_COOKIES_FROM_BROWSER` for host-based worker runs. Local compose mounts `backend/.secrets` into `download-worker` for cookie files.
- Run source upload execution via `python -m app.workers.upload_ingest` or the `upload-worker` compose service; it uploads `downloaded` assets, verifies object state, then advances jobs to `source_uploaded`.
- Run media processing execution via `python -m app.workers.media_processing` or the `processing-worker` compose service; it downloads uploaded source objects, probes them with `ffprobe`, emits normalized MP4 + thumbnail derivatives by default, adds `proxy_mp4` plus HLS outputs when `create_hls=true`, then advances jobs to `completed`.
- Worker reclaim now relies on `progress.last_transition_at` plus stage-specific `*_WORKER_STALE_AFTER_SECONDS` settings. There is no heartbeat yet, so keep those timeouts conservative for long-running jobs.
- Live object-storage smoke coverage now lives at `backend/tests/services/test_live_object_storage_smoke.py`; standard `S3_*` vars and `RUN_LIVE_S3_SMOKE=1` can live in the gitignored root `.env.local`, while host-based pytest now auto-loads the tracked root `.env.test` defaults for the dedicated Postgres listener on `localhost:55432`.
- Do not bypass rights checks when adding downloader or media workers.
- `fetch_comments`, `fetch_danmaku`, and `fetch_subtitles` now run inside the metadata worker and persist into `video_comments`, `video_danmaku`, and `video_subtitles`.
- Comment crawling should now be treated as a two-part result: persisted rows plus a completeness summary in `ingest_jobs.progress["auxiliary"]["comments"]`.
- Danmaku rows now carry source/date semantics and completeness in job progress, and the comments/danmaku/subtitle read routes now surface a route-level `completeness` object derived from the latest relevant ingest job.
- Rights review approval now synchronously resumes eligible blocked ingest jobs in `backend/app/services/rights.py`; there is no separate background reconciler for that path anymore.
- The operator browse surface now lives in `frontend/src/routes/_layout/index.tsx` and `frontend/src/routes/_layout/videos.tsx`, backed by manual frontend API bindings in `frontend/src/lib/ingestionApi.ts`; `/videos` should be treated as a data workspace first and playback surface second.
- Auxiliary fetches currently persist into PostgreSQL only; subtitle read APIs exist now, but there are still no object-storage sidecar assets for subtitle/comment/danmaku payloads.
- Start the dedicated local test DB with `docker compose --profile test up -d test-db` when running host-based ingestion verification, and keep host-based DB-mutating pytest suites serial because they clear the same shared tables.

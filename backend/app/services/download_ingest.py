from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

from app.core.config import settings
from app.downloader.base import (
    DownloaderAdapter,
    DownloaderError,
    DownloaderResultError,
    DownloadPlan,
)
from app.ingest_models import IngestJob, MediaAsset, Video
from app.services.audit import record_audit_event
from app.services.storage_keys import build_asset_storage_key

_AUDIO_EXTENSIONS = {"aac", "flac", "m4a", "mp3", "ogg", "opus", "wav"}
_VIDEO_EXTENSIONS = {"avi", "flv", "mkv", "mov", "mp4", "ts", "webm"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _merge_progress(job: IngestJob, *, payload: dict[str, object]) -> None:
    progress = dict(job.progress)
    progress.update(payload)
    job.progress = progress


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _download_input_url(job: IngestJob) -> str:
    stripped_input = job.input_text.strip()
    if stripped_input.startswith(("http://", "https://")):
        return stripped_input
    if not job.normalized_bvid:
        raise ValueError(f"Ingest job {job.id} has no normalized BVID")
    return f"https://www.bilibili.com/video/{job.normalized_bvid}"


def _default_format_selector(max_height: int | None) -> str:
    if max_height is None:
        return "bv*+ba/best"
    return f"bv*[height<={max_height}]+ba/b[height<={max_height}]/best"


def _workspace_dir(job_id: uuid.UUID) -> Path:
    return Path(settings.INGEST_TMP_DIR) / "jobs" / str(job_id) / "source"


def _select_asset_type(path: Path, *, total_files: int) -> str:
    extension = path.suffix.lower().lstrip(".")
    if total_files <= 1:
        return "source_archive"
    if extension in _AUDIO_EXTENSIONS:
        return "source_audio_stream"
    if extension in _VIDEO_EXTENSIONS:
        return "source_video_stream"
    return "source_archive"


def _content_type_for(path: Path) -> str | None:
    content_type, _ = mimetypes.guess_type(path.name)
    return content_type


def _record_source_assets(
    session: Session,
    *,
    job: IngestJob,
    video: Video,
    plan: DownloadPlan,
    cid: int | None,
    local_files: list[str],
    info_json_path: str | None,
    workspace_dir: Path,
) -> list[MediaAsset]:
    assets: list[MediaAsset] = []
    for raw_path in local_files:
        path = Path(raw_path)
        if not path.is_file():
            raise DownloaderResultError(f"Downloader output file is missing: {path}")

        asset = MediaAsset(
            bvid=video.bvid,
            cid=cid,
            job_id=job.id,
            asset_type=_select_asset_type(path, total_files=len(local_files)),
            status="downloaded",
            s3_bucket=settings.S3_BUCKET,
            s3_region=settings.S3_REGION,
            original_url_hash=_hash_text(plan.webpage_url),
            filename=path.name,
            content_type=_content_type_for(path),
            container_format=path.suffix.lower().lstrip(".") or None,
            duration_seconds=float(video.duration_seconds)
            if video.duration_seconds is not None
            else None,
            size_bytes=path.stat().st_size,
            sha256=_sha256_file(path),
            metadata_json={
                "local_path": str(path),
                "workspace_dir": str(workspace_dir),
                "info_json_path": info_json_path,
                "webpage_url": plan.webpage_url,
                "selected_format_id": plan.selected_format_id,
                "format_selector": plan.format_selector,
                "requested_max_height": plan.max_height,
            },
        )
        asset.s3_key = build_asset_storage_key(
            asset_type=asset.asset_type,
            bvid=asset.bvid,
            cid=asset.cid,
            asset_id=asset.id,
            filename=asset.filename,
        )
        session.add(asset)
        assets.append(asset)
    return assets


def _start_download(session: Session, *, job: IngestJob, started_at: datetime) -> None:
    job.status = "downloading"
    job.phase = "downloading source media"
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    _merge_progress(
        job,
        payload={
            "current_step": "source_downloading",
            "last_transition_at": started_at.isoformat(),
            "next_step": "downloader_worker",
        },
    )
    session.add(job)


def _complete_download(
    session: Session,
    *,
    job: IngestJob,
    asset_ids: list[uuid.UUID],
    completed_at: datetime,
    workspace_dir: Path,
) -> None:
    job.status = "source_downloaded"
    job.phase = "source media downloaded; ready for upload worker"
    job.finished_at = None
    _merge_progress(
        job,
        payload={
            "current_step": "source_downloaded",
            "last_transition_at": completed_at.isoformat(),
            "next_step": "upload_worker",
            "download": {
                "asset_ids": [str(asset_id) for asset_id in asset_ids],
                "asset_count": len(asset_ids),
                "workspace_dir": str(workspace_dir),
                "completed_at": completed_at.isoformat(),
            },
        },
    )
    session.add(job)


def _fail_download(
    session: Session,
    *,
    job: IngestJob,
    error_code: str,
    message: str,
    failed_at: datetime,
) -> None:
    job.status = "failed"
    job.phase = "source download failed"
    job.error_code = error_code
    job.error_message = message
    job.finished_at = failed_at
    job.retry_count += 1
    _merge_progress(
        job,
        payload={
            "current_step": "source_download_failed",
            "last_transition_at": failed_at.isoformat(),
        },
    )
    session.add(job)


def _cleanup_workspace(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def process_download_ingest_job(
    *,
    session: Session,
    job_id: uuid.UUID,
    adapter: DownloaderAdapter,
) -> IngestJob:
    job = session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Ingest job {job_id} not found")
    if not job.normalized_bvid:
        raise ValueError(f"Ingest job {job_id} has no normalized BVID")
    if job.status != "metadata_ready":
        raise ValueError(
            f"Ingest job {job_id} is not ready for source download: {job.status}"
        )
    if not bool(job.options.get("download_video")):
        raise ValueError(f"Ingest job {job_id} did not request video download")

    video = session.get(Video, job.normalized_bvid)
    if video is None:
        raise ValueError(
            f"Video {job.normalized_bvid} metadata must exist before download"
        )

    started_at = _now_utc()
    _start_download(session, job=job, started_at=started_at)
    record_audit_event(
        session=session,
        actor=job.requested_by,
        action="ingest_job.download_started",
        resource_type="ingest_job",
        resource_id=str(job.id),
        message="Started source media download",
        payload={"bvid": video.bvid},
    )
    session.commit()
    session.refresh(job)

    workspace_dir = _workspace_dir(job.id)
    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        plan = adapter.extract_info(_download_input_url(job))
        if plan.bvid != job.normalized_bvid:
            raise DownloaderResultError(
                "Downloader adapter returned a different BVID than the ingest job"
            )
        plan.title = plan.title or video.title
        plan.max_height = (
            int(job.options["max_height"])
            if job.options.get("max_height") is not None
            else plan.max_height
        )
        plan.format_selector = plan.format_selector or _default_format_selector(
            plan.max_height
        )

        result = adapter.download(plan, str(workspace_dir))
        if result.bvid != job.normalized_bvid:
            raise DownloaderResultError(
                "Downloader adapter returned a different BVID after download"
            )
        download_cid = result.cid or plan.cid

        assets = _record_source_assets(
            session,
            job=job,
            video=video,
            plan=plan,
            cid=download_cid,
            local_files=result.local_files,
            info_json_path=result.info_json_path,
            workspace_dir=workspace_dir,
        )
        completed_at = _now_utc()
        _complete_download(
            session,
            job=job,
            asset_ids=[asset.id for asset in assets],
            completed_at=completed_at,
            workspace_dir=workspace_dir,
        )
        record_audit_event(
            session=session,
            actor=job.requested_by,
            action="ingest_job.download_completed",
            resource_type="ingest_job",
            resource_id=str(job.id),
            message="Completed source media download",
            payload={
                "bvid": video.bvid,
                "asset_ids": [str(asset.id) for asset in assets],
                "asset_count": len(assets),
            },
        )
    except Exception as exc:
        session.rollback()
        _cleanup_workspace(workspace_dir)
        failed_job = session.get(IngestJob, job_id)
        if failed_job is None:
            raise
        failed_at = _now_utc()
        _fail_download(
            session,
            job=failed_job,
            error_code=(
                exc.error_code if isinstance(exc, DownloaderError) else "source_download_failed"
            ),
            message=str(exc),
            failed_at=failed_at,
        )
        record_audit_event(
            session=session,
            actor=failed_job.requested_by,
            action="ingest_job.download_failed",
            resource_type="ingest_job",
            resource_id=str(failed_job.id),
            message="Source media download failed",
            payload={"bvid": failed_job.normalized_bvid, "error": str(exc)},
        )
        job = failed_job

    session.commit()
    session.refresh(job)
    return job

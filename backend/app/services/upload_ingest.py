from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.ingest_models import IngestJob, MediaAsset
from app.services.audit import record_audit_event
from app.services.subtitle_transcription import enqueue_subtitle_transcription_tasks
from app.uploader.base import ObjectStorageClient, ObjectStorageError


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _merge_progress(job: IngestJob, *, payload: dict[str, object]) -> None:
    progress = dict(job.progress)
    progress.update(payload)
    job.progress = progress


def _assets_for_job(session: Session, *, job_id: uuid.UUID) -> list[MediaAsset]:
    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.job_id == job_id,
            MediaAsset.status == "downloaded",
        )
        .order_by(MediaAsset.created_at.asc())
    )
    return list(session.exec(statement).all())


def _find_existing_uploaded_asset(
    session: Session, *, asset: MediaAsset
) -> MediaAsset | None:
    if not asset.sha256:
        return None

    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.sha256 == asset.sha256,
            MediaAsset.asset_type == asset.asset_type,
            MediaAsset.status.in_(("uploaded", "ready")),
        )
        .order_by(MediaAsset.ready_at.desc(), MediaAsset.created_at.desc())
        .limit(1)
    )
    return session.exec(statement).first()


def _local_asset_path(asset: MediaAsset) -> Path:
    raw_path = asset.metadata_json.get("local_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError(f"Media asset {asset.id} is missing a local source path")
    return Path(raw_path)


def _workspace_dir(asset: MediaAsset) -> Path | None:
    raw_path = asset.metadata_json.get("workspace_dir")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    return Path(raw_path)


def _cleanup_workspace(path: Path | None) -> None:
    if path is None:
        return
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _mark_asset_as_reused(
    *,
    asset: MediaAsset,
    existing_asset: MediaAsset,
    uploaded_at: datetime,
) -> None:
    metadata_json = dict(asset.metadata_json)
    metadata_json["uploaded_at"] = uploaded_at.isoformat()
    metadata_json["uploaded_bucket"] = existing_asset.s3_bucket
    metadata_json["uploaded_key"] = existing_asset.s3_key
    metadata_json["verified_size_bytes"] = existing_asset.size_bytes
    metadata_json["verified_etag"] = existing_asset.etag
    metadata_json["local_file_removed"] = True
    metadata_json["reused_from_asset_id"] = str(existing_asset.id)
    if asset.sha256:
        metadata_json["source_sha256"] = asset.sha256

    asset.status = "uploaded"
    asset.s3_bucket = existing_asset.s3_bucket
    asset.s3_key = existing_asset.s3_key
    asset.s3_region = existing_asset.s3_region
    asset.storage_class = existing_asset.storage_class
    asset.size_bytes = existing_asset.size_bytes
    asset.etag = existing_asset.etag
    asset.content_type = existing_asset.content_type or asset.content_type
    asset.container_format = existing_asset.container_format or asset.container_format
    asset.video_codec = existing_asset.video_codec or asset.video_codec
    asset.audio_codec = existing_asset.audio_codec or asset.audio_codec
    asset.width = existing_asset.width or asset.width
    asset.height = existing_asset.height or asset.height
    asset.fps = existing_asset.fps or asset.fps
    asset.bitrate = existing_asset.bitrate or asset.bitrate
    asset.duration_seconds = existing_asset.duration_seconds or asset.duration_seconds
    asset.ready_at = uploaded_at
    asset.sha256 = None
    asset.metadata_json = metadata_json


def _start_upload(session: Session, *, job: IngestJob, started_at: datetime) -> None:
    job.status = "uploading_source"
    job.phase = "uploading source media to object storage"
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    _merge_progress(
        job,
        payload={
            "current_step": "source_uploading",
            "last_transition_at": started_at.isoformat(),
            "next_step": "upload_worker",
        },
    )
    session.add(job)


def _complete_upload(
    session: Session,
    *,
    job: IngestJob,
    asset_ids: list[uuid.UUID],
    completed_at: datetime,
) -> None:
    job.status = "source_uploaded"
    job.phase = "source media uploaded; ready for processing worker"
    job.finished_at = None
    _merge_progress(
        job,
        payload={
            "current_step": "source_uploaded",
            "last_transition_at": completed_at.isoformat(),
            "next_step": "media_processing_worker",
            "upload": {
                "asset_ids": [str(asset_id) for asset_id in asset_ids],
                "asset_count": len(asset_ids),
                "completed_at": completed_at.isoformat(),
            },
        },
    )
    session.add(job)


def _fail_upload(
    session: Session,
    *,
    job: IngestJob,
    error_code: str,
    message: str,
    failed_at: datetime,
) -> None:
    job.status = "failed"
    job.phase = "source upload failed"
    job.error_code = error_code
    job.error_message = message
    job.finished_at = failed_at
    job.retry_count += 1
    _merge_progress(
        job,
        payload={
            "current_step": "source_upload_failed",
            "last_transition_at": failed_at.isoformat(),
        },
    )
    session.add(job)


def process_upload_ingest_job(
    *,
    session: Session,
    job_id: uuid.UUID,
    storage_client: ObjectStorageClient,
) -> IngestJob:
    job = session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Ingest job {job_id} not found")
    if job.status != "source_downloaded":
        raise ValueError(
            f"Ingest job {job_id} is not ready for source upload: {job.status}"
        )

    assets = _assets_for_job(session, job_id=job.id)
    if not assets:
        raise ValueError(f"Ingest job {job_id} has no downloaded assets to upload")

    started_at = _now_utc()
    _start_upload(session, job=job, started_at=started_at)
    record_audit_event(
        session=session,
        actor=job.requested_by,
        action="ingest_job.source_upload_started",
        resource_type="ingest_job",
        resource_id=str(job.id),
        message="Started object storage upload for source media",
        payload={
            "asset_ids": [str(asset.id) for asset in assets],
            "asset_count": len(assets),
        },
    )
    session.commit()
    session.refresh(job)

    workspace_to_cleanup = _workspace_dir(assets[0])
    try:
        uploaded_assets: list[MediaAsset] = []
        for asset in assets:
            uploaded_at = _now_utc()
            reusable_asset = _find_existing_uploaded_asset(session, asset=asset)
            if reusable_asset is not None:
                _mark_asset_as_reused(
                    asset=asset,
                    existing_asset=reusable_asset,
                    uploaded_at=uploaded_at,
                )
                session.add(asset)
                uploaded_assets.append(asset)
                continue

            local_path = _local_asset_path(asset)
            if not local_path.is_file():
                raise ValueError(f"Local upload source does not exist: {local_path}")
            if not asset.s3_bucket or not asset.s3_key:
                raise ValueError(
                    f"Media asset {asset.id} is missing object storage location data"
                )

            result = storage_client.multipart_upload_file(
                bucket=asset.s3_bucket,
                key=asset.s3_key,
                local_path=local_path,
                content_type=asset.content_type,
                metadata={
                    "bvid": asset.bvid,
                    "asset_type": asset.asset_type,
                    "job_id": str(job.id),
                    "asset_id": str(asset.id),
                },
            )

            asset.status = "uploaded"
            asset.etag = result.etag
            asset.size_bytes = result.size_bytes
            asset.content_type = result.content_type or asset.content_type
            asset.ready_at = uploaded_at
            metadata_json = dict(asset.metadata_json)
            metadata_json["uploaded_at"] = asset.ready_at.isoformat()
            metadata_json["uploaded_bucket"] = result.bucket
            metadata_json["uploaded_key"] = result.key
            metadata_json["verified_size_bytes"] = result.size_bytes
            metadata_json["verified_etag"] = result.etag
            metadata_json["local_file_removed"] = True
            asset.metadata_json = metadata_json
            session.add(asset)
            uploaded_assets.append(asset)

        queued_subtitle_assets = enqueue_subtitle_transcription_tasks(
            session,
            job=job,
            source_assets=uploaded_assets,
            replace_existing_ready=bool(job.options.get("force_refresh")),
        )

        completed_at = _now_utc()
        if bool(job.options.get("transcribe_subtitles")):
            _merge_progress(
                job,
                payload={
                    "subtitle_transcription": {
                        "requested": True,
                        "variant": "openai-stt",
                        "task_asset_ids": [
                            str(asset.id) for asset in queued_subtitle_assets
                        ],
                        "task_asset_count": len(queued_subtitle_assets),
                        "queued_at": completed_at.isoformat(),
                    }
                },
            )
            session.add(job)
        _complete_upload(
            session,
            job=job,
            asset_ids=[asset.id for asset in uploaded_assets],
            completed_at=completed_at,
        )
        record_audit_event(
            session=session,
            actor=job.requested_by,
            action="ingest_job.source_upload_completed",
            resource_type="ingest_job",
            resource_id=str(job.id),
            message="Completed object storage upload for source media",
            payload={
                "asset_ids": [str(asset.id) for asset in uploaded_assets],
                "asset_count": len(uploaded_assets),
            },
        )
        session.commit()
        _cleanup_workspace(workspace_to_cleanup)
    except Exception as exc:
        session.rollback()
        failed_job = session.get(IngestJob, job_id)
        if failed_job is None:
            raise
        failed_at = _now_utc()
        _fail_upload(
            session,
            job=failed_job,
            error_code=(
                exc.error_code if isinstance(exc, ObjectStorageError) else "storage_upload_failed"
            ),
            message=str(exc),
            failed_at=failed_at,
        )
        record_audit_event(
            session=session,
            actor=failed_job.requested_by,
            action="ingest_job.source_upload_failed",
            resource_type="ingest_job",
            resource_id=str(failed_job.id),
            message="Object storage upload for source media failed",
            payload={"error": str(exc)},
        )
        session.commit()
        job = failed_job

    session.refresh(job)
    return job

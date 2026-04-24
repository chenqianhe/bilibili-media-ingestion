from __future__ import annotations

import hashlib
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.ingest_models import IngestJob, MediaAsset, Uploader, Video
from app.services.upload_ingest import process_upload_ingest_job
from app.uploader.base import (
    ObjectStorageResult,
    ObjectStorageUploadError,
    ObjectStorageVerificationError,
)
from app.workers.upload_ingest import process_next_upload_ingest_job
from tests.utils.utils import random_bvid


class RecordingObjectStorageClient:
    def __init__(self, *, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.uploaded: list[tuple[str, str]] = []

    def multipart_upload_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectStorageResult:
        del metadata
        remote_path = self.root_dir / bucket / key
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, remote_path)
        self.uploaded.append((bucket, key))
        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=remote_path.stat().st_size,
            etag=hashlib.md5(remote_path.read_bytes()).hexdigest(),  # noqa: S324
            content_type=content_type,
        )


class FailingObjectStorageClient:
    def __init__(self, *, error: ObjectStorageUploadError) -> None:
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def multipart_upload_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectStorageResult:
        del local_path, content_type, metadata
        self.calls.append((bucket, key))
        raise self.error


class DispatchingObjectStorageClient:
    def __init__(self, *, root_dir: Path) -> None:
        self.delegate = RecordingObjectStorageClient(root_dir=root_dir)

    def multipart_upload_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectStorageResult:
        return self.delegate.multipart_upload_file(
            bucket=bucket,
            key=key,
            local_path=local_path,
            content_type=content_type,
            metadata=metadata,
        )


def create_video(db: Session, *, bvid: str) -> Video:
    uploader = db.get(Uploader, 42)
    if uploader is None:
        uploader = Uploader(mid=42, name="Uploader 42", raw={"source": "test"})
        db.add(uploader)
        db.commit()

    video = Video(
        bvid=bvid,
        aid=int.from_bytes(bvid.encode("utf-8"), "little") % 2_000_000_000 + 1,
        title=f"Video {bvid}",
        duration_seconds=330,
        owner_mid=42,
        owner_name="Uploader 42",
        raw={"source": "test"},
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def create_source_uploaded_candidate_job(
    db: Session,
    *,
    bvid: str,
    priority: int = 100,
    transcribe_subtitles: bool = False,
) -> IngestJob:
    job = IngestJob(
        input_text=f"https://www.bilibili.com/video/{bvid}",
        normalized_bvid=bvid,
        requested_by="tester@example.com",
        status="source_downloaded",
        phase="source media downloaded; ready for upload worker",
        priority=priority,
        options={
            "download_video": True,
            "transcribe_subtitles": transcribe_subtitles,
        },
        progress={"next_step": "upload_worker"},
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        finished_at=None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def create_downloaded_asset(
    db: Session,
    *,
    job_id: uuid.UUID,
    bvid: str,
    workspace_dir: Path,
    filename: str = "source.mp4",
    content: bytes | None = None,
) -> MediaAsset:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    resolved_content = content or f"{bvid}:{filename}".encode()
    local_path = workspace_dir / filename
    local_path.write_bytes(resolved_content)

    asset = MediaAsset(
        bvid=bvid,
        job_id=job_id,
        asset_type="source_archive",
        status="downloaded",
        s3_bucket="bili-media-dev",
        s3_key=f"media/source/bvid={bvid}/cid=unknown/asset_id=test/{filename}",
        filename=filename,
        content_type="video/mp4",
        size_bytes=len(resolved_content),
        sha256=hashlib.sha256(resolved_content).hexdigest(),
        metadata_json={
            "local_path": str(local_path),
            "workspace_dir": str(workspace_dir),
        },
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def test_process_upload_ingest_job_uploads_assets_and_cleans_workspace(
    db: Session, tmp_path: Path
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_source_uploaded_candidate_job(db, bvid=bvid)
    workspace_dir = tmp_path / "jobs" / str(job.id) / "source"
    asset = create_downloaded_asset(
        db, job_id=job.id, bvid=bvid, workspace_dir=workspace_dir
    )

    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processed_job = process_upload_ingest_job(
        session=db,
        job_id=job.id,
        storage_client=storage_client,
    )

    assert processed_job.status == "source_uploaded"
    assert processed_job.phase == "source media uploaded; ready for processing worker"
    assert processed_job.progress["next_step"] == "media_processing_worker"

    uploaded_asset = db.get(MediaAsset, asset.id)
    assert uploaded_asset is not None
    assert uploaded_asset.status == "uploaded"
    assert uploaded_asset.etag is not None
    assert uploaded_asset.ready_at is not None
    assert uploaded_asset.metadata_json["local_file_removed"] is True
    assert not workspace_dir.exists()
    assert (tmp_path / "remote" / "bili-media-dev" / uploaded_asset.s3_key).is_file()


def test_process_upload_ingest_job_enqueues_subtitle_transcription_task(
    db: Session, tmp_path: Path
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_source_uploaded_candidate_job(
        db,
        bvid=bvid,
        transcribe_subtitles=True,
    )
    workspace_dir = tmp_path / "jobs" / str(job.id) / "source"
    asset = create_downloaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        workspace_dir=workspace_dir,
        filename="lesson.mp4",
    )

    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processed_job = process_upload_ingest_job(
        session=db,
        job_id=job.id,
        storage_client=storage_client,
    )

    assert processed_job.progress["subtitle_transcription"]["requested"] is True
    assert processed_job.progress["subtitle_transcription"]["task_asset_count"] == 1

    subtitle_assets = list(
        db.exec(
            select(MediaAsset).where(
                MediaAsset.job_id == job.id,
                MediaAsset.asset_type == "subtitle",
            )
        ).all()
    )
    assert len(subtitle_assets) == 1
    subtitle_asset = subtitle_assets[0]
    assert subtitle_asset.variant == "openai-stt"
    assert subtitle_asset.status == "pending"
    assert subtitle_asset.content_type == "application/json"
    assert (
        subtitle_asset.metadata_json["transcription_source_asset_id"] == str(asset.id)
    )
    assert subtitle_asset.metadata_json["transcription_temperature"] == 0.0
    assert subtitle_asset.metadata_json["audio_format"] == "m4a"


def test_process_upload_ingest_job_rolls_back_asset_state_on_failure(
    db: Session, tmp_path: Path
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_source_uploaded_candidate_job(db, bvid=bvid)
    workspace_dir = tmp_path / "jobs" / str(job.id) / "source"
    asset = create_downloaded_asset(
        db, job_id=job.id, bvid=bvid, workspace_dir=workspace_dir
    )

    processed_job = process_upload_ingest_job(
        session=db,
        job_id=job.id,
        storage_client=FailingObjectStorageClient(
            error=ObjectStorageVerificationError("head verification failed")
        ),
    )

    assert processed_job.status == "failed"
    assert processed_job.error_code == "storage_upload_verification_failed"
    assert processed_job.retry_count == 1

    failed_asset = db.get(MediaAsset, asset.id)
    assert failed_asset is not None
    assert failed_asset.status == "downloaded"
    assert failed_asset.etag is None
    assert workspace_dir.exists()


def test_process_next_upload_ingest_job_uses_priority_order(
    db: Session, tmp_path: Path
) -> None:
    lower_priority_bvid = random_bvid()
    higher_priority_bvid = random_bvid()
    create_video(db, bvid=lower_priority_bvid)
    create_video(db, bvid=higher_priority_bvid)

    lower_priority_job = create_source_uploaded_candidate_job(
        db, bvid=lower_priority_bvid, priority=-30_002
    )
    higher_priority_job = create_source_uploaded_candidate_job(
        db, bvid=higher_priority_bvid, priority=-30_003
    )
    create_downloaded_asset(
        db,
        job_id=lower_priority_job.id,
        bvid=lower_priority_bvid,
        workspace_dir=tmp_path / "jobs" / str(lower_priority_job.id) / "source",
        filename="lower.mp4",
    )
    create_downloaded_asset(
        db,
        job_id=higher_priority_job.id,
        bvid=higher_priority_bvid,
        workspace_dir=tmp_path / "jobs" / str(higher_priority_job.id) / "source",
        filename="higher.mp4",
    )

    storage_client = DispatchingObjectStorageClient(root_dir=tmp_path / "remote")
    processed_job = process_next_upload_ingest_job(
        session=db,
        storage_client=storage_client,
    )

    assert processed_job is not None
    assert processed_job.id == higher_priority_job.id
    assert processed_job.status == "source_uploaded"

    second_processed_job = process_next_upload_ingest_job(
        session=db,
        storage_client=storage_client,
    )
    assert second_processed_job is not None
    assert second_processed_job.id == lower_priority_job.id
    assert second_processed_job.status == "source_uploaded"


def test_process_next_upload_ingest_job_reclaims_stale_uploading_job(
    db: Session, tmp_path: Path
) -> None:
    stale_bvid = random_bvid()
    create_video(db, bvid=stale_bvid)
    stale_job = create_source_uploaded_candidate_job(db, bvid=stale_bvid)
    stale_transition_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    stale_job.priority = -30_000
    stale_job.status = "uploading_source"
    stale_job.phase = "uploading source media to object storage"
    stale_job.progress = {
        "current_step": "source_uploading",
        "last_transition_at": stale_transition_at.isoformat(),
        "next_step": "upload_worker",
    }
    db.add(stale_job)
    db.commit()
    db.refresh(stale_job)

    create_downloaded_asset(
        db,
        job_id=stale_job.id,
        bvid=stale_bvid,
        workspace_dir=tmp_path / "jobs" / str(stale_job.id) / "source",
    )
    storage_client = DispatchingObjectStorageClient(root_dir=tmp_path / "remote")

    processed_job = process_next_upload_ingest_job(
        session=db,
        storage_client=storage_client,
    )

    assert processed_job is not None
    assert processed_job.id == stale_job.id
    assert processed_job.status == "source_uploaded"
    assert processed_job.progress["reclaim"]["count"] == 1
    assert processed_job.progress["reclaim"]["previous_status"] == "uploading_source"


def test_process_next_upload_ingest_job_skips_fresh_uploading_jobs(
    db: Session, tmp_path: Path
) -> None:
    fresh_bvid = random_bvid()
    queued_bvid = random_bvid()
    create_video(db, bvid=fresh_bvid)
    create_video(db, bvid=queued_bvid)

    fresh_job = create_source_uploaded_candidate_job(db, bvid=fresh_bvid, priority=1)
    fresh_transition_at = datetime.now(timezone.utc)
    fresh_job.priority = -30_000
    fresh_job.status = "uploading_source"
    fresh_job.phase = "uploading source media to object storage"
    fresh_job.progress = {
        "current_step": "source_uploading",
        "last_transition_at": fresh_transition_at.isoformat(),
        "next_step": "upload_worker",
    }
    db.add(fresh_job)

    queued_job = create_source_uploaded_candidate_job(
        db, bvid=queued_bvid, priority=-29_999
    )
    db.add(queued_job)
    db.commit()
    db.refresh(fresh_job)
    db.refresh(queued_job)

    create_downloaded_asset(
        db,
        job_id=fresh_job.id,
        bvid=fresh_bvid,
        workspace_dir=tmp_path / "jobs" / str(fresh_job.id) / "source",
        filename="fresh.mp4",
    )
    create_downloaded_asset(
        db,
        job_id=queued_job.id,
        bvid=queued_bvid,
        workspace_dir=tmp_path / "jobs" / str(queued_job.id) / "source",
        filename="queued.mp4",
    )
    storage_client = DispatchingObjectStorageClient(root_dir=tmp_path / "remote")

    processed_job = process_next_upload_ingest_job(
        session=db,
        storage_client=storage_client,
    )

    assert processed_job is not None
    assert processed_job.id == queued_job.id
    assert processed_job.status == "source_uploaded"
    skipped_job = db.get(IngestJob, fresh_job.id)
    assert skipped_job is not None
    assert skipped_job.status == "uploading_source"
    assert "reclaim" not in skipped_job.progress


def test_process_upload_ingest_job_requires_local_file(
    db: Session, tmp_path: Path
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_source_uploaded_candidate_job(db, bvid=bvid)
    workspace_dir = tmp_path / "jobs" / str(job.id) / "source"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    asset = MediaAsset(
        bvid=bvid,
        job_id=job.id,
        asset_type="source_archive",
        status="downloaded",
        s3_bucket="bili-media-dev",
        s3_key=f"media/source/bvid={bvid}/cid=unknown/asset_id=test/missing.mp4",
        filename="missing.mp4",
        content_type="video/mp4",
        metadata_json={
            "local_path": str(workspace_dir / "missing.mp4"),
            "workspace_dir": str(workspace_dir),
        },
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    processed_job = process_upload_ingest_job(
        session=db,
        job_id=job.id,
        storage_client=RecordingObjectStorageClient(root_dir=tmp_path / "remote"),
    )

    assert processed_job.status == "failed"
    assert processed_job.error_code == "storage_upload_failed"
    assert "Local upload source does not exist" in (processed_job.error_message or "")
    unchanged_asset = db.get(MediaAsset, asset.id)
    assert unchanged_asset is not None
    assert unchanged_asset.status == "downloaded"


def test_process_upload_ingest_job_reuses_existing_uploaded_asset(
    db: Session, tmp_path: Path
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)

    existing_asset = MediaAsset(
        bvid=bvid,
        job_id=None,
        asset_type="source_archive",
        status="uploaded",
        s3_bucket="bili-media-dev",
        s3_key=f"media/source/bvid={bvid}/cid=unknown/asset_id=existing/source.mp4",
        filename="source.mp4",
        content_type="video/mp4",
        size_bytes=len(b"same-content"),
        sha256=hashlib.sha256(b"same-content").hexdigest(),
        etag="existing-etag",
        ready_at=datetime.now(timezone.utc),
        metadata_json={"seed": "existing"},
    )
    db.add(existing_asset)
    db.commit()
    db.refresh(existing_asset)

    job = create_source_uploaded_candidate_job(db, bvid=bvid)
    workspace_dir = tmp_path / "jobs" / str(job.id) / "source"
    new_asset = create_downloaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        workspace_dir=workspace_dir,
        filename="duplicate.mp4",
        content=b"same-content",
    )

    processed_job = process_upload_ingest_job(
        session=db,
        job_id=job.id,
        storage_client=RecordingObjectStorageClient(root_dir=tmp_path / "remote"),
    )

    assert processed_job.status == "source_uploaded"
    reused_asset = db.get(MediaAsset, new_asset.id)
    assert reused_asset is not None
    assert reused_asset.status == "uploaded"
    assert reused_asset.s3_key == existing_asset.s3_key
    assert reused_asset.etag == existing_asset.etag
    assert reused_asset.sha256 is None
    assert reused_asset.metadata_json["reused_from_asset_id"] == str(existing_asset.id)

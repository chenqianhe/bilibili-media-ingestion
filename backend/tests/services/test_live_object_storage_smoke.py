from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.ingest_models import IngestJob, MediaAsset, Video
from app.processor.ffmpeg import FFmpegMediaProcessor
from app.services.media_processing import process_media_processing_job
from app.services.storage_keys import build_asset_storage_key
from app.services.upload_ingest import process_upload_ingest_job
from app.uploader.s3_multipart import S3MultipartObjectStorageClient
from tests.utils.utils import random_bvid


def _settings_value(name: str) -> str | None:
    if name == "RUN_LIVE_S3_SMOKE":
        return "1" if settings.RUN_LIVE_S3_SMOKE else None
    if name in {
        "S3_ENDPOINT_URL",
        "S3_ACCESS_KEY",
        "S3_SECRET_KEY",
        "S3_BUCKET",
        "S3_REGION",
    }:
        value = getattr(settings, name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    for name in names:
        value = _settings_value(name)
        if value and value.strip():
            return value.strip()
    return None


def _live_smoke_enabled() -> bool:
    return _env_value("RUN_LIVE_S3_SMOKE") == "1"


pytestmark = pytest.mark.skipif(
    not _live_smoke_enabled(),
    reason=(
        "Set RUN_LIVE_S3_SMOKE=1 with live S3 credentials "
        "in the environment or root .env.local to run this smoke test"
    ),
)


def _build_storage_client() -> S3MultipartObjectStorageClient:
    endpoint_url = _env_value("S3_ENDPOINT_URL", "ENDPOINT_URL")
    access_key = _env_value("S3_ACCESS_KEY", "ACCESS_KEY_ID")
    secret_key = _env_value("S3_SECRET_KEY", "SECRET_ACCESS_KEY")
    region = _env_value("S3_REGION")

    missing = [
        name
        for name, value in (
            ("S3 endpoint", endpoint_url),
            ("S3 access key", access_key),
            ("S3 secret key", secret_key),
        )
        if not value
    ]
    if missing:
        pytest.skip(f"Missing live S3 configuration: {', '.join(missing)}")

    return S3MultipartObjectStorageClient(
        endpoint_url=endpoint_url,
        access_key=access_key,
        secret_key=secret_key,
        region=region,
    )


def _bucket_name() -> str:
    bucket = _env_value("S3_BUCKET", "BUCKET_NAME")
    if not bucket:
        pytest.skip("Missing S3_BUCKET/BUCKET_NAME for live smoke test")
    return bucket


def _create_sample_video(path: Path) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=24",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:duration=2",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _create_video(db: Session, *, bvid: str) -> Video:
    video = Video(
        bvid=bvid,
        aid=int.from_bytes(bvid.encode("utf-8"), "little") % 2_000_000_000 + 1,
        title=f"Live smoke {bvid}",
        duration_seconds=2,
        raw={"source": "live-smoke"},
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def _create_source_downloaded_job(db: Session, *, bvid: str) -> IngestJob:
    job = IngestJob(
        input_text=f"https://www.bilibili.com/video/{bvid}",
        normalized_bvid=bvid,
        requested_by="live-smoke@example.com",
        status="source_downloaded",
        phase="source media downloaded; ready for upload worker",
        options={
            "download_video": True,
            "create_normalized_mp4": True,
            "create_hls": False,
        },
        progress={"next_step": "upload_worker"},
        started_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _create_downloaded_asset(
    db: Session,
    *,
    job: IngestJob,
    bucket: str,
    source_path: Path,
    workspace_dir: Path,
) -> MediaAsset:
    content_type, _ = mimetypes.guess_type(source_path.name)
    asset = MediaAsset(
        bvid=job.normalized_bvid or "",
        job_id=job.id,
        asset_type="source_archive",
        status="downloaded",
        s3_bucket=bucket,
        s3_region=_env_value("S3_REGION"),
        filename=source_path.name,
        content_type=content_type,
        container_format=source_path.suffix.lower().lstrip(".") or None,
        size_bytes=source_path.stat().st_size,
        sha256=_sha256_file(source_path),
        metadata_json={
            "local_path": str(source_path),
            "workspace_dir": str(workspace_dir),
            "source": "live-smoke",
        },
    )
    asset.s3_key = build_asset_storage_key(
        asset_type=asset.asset_type,
        bvid=asset.bvid,
        cid=asset.cid,
        asset_id=asset.id,
        filename=asset.filename,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def test_live_upload_and_media_processing_against_object_storage(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    storage_client = _build_storage_client()
    processor = FFmpegMediaProcessor()
    bucket = _bucket_name()

    bvid = random_bvid()
    _create_video(db, bvid=bvid)
    job = _create_source_downloaded_job(db, bvid=bvid)

    workspace_dir = tmp_path / "jobs" / str(job.id) / "source"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    source_path = workspace_dir / "live-source.mp4"
    _create_sample_video(source_path)
    source_asset = _create_downloaded_asset(
        db,
        job=job,
        bucket=bucket,
        source_path=source_path,
        workspace_dir=workspace_dir,
    )

    remote_objects: list[tuple[str, str]] = []
    try:
        uploaded_job = process_upload_ingest_job(
            session=db,
            job_id=job.id,
            storage_client=storage_client,
        )
        assert uploaded_job.status == "source_uploaded"

        source_asset = db.get(MediaAsset, source_asset.id)
        assert source_asset is not None
        assert source_asset.s3_bucket
        assert source_asset.s3_key
        remote_objects.append((source_asset.s3_bucket, source_asset.s3_key))

        processed_job = process_media_processing_job(
            session=db,
            job_id=job.id,
            storage_client=storage_client,
            processor=processor,
        )
        assert processed_job.status == "completed"

        assets = list(
            db.exec(select(MediaAsset).where(MediaAsset.job_id == job.id)).all()
        )
        normalized_asset = next(
            asset for asset in assets if asset.asset_type == "normalized_mp4"
        )
        thumbnail_asset = next(
            asset for asset in assets if asset.asset_type == "thumbnail"
        )
        remote_objects.extend(
            [
                (normalized_asset.s3_bucket or "", normalized_asset.s3_key or ""),
                (thumbnail_asset.s3_bucket or "", thumbnail_asset.s3_key or ""),
            ]
        )

        assert normalized_asset.status == "ready"
        assert normalized_asset.size_bytes is not None and normalized_asset.size_bytes > 0
        assert normalized_asset.video_codec == "h264"
        assert normalized_asset.container_format == "mp4"

        assert thumbnail_asset.status == "ready"
        assert thumbnail_asset.size_bytes is not None and thumbnail_asset.size_bytes > 0
        assert thumbnail_asset.container_format in {"jpeg", "mjpeg", "jpg"}

        normalized_download = tmp_path / "normalized-download.mp4"
        thumbnail_download = tmp_path / "thumbnail-download.jpg"
        storage_client.download_file(
            bucket=normalized_asset.s3_bucket or "",
            key=normalized_asset.s3_key or "",
            local_path=normalized_download,
        )
        storage_client.download_file(
            bucket=thumbnail_asset.s3_bucket or "",
            key=thumbnail_asset.s3_key or "",
            local_path=thumbnail_download,
        )

        assert normalized_download.is_file()
        assert normalized_download.stat().st_size == normalized_asset.size_bytes
        assert thumbnail_download.is_file()
        assert thumbnail_download.stat().st_size == thumbnail_asset.size_bytes
    finally:
        for bucket_name, key in reversed(remote_objects):
            if bucket_name and key:
                try:
                    storage_client.delete_object(bucket=bucket_name, key=key)
                except Exception:
                    pass
        shutil.rmtree(tmp_path, ignore_errors=True)

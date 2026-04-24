from __future__ import annotations

import hashlib
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.ingest_models import IngestJob, MediaAsset, Video
from app.processor.base import MediaProbeResult
from app.services.media_processing import process_media_processing_job
from app.services.storage_keys import build_asset_storage_key
from app.uploader.base import (
    ObjectStorageResult,
    ObjectStorageUploadError,
)
from app.workers.media_processing import process_next_media_processing_job
from tests.utils.utils import random_bvid


class RecordingObjectStorageClient:
    def __init__(self, *, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.uploaded: list[tuple[str, str]] = []
        self.downloaded: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []

    def remote_path(self, *, bucket: str, key: str) -> Path:
        return self.root_dir / bucket / key

    def seed_object(self, *, bucket: str, key: str, content: bytes) -> None:
        remote_path = self.remote_path(bucket=bucket, key=key)
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        remote_path.write_bytes(content)

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
        remote_path = self.remote_path(bucket=bucket, key=key)
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        remote_path.write_bytes(local_path.read_bytes())
        self.uploaded.append((bucket, key))
        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=remote_path.stat().st_size,
            etag=hashlib.md5(remote_path.read_bytes()).hexdigest(),  # noqa: S324
            content_type=content_type,
        )

    def download_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
    ) -> ObjectStorageResult:
        remote_path = self.remote_path(bucket=bucket, key=key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(remote_path.read_bytes())
        self.downloaded.append((bucket, key))
        content_type, _ = mimetypes.guess_type(remote_path.name)
        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=local_path.stat().st_size,
            etag=hashlib.md5(local_path.read_bytes()).hexdigest(),  # noqa: S324
            content_type=content_type,
        )

    def delete_object(
        self,
        *,
        bucket: str,
        key: str,
    ) -> None:
        remote_path = self.remote_path(bucket=bucket, key=key)
        remote_path.unlink(missing_ok=True)
        self.deleted.append((bucket, key))


class FailingUploadStorageClient(RecordingObjectStorageClient):
    def __init__(self, *, root_dir: Path, fail_on_attempt: int) -> None:
        super().__init__(root_dir=root_dir)
        self.fail_on_attempt = fail_on_attempt
        self.upload_attempts = 0

    def multipart_upload_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectStorageResult:
        self.upload_attempts += 1
        if self.upload_attempts == self.fail_on_attempt:
            raise ObjectStorageUploadError("storage upload failed during media processing")
        return super().multipart_upload_file(
            bucket=bucket,
            key=key,
            local_path=local_path,
            content_type=content_type,
            metadata=metadata,
        )


class StaticMediaProcessor:
    def __init__(
        self,
        *,
        normalized_content: bytes | None = None,
        proxy_content: bytes | None = None,
        thumbnail_content: bytes | None = None,
    ) -> None:
        unique_suffix = uuid.uuid4().hex.encode("ascii")
        self.normalized_content = (
            normalized_content or b"normalized-mp4-binary-" + unique_suffix
        )
        self.proxy_content = proxy_content or b"proxy-mp4-binary-" + unique_suffix
        self.thumbnail_content = (
            thumbnail_content or b"thumbnail-jpeg-binary-" + unique_suffix
        )
        self.normalized_calls: list[tuple[Path, Path | None, bool, Path]] = []
        self.proxy_calls: list[tuple[Path, Path | None, bool, Path]] = []
        self.hls_calls: list[tuple[Path, Path | None, bool, Path]] = []
        self.thumbnail_calls: list[tuple[Path, Path, float | None]] = []

    def probe(self, *, input_path: Path) -> MediaProbeResult:
        lower_name = input_path.name.lower()
        if lower_name.endswith((".jpg", ".jpeg")):
            return MediaProbeResult(
                container_format="jpeg",
                video_codec="mjpeg",
                width=1280,
                height=720,
                bitrate=128_000,
                has_video=True,
                stream_count=1,
                raw={"kind": "thumbnail"},
            )
        if lower_name.endswith(".m3u8"):
            return MediaProbeResult(
                container_format="m3u8",
                raw={"kind": "hls_playlist"},
            )
        if lower_name.endswith(".ts") or "segment_" in lower_name:
            return MediaProbeResult(
                container_format="mpegts",
                video_codec="h264",
                audio_codec="aac",
                width=1280,
                height=720,
                fps=30.0,
                bitrate=850_000,
                duration_seconds=6.0,
                has_video=True,
                has_audio=True,
                stream_count=2,
                raw={"kind": "hls_segment"},
            )
        if "proxy" in lower_name:
            return MediaProbeResult(
                container_format="mp4",
                video_codec="h264",
                audio_codec="aac",
                width=1280,
                height=720,
                fps=30.0,
                bitrate=850_000,
                duration_seconds=330.0,
                has_video=True,
                has_audio=True,
                stream_count=2,
                raw={"kind": "proxy"},
            )
        if "normalized" in lower_name:
            return MediaProbeResult(
                container_format="mp4",
                video_codec="h264",
                audio_codec="aac",
                width=1280,
                height=720,
                fps=30.0,
                bitrate=900_000,
                duration_seconds=330.0,
                has_video=True,
                has_audio=True,
                stream_count=2,
                raw={"kind": "normalized"},
            )
        if "audio" in lower_name or input_path.suffix.lower() == ".m4a":
            return MediaProbeResult(
                container_format="m4a",
                audio_codec="aac",
                bitrate=128_000,
                duration_seconds=330.0,
                has_audio=True,
                stream_count=1,
                raw={"kind": "audio_source"},
            )
        if "video" in lower_name:
            return MediaProbeResult(
                container_format="mp4",
                video_codec="h264",
                width=1920,
                height=1080,
                fps=60.0,
                bitrate=1_500_000,
                duration_seconds=330.0,
                has_video=True,
                stream_count=1,
                raw={"kind": "video_source"},
            )
        return MediaProbeResult(
            container_format="mp4",
            video_codec="h264",
            audio_codec="aac",
            width=1920,
            height=1080,
            fps=30.0,
            bitrate=1_250_000,
            duration_seconds=330.0,
            has_video=True,
            has_audio=True,
            stream_count=2,
            raw={"kind": "source_archive"},
        )

    def create_normalized_mp4(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_path: Path,
        include_audio_from_video_input: bool = True,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.normalized_content)
        self.normalized_calls.append(
            (
                video_input_path,
                audio_input_path,
                include_audio_from_video_input,
                output_path,
            )
        )

    def create_proxy_mp4(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_path: Path,
        include_audio_from_video_input: bool = True,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.proxy_content)
        self.proxy_calls.append(
            (
                video_input_path,
                audio_input_path,
                include_audio_from_video_input,
                output_path,
            )
        )

    def create_hls_package(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_dir: Path,
        include_audio_from_video_input: bool = True,
        segment_duration_seconds: int = 6,
    ) -> Path:
        del segment_duration_seconds
        output_dir.mkdir(parents=True, exist_ok=True)
        media_playlist = output_dir / "stream.m3u8"
        master_playlist = output_dir / "master.m3u8"
        segment_path = output_dir / "segment_00000.ts"
        segment_path.write_bytes(b"hls-segment-binary")
        media_playlist.write_text(
            "\n".join(
                [
                    "#EXTM3U",
                    "#EXT-X-TARGETDURATION:6",
                    "#EXT-X-VERSION:3",
                    "#EXT-X-PLAYLIST-TYPE:VOD",
                    "#EXTINF:6.0,",
                    segment_path.name,
                    "#EXT-X-ENDLIST",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        master_playlist.write_text(
            "\n".join(
                [
                    "#EXTM3U",
                    "#EXT-X-VERSION:3",
                    "#EXT-X-STREAM-INF:BANDWIDTH=1200000",
                    media_playlist.name,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.hls_calls.append(
            (
                video_input_path,
                audio_input_path,
                include_audio_from_video_input,
                output_dir,
            )
        )
        return master_playlist

    def create_thumbnail(
        self,
        *,
        video_input_path: Path,
        output_path: Path,
        offset_seconds: float | None = None,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.thumbnail_content)
        self.thumbnail_calls.append((video_input_path, output_path, offset_seconds))


def create_video(db: Session, *, bvid: str) -> Video:
    video = Video(
        bvid=bvid,
        aid=int.from_bytes(bvid.encode("utf-8"), "little") % 2_000_000_000 + 1,
        title=f"Video {bvid}",
        duration_seconds=330,
        raw={"source": "test"},
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def create_source_uploaded_job(
    db: Session,
    *,
    bvid: str,
    priority: int = 100,
    options: dict[str, object] | None = None,
) -> IngestJob:
    job = IngestJob(
        input_text=f"https://www.bilibili.com/video/{bvid}",
        normalized_bvid=bvid,
        requested_by="tester@example.com",
        status="source_uploaded",
        phase="source media uploaded; ready for processing worker",
        priority=priority,
        options=options
        or {
            "download_video": True,
            "create_normalized_mp4": True,
            "create_hls": False,
        },
        progress={"next_step": "media_processing_worker"},
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        finished_at=None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def create_uploaded_source_asset(
    db: Session,
    *,
    job_id: uuid.UUID,
    bvid: str,
    storage_client: RecordingObjectStorageClient,
    filename: str,
    content: bytes,
    asset_type: str = "source_archive",
    cid: int | None = None,
) -> MediaAsset:
    content_type, _ = mimetypes.guess_type(filename)
    asset = MediaAsset(
        bvid=bvid,
        cid=cid,
        job_id=job_id,
        asset_type=asset_type,
        status="uploaded",
        s3_bucket="bili-media-dev",
        s3_region="us-east-1",
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        etag=hashlib.md5(content).hexdigest(),  # noqa: S324
        ready_at=datetime.now(timezone.utc),
        metadata_json={"uploaded_at": datetime.now(timezone.utc).isoformat()},
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
    storage_client.seed_object(
        bucket=asset.s3_bucket or "bili-media-dev",
        key=asset.s3_key or "",
        content=content,
    )
    return asset


def create_existing_ready_asset(
    db: Session,
    *,
    bvid: str,
    storage_client: RecordingObjectStorageClient,
    asset_type: str,
    filename: str,
    content: bytes,
    cid: int | None = None,
) -> MediaAsset:
    content_type, _ = mimetypes.guess_type(filename)
    asset = MediaAsset(
        bvid=bvid,
        cid=cid,
        job_id=None,
        asset_type=asset_type,
        status="ready",
        s3_bucket="bili-media-dev",
        s3_region="us-east-1",
        filename=filename,
        content_type=content_type,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        etag=hashlib.md5(content).hexdigest(),  # noqa: S324
        ready_at=datetime.now(timezone.utc),
        metadata_json={"seed": "existing"},
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
    storage_client.seed_object(
        bucket=asset.s3_bucket or "bili-media-dev",
        key=asset.s3_key or "",
        content=content,
    )
    return asset


def asset_list_for_job(db: Session, *, job_id: uuid.UUID) -> list[MediaAsset]:
    statement = (
        select(MediaAsset)
        .where(MediaAsset.job_id == job_id)
        .order_by(MediaAsset.created_at.asc())
    )
    return list(db.exec(statement).all())


def test_process_media_processing_job_creates_derivatives(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processor = StaticMediaProcessor()
    job = create_source_uploaded_job(db, bvid=bvid)
    source_asset = create_uploaded_source_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        storage_client=storage_client,
        filename="source.mp4",
        content=b"uploaded-source-archive",
    )

    processed_job = process_media_processing_job(
        session=db,
        job_id=job.id,
        storage_client=storage_client,
        processor=processor,
    )

    assert processed_job.status == "completed"
    assert processed_job.phase == "media processed; derivative assets ready"
    assert processed_job.progress["next_step"] == "job_complete"
    assert processed_job.finished_at is not None
    assert not (tmp_path / "jobs" / str(job.id) / "processing").exists()

    updated_source_asset = db.get(MediaAsset, source_asset.id)
    assert updated_source_asset is not None
    assert updated_source_asset.status == "uploaded"
    assert updated_source_asset.container_format == "mp4"
    assert updated_source_asset.video_codec == "h264"
    assert updated_source_asset.audio_codec == "aac"
    assert updated_source_asset.width == 1920
    assert updated_source_asset.height == 1080
    assert updated_source_asset.metadata_json["ffprobe"]["kind"] == "source_archive"

    assets = asset_list_for_job(db, job_id=job.id)
    assert {asset.asset_type for asset in assets} == {
        "source_archive",
        "normalized_mp4",
        "thumbnail",
    }

    normalized_asset = next(asset for asset in assets if asset.asset_type == "normalized_mp4")
    thumbnail_asset = next(asset for asset in assets if asset.asset_type == "thumbnail")

    assert normalized_asset.status == "ready"
    assert normalized_asset.variant == "default"
    assert normalized_asset.video_codec == "h264"
    assert normalized_asset.audio_codec == "aac"
    assert normalized_asset.s3_key is not None
    assert storage_client.remote_path(
        bucket=normalized_asset.s3_bucket or "",
        key=normalized_asset.s3_key,
    ).is_file()

    assert thumbnail_asset.status == "ready"
    assert thumbnail_asset.variant == "poster"
    assert thumbnail_asset.container_format == "jpeg"
    assert thumbnail_asset.metadata_json["ffprobe"]["kind"] == "thumbnail"

    assert len(storage_client.downloaded) == 1
    assert len(storage_client.uploaded) == 2
    assert len(processor.normalized_calls) == 1
    assert len(processor.thumbnail_calls) == 1


def test_process_media_processing_job_creates_proxy_and_hls_derivatives(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processor = StaticMediaProcessor()
    job = create_source_uploaded_job(
        db,
        bvid=bvid,
        options={
            "download_video": True,
            "create_normalized_mp4": True,
            "create_hls": True,
        },
    )
    create_uploaded_source_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        storage_client=storage_client,
        filename="source.mp4",
        content=b"uploaded-source-archive",
    )

    processed_job = process_media_processing_job(
        session=db,
        job_id=job.id,
        storage_client=storage_client,
        processor=processor,
    )

    assert processed_job.status == "completed"
    assets = asset_list_for_job(db, job_id=job.id)
    assert {asset.asset_type for asset in assets} == {
        "source_archive",
        "normalized_mp4",
        "proxy_mp4",
        "hls_master",
        "hls_segment",
        "thumbnail",
    }

    proxy_asset = next(asset for asset in assets if asset.asset_type == "proxy_mp4")
    master_asset = next(asset for asset in assets if asset.asset_type == "hls_master")
    playlist_asset = next(
        asset
        for asset in assets
        if asset.asset_type == "hls_segment"
        and asset.metadata_json.get("hls_role") == "media_playlist"
    )
    segment_asset = next(
        asset
        for asset in assets
        if asset.asset_type == "hls_segment"
        and asset.metadata_json.get("hls_role") == "media_segment"
    )

    assert proxy_asset.status == "ready"
    assert proxy_asset.video_codec == "h264"
    assert proxy_asset.audio_codec == "aac"
    assert proxy_asset.metadata_json["ffprobe"]["kind"] == "proxy"

    assert playlist_asset.metadata_json["hls_relative_path"] == "stream.m3u8"
    playlist_references = playlist_asset.metadata_json["hls_references"]
    assert playlist_references == [
        {
            "uri": "segment_00000.ts",
            "asset_id": str(segment_asset.id),
            "relative_path": "segment_00000.ts",
        }
    ]
    assert playlist_asset.metadata_json["proxy_asset_id"] == str(proxy_asset.id)

    assert master_asset.metadata_json["hls_relative_path"] == "master.m3u8"
    master_references = master_asset.metadata_json["hls_references"]
    assert master_references == [
        {
            "uri": "stream.m3u8",
            "asset_id": str(playlist_asset.id),
            "relative_path": "stream.m3u8",
        }
    ]
    assert master_asset.metadata_json["proxy_asset_id"] == str(proxy_asset.id)
    assert len(processor.proxy_calls) == 1
    assert len(processor.hls_calls) == 1


def test_process_media_processing_job_handles_split_video_and_audio_sources(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processor = StaticMediaProcessor()
    job = create_source_uploaded_job(db, bvid=bvid)
    video_asset = create_uploaded_source_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        storage_client=storage_client,
        filename="video.mp4",
        content=b"uploaded-video-stream",
        asset_type="source_video_stream",
    )
    audio_asset = create_uploaded_source_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        storage_client=storage_client,
        filename="audio.m4a",
        content=b"uploaded-audio-stream",
        asset_type="source_audio_stream",
    )

    processed_job = process_media_processing_job(
        session=db,
        job_id=job.id,
        storage_client=storage_client,
        processor=processor,
    )

    assert processed_job.status == "completed"
    assert len(processor.normalized_calls) == 1
    normalized_call = processor.normalized_calls[0]
    assert normalized_call[1] is not None
    assert normalized_call[1].name.endswith("audio.m4a")
    assert normalized_call[2] is False

    refreshed_video_asset = db.get(MediaAsset, video_asset.id)
    refreshed_audio_asset = db.get(MediaAsset, audio_asset.id)
    assert refreshed_video_asset is not None
    assert refreshed_audio_asset is not None
    assert refreshed_video_asset.video_codec == "h264"
    assert refreshed_video_asset.audio_codec is None
    assert refreshed_audio_asset.audio_codec == "aac"
    assert refreshed_audio_asset.video_codec is None

    assets = asset_list_for_job(db, job_id=job.id)
    assert {asset.asset_type for asset in assets} == {
        "source_video_stream",
        "source_audio_stream",
        "normalized_mp4",
        "thumbnail",
    }


def test_process_media_processing_job_rolls_back_and_cleans_remote_outputs(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    storage_client = FailingUploadStorageClient(
        root_dir=tmp_path / "remote",
        fail_on_attempt=2,
    )
    processor = StaticMediaProcessor()
    job = create_source_uploaded_job(db, bvid=bvid)
    source_asset = create_uploaded_source_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        storage_client=storage_client,
        filename="source.mp4",
        content=b"uploaded-source-archive",
    )

    processed_job = process_media_processing_job(
        session=db,
        job_id=job.id,
        storage_client=storage_client,
        processor=processor,
    )

    assert processed_job.status == "failed"
    assert processed_job.error_code == "storage_upload_failed"
    assert processed_job.retry_count == 1
    assert not (tmp_path / "jobs" / str(job.id) / "processing").exists()

    refreshed_source_asset = db.get(MediaAsset, source_asset.id)
    assert refreshed_source_asset is not None
    assert refreshed_source_asset.container_format is None

    assets = asset_list_for_job(db, job_id=job.id)
    assert {asset.asset_type for asset in assets} == {"source_archive"}
    assert len(storage_client.uploaded) == 1
    assert len(storage_client.deleted) == 1
    deleted_bucket, deleted_key = storage_client.deleted[0]
    assert not storage_client.remote_path(bucket=deleted_bucket, key=deleted_key).exists()


def test_process_next_media_processing_job_uses_priority_order(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    higher_priority_bvid = random_bvid()
    lower_priority_bvid = random_bvid()
    create_video(db, bvid=higher_priority_bvid)
    create_video(db, bvid=lower_priority_bvid)
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processor = StaticMediaProcessor()

    lower_priority_job = create_source_uploaded_job(
        db,
        bvid=lower_priority_bvid,
        priority=-40_002,
    )
    higher_priority_job = create_source_uploaded_job(
        db,
        bvid=higher_priority_bvid,
        priority=-40_003,
    )
    create_uploaded_source_asset(
        db,
        job_id=lower_priority_job.id,
        bvid=lower_priority_bvid,
        storage_client=storage_client,
        filename="lower.mp4",
        content=b"lower-priority-source",
    )
    create_uploaded_source_asset(
        db,
        job_id=higher_priority_job.id,
        bvid=higher_priority_bvid,
        storage_client=storage_client,
        filename="higher.mp4",
        content=b"higher-priority-source",
    )

    first_processed_job = process_next_media_processing_job(
        session=db,
        storage_client=storage_client,
        processor=processor,
    )
    second_processed_job = process_next_media_processing_job(
        session=db,
        storage_client=storage_client,
        processor=processor,
    )

    assert first_processed_job is not None
    assert second_processed_job is not None
    assert first_processed_job.id == higher_priority_job.id
    assert second_processed_job.id == lower_priority_job.id
    assert first_processed_job.status == "completed"
    assert second_processed_job.status == "completed"


def test_process_next_media_processing_job_reclaims_stale_processing_job(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    stale_bvid = random_bvid()
    create_video(db, bvid=stale_bvid)
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processor = StaticMediaProcessor()
    stale_job = create_source_uploaded_job(db, bvid=stale_bvid)
    stale_transition_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    stale_job.priority = -40_000
    stale_job.status = "processing_media"
    stale_job.phase = "processing uploaded source media"
    stale_job.progress = {
        "current_step": "media_processing",
        "last_transition_at": stale_transition_at.isoformat(),
        "next_step": "media_processing_worker",
    }
    db.add(stale_job)
    db.commit()
    db.refresh(stale_job)

    create_uploaded_source_asset(
        db,
        job_id=stale_job.id,
        bvid=stale_bvid,
        storage_client=storage_client,
        filename="stale.mp4",
        content=b"stale-processing-source",
    )

    processed_job = process_next_media_processing_job(
        session=db,
        storage_client=storage_client,
        processor=processor,
    )

    assert processed_job is not None
    assert processed_job.id == stale_job.id
    assert processed_job.status == "completed"
    assert processed_job.progress["reclaim"]["count"] == 1
    assert processed_job.progress["reclaim"]["previous_status"] == "processing_media"


def test_process_next_media_processing_job_skips_fresh_processing_jobs(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    fresh_bvid = random_bvid()
    queued_bvid = random_bvid()
    create_video(db, bvid=fresh_bvid)
    create_video(db, bvid=queued_bvid)
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processor = StaticMediaProcessor()

    fresh_job = create_source_uploaded_job(db, bvid=fresh_bvid, priority=1)
    fresh_transition_at = datetime.now(timezone.utc)
    fresh_job.priority = -40_000
    fresh_job.status = "processing_media"
    fresh_job.phase = "processing uploaded source media"
    fresh_job.progress = {
        "current_step": "media_processing",
        "last_transition_at": fresh_transition_at.isoformat(),
        "next_step": "media_processing_worker",
    }
    db.add(fresh_job)

    queued_job = create_source_uploaded_job(db, bvid=queued_bvid, priority=-39_999)
    db.add(queued_job)
    db.commit()
    db.refresh(fresh_job)
    db.refresh(queued_job)

    create_uploaded_source_asset(
        db,
        job_id=fresh_job.id,
        bvid=fresh_bvid,
        storage_client=storage_client,
        filename="fresh.mp4",
        content=b"fresh-processing-source",
    )
    create_uploaded_source_asset(
        db,
        job_id=queued_job.id,
        bvid=queued_bvid,
        storage_client=storage_client,
        filename="queued.mp4",
        content=b"queued-processing-source",
    )

    processed_job = process_next_media_processing_job(
        session=db,
        storage_client=storage_client,
        processor=processor,
    )

    assert processed_job is not None
    assert processed_job.id == queued_job.id
    assert processed_job.status == "completed"
    skipped_job = db.get(IngestJob, fresh_job.id)
    assert skipped_job is not None
    assert skipped_job.status == "processing_media"
    assert "reclaim" not in skipped_job.progress


def test_process_media_processing_job_reuses_existing_derivative_asset(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.media_processing.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")
    processor = StaticMediaProcessor()
    existing_asset = create_existing_ready_asset(
        db,
        bvid=bvid,
        storage_client=storage_client,
        asset_type="normalized_mp4",
        filename="existing.normalized.mp4",
        content=processor.normalized_content,
    )
    job = create_source_uploaded_job(db, bvid=bvid)
    create_uploaded_source_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        storage_client=storage_client,
        filename="source.mp4",
        content=b"uploaded-source-archive",
    )

    processed_job = process_media_processing_job(
        session=db,
        job_id=job.id,
        storage_client=storage_client,
        processor=processor,
    )

    assert processed_job.status == "completed"
    assets = asset_list_for_job(db, job_id=job.id)
    normalized_asset = next(asset for asset in assets if asset.asset_type == "normalized_mp4")
    thumbnail_asset = next(asset for asset in assets if asset.asset_type == "thumbnail")

    assert normalized_asset.status == "ready"
    assert normalized_asset.s3_key == existing_asset.s3_key
    assert normalized_asset.sha256 is None
    assert normalized_asset.metadata_json["reused_from_asset_id"] == str(existing_asset.id)
    assert normalized_asset.metadata_json["derived_sha256"] == hashlib.sha256(
        processor.normalized_content
    ).hexdigest()
    assert thumbnail_asset.status == "ready"
    assert len(storage_client.uploaded) == 1

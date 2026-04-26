from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.ingest_models import (
    AuditEvent,
    IngestJob,
    MediaAsset,
    Uploader,
    Video,
    VideoSubtitle,
)
from app.services.subtitle_transcription import (
    backfill_subtitle_transcription_tasks,
    claim_next_subtitle_transcription_task,
    enqueue_subtitle_transcription_tasks,
    process_subtitle_transcription_task,
)
from app.transcription.base import (
    PreparedAudioChunk,
    SubtitleTranscriptionResult,
    SubtitleTranscriptionSegment,
)
from app.uploader.base import ObjectStorageResult
from tests.utils.utils import random_bvid


class RoundTripObjectStorageClient:
    def __init__(self, *, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.uploaded: list[tuple[str, str]] = []

    def seed_object(self, *, bucket: str, key: str, content: bytes) -> None:
        target_path = self.root_dir / bucket / key
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)

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
        target_path = self.root_dir / bucket / key
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target_path)
        self.uploaded.append((bucket, key))
        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=target_path.stat().st_size,
            etag=f"etag-{target_path.stat().st_size}",
            content_type=content_type,
        )

    def download_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
    ) -> ObjectStorageResult:
        source_path = self.root_dir / bucket / key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, local_path)
        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=source_path.stat().st_size,
            etag=f"etag-{source_path.stat().st_size}",
            content_type="video/mp4",
        )

    def delete_object(self, *, bucket: str, key: str) -> None:
        (self.root_dir / bucket / key).unlink(missing_ok=True)


class FakeAudioPreparer:
    def prepare_chunks(
        self,
        *,
        input_path: Path,
        output_dir: Path,
    ) -> list[PreparedAudioChunk]:
        del input_path
        output_dir.mkdir(parents=True, exist_ok=True)
        chunk_path = output_dir / "chunk-000.m4a"
        chunk_path.write_bytes(b"audio")
        return [PreparedAudioChunk(path=chunk_path, start_seconds=0.0, duration_seconds=4.0)]


class FakeTranscriber:
    def __init__(
        self,
        *,
        model: str = "whisper-1",
        supports_prompt: bool = False,
    ) -> None:
        self._model = model
        self._supports_prompt = supports_prompt
        self.prompts: list[str | None] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_prompt(self) -> bool:
        return self._supports_prompt

    def transcribe(
        self,
        *,
        input_path: Path,
        language: str | None = None,
        prompt: str | None = None,
    ) -> SubtitleTranscriptionResult:
        del input_path, language
        self.prompts.append(prompt)
        return SubtitleTranscriptionResult(
            text="第一句 第二句",
            language="zh",
            segments=[
                SubtitleTranscriptionSegment(
                    start_seconds=0.0,
                    end_seconds=1.5,
                    text="第一句",
                ),
                SubtitleTranscriptionSegment(
                    start_seconds=1.5,
                    end_seconds=3.0,
                    text="第二句",
                ),
            ],
            raw={
                "text": "第一句 第二句",
                "language": "zh",
                "segments": [
                    {"start": 0.0, "end": 1.5, "text": "第一句"},
                    {"start": 1.5, "end": 3.0, "text": "第二句"},
                ],
            },
            usage={"type": "duration", "seconds": 4},
        )


def create_video(db: Session, *, bvid: str) -> Video:
    uploader = db.get(Uploader, 84)
    if uploader is None:
        uploader = Uploader(mid=84, name="Uploader 84", raw={"source": "test"})
        db.add(uploader)
        db.commit()

    video = Video(
        bvid=bvid,
        aid=int.from_bytes(bvid.encode("utf-8"), "little") % 2_000_000_000 + 1,
        title=f"字幕测试 {bvid}",
        duration_seconds=240,
        owner_mid=84,
        owner_name="Uploader 84",
        raw={"source": "test"},
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def create_ingest_job(
    db: Session,
    *,
    bvid: str,
    transcribe_subtitles: bool = True,
    force_refresh: bool = False,
) -> IngestJob:
    job = IngestJob(
        input_text=f"https://www.bilibili.com/video/{bvid}",
        normalized_bvid=bvid,
        requested_by="tester@example.com",
        status="source_uploaded",
        phase="source media uploaded; ready for processing worker",
        options={
            "download_video": True,
            "transcribe_subtitles": transcribe_subtitles,
            "force_refresh": force_refresh,
        },
        progress={"next_step": "media_processing_worker"},
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def create_uploaded_asset(
    db: Session,
    *,
    job_id: uuid.UUID,
    bvid: str,
    cid: int | None,
    asset_type: str,
    filename: str,
    bucket: str = "bili-media-dev",
    key: str | None = None,
) -> MediaAsset:
    asset = MediaAsset(
        bvid=bvid,
        cid=cid,
        job_id=job_id,
        asset_type=asset_type,
        status="uploaded",
        s3_bucket=bucket,
        s3_key=key or f"media/source/bvid={bvid}/cid={cid or 'unknown'}/{filename}",
        filename=filename,
        content_type="video/mp4" if filename.endswith(".mp4") else "audio/mp4",
        metadata_json={},
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def create_ready_subtitle_asset(
    db: Session,
    *,
    job_id: uuid.UUID,
    bvid: str,
    cid: int | None,
    source_asset_id: uuid.UUID,
) -> MediaAsset:
    asset = MediaAsset(
        bvid=bvid,
        cid=cid,
        job_id=job_id,
        asset_type="subtitle",
        variant="openai-stt",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/subtitle/bvid={bvid}/cid={cid or 'unknown'}/previous.json",
        filename="previous.openai-stt.json",
        content_type="application/json",
        metadata_json={
            "transcription_source_asset_id": str(source_asset_id),
            "transcription_model": "gpt-4o-transcribe-diarize",
        },
        ready_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset


def test_enqueue_subtitle_transcription_tasks_prefers_audio_stream(
    db: Session,
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_ingest_job(db, bvid=bvid)
    video_asset = create_uploaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=202,
        asset_type="source_video_stream",
        filename="lesson.mp4",
    )
    audio_asset = create_uploaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=202,
        asset_type="source_audio_stream",
        filename="lesson.m4a",
    )

    queued_assets = enqueue_subtitle_transcription_tasks(
        db,
        job=job,
        source_assets=[video_asset, audio_asset],
    )

    assert len(queued_assets) == 1
    queued_asset = queued_assets[0]
    assert queued_asset.asset_type == "subtitle"
    assert queued_asset.variant == "openai-stt"
    assert queued_asset.metadata_json["transcription_source_asset_id"] == str(audio_asset.id)
    assert queued_asset.metadata_json["transcription_source_asset_ids"] == [
        str(video_asset.id),
        str(audio_asset.id),
    ]


def test_enqueue_subtitle_transcription_tasks_force_refresh_replaces_ready_asset(
    db: Session,
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_ingest_job(
        db,
        bvid=bvid,
        force_refresh=True,
    )
    source_asset = create_uploaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=212,
        asset_type="source_archive",
        filename="lesson.mp4",
    )
    previous_subtitle_asset = create_ready_subtitle_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=212,
        source_asset_id=source_asset.id,
    )

    queued_assets = enqueue_subtitle_transcription_tasks(
        db,
        job=job,
        source_assets=[source_asset],
        replace_existing_ready=bool(job.options.get("force_refresh")),
    )

    assert len(queued_assets) == 1
    queued_asset = queued_assets[0]
    assert queued_asset.id != previous_subtitle_asset.id
    assert queued_asset.metadata_json["replaces_subtitle_asset_id"] == str(
        previous_subtitle_asset.id
    )


def test_process_subtitle_transcription_task_uploads_json_and_replaces_subtitle_row(
    db: Session,
    tmp_path: Path,
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_ingest_job(db, bvid=bvid)
    source_asset = create_uploaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=303,
        asset_type="source_archive",
        filename="episode.mp4",
    )
    queued_assets = enqueue_subtitle_transcription_tasks(
        db,
        job=job,
        source_assets=[source_asset],
    )
    assert len(queued_assets) == 1
    subtitle_asset = queued_assets[0]
    db.commit()

    db.add(
        VideoSubtitle(
            bvid=bvid,
            cid=303,
            lang="zh",
            source="openai_stt",
            content="old subtitle",
            raw={"stale": True},
            asset_id=subtitle_asset.id,
        )
    )
    db.commit()

    storage_client = RoundTripObjectStorageClient(root_dir=tmp_path / "remote")
    storage_client.seed_object(
        bucket=source_asset.s3_bucket or "bili-media-dev",
        key=source_asset.s3_key or "source.mp4",
        content=b"video",
    )

    processed_asset = process_subtitle_transcription_task(
        session=db,
        asset_id=subtitle_asset.id,
        storage_client=storage_client,
        audio_preparer=FakeAudioPreparer(),
        transcriber=FakeTranscriber(),
    )

    assert processed_asset.status == "ready"
    assert processed_asset.content_type == "application/json"
    assert processed_asset.metadata_json["subtitle_segment_count"] == 2
    assert processed_asset.metadata_json["subtitle_language"] == "zh"

    subtitles = list(
        db.exec(
            select(VideoSubtitle).where(
                VideoSubtitle.bvid == bvid,
                VideoSubtitle.cid == 303,
                VideoSubtitle.source == "openai_stt",
            )
        ).all()
    )
    assert len(subtitles) == 1
    subtitle = subtitles[0]
    assert subtitle.asset_id == processed_asset.id
    assert "00:00:00,000 --> 00:00:01,500" in (subtitle.content or "")
    assert "第一句" in (subtitle.content or "")

    uploaded_json_path = (
        tmp_path
        / "remote"
        / (processed_asset.s3_bucket or "bili-media-dev")
        / (processed_asset.s3_key or "subtitle.json")
    )
    assert uploaded_json_path.is_file()
    payload = json.loads(uploaded_json_path.read_text(encoding="utf-8"))
    assert payload["model"] == "whisper-1"
    assert payload["language"] == "zh"
    assert payload["temperature"] == 0.0
    assert payload["audio"]["format"] == "m4a"
    assert len(payload["segments"]) == 2


def test_claim_next_subtitle_transcription_task_reclaims_stale_asset(
    db: Session,
) -> None:
    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_ingest_job(db, bvid=bvid)
    source_asset = create_uploaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=404,
        asset_type="source_archive",
        filename="source.mp4",
    )
    queued_assets = enqueue_subtitle_transcription_tasks(
        db,
        job=job,
        source_assets=[source_asset],
    )
    assert len(queued_assets) == 1

    subtitle_asset = db.get(MediaAsset, queued_assets[0].id)
    assert subtitle_asset is not None
    for other_asset in db.exec(
        select(MediaAsset).where(MediaAsset.asset_type == "subtitle")
    ).all():
        if other_asset.id != subtitle_asset.id:
            db.delete(other_asset)
    subtitle_asset.status = "processing"
    subtitle_asset.metadata_json = {
        **subtitle_asset.metadata_json,
        "last_transition_at": (
            datetime.now(timezone.utc) - timedelta(hours=5)
        ).isoformat(),
    }
    db.add(subtitle_asset)
    db.commit()

    claimed_asset = claim_next_subtitle_transcription_task(
        db,
        stale_after_seconds=60.0,
    )

    assert claimed_asset is not None
    assert claimed_asset.id == subtitle_asset.id
    assert claimed_asset.status == "pending"
    assert claimed_asset.metadata_json["reclaim"]["count"] == 1


def test_backfill_subtitle_transcription_tasks_groups_by_bvid_and_records_audit_events(
    db: Session,
) -> None:
    first_bvid = random_bvid()
    second_bvid = random_bvid()
    shared_cid = 505
    create_video(db, bvid=first_bvid)
    create_video(db, bvid=second_bvid)
    first_job = create_ingest_job(
        db,
        bvid=first_bvid,
        transcribe_subtitles=False,
    )
    second_job = create_ingest_job(
        db,
        bvid=second_bvid,
        transcribe_subtitles=False,
    )
    first_source_asset = create_uploaded_asset(
        db,
        job_id=first_job.id,
        bvid=first_bvid,
        cid=shared_cid,
        asset_type="source_archive",
        filename="first.mp4",
    )
    second_source_asset = create_uploaded_asset(
        db,
        job_id=second_job.id,
        bvid=second_bvid,
        cid=shared_cid,
        asset_type="source_archive",
        filename="second.mp4",
    )

    queued_assets = backfill_subtitle_transcription_tasks(
        db,
        cid=shared_cid,
    )
    db.commit()

    assert len(queued_assets) == 2
    queued_assets_by_bvid = {asset.bvid: asset for asset in queued_assets}
    assert set(queued_assets_by_bvid) == {first_bvid, second_bvid}
    assert queued_assets_by_bvid[first_bvid].job_id == first_job.id
    assert queued_assets_by_bvid[second_bvid].job_id == second_job.id
    assert queued_assets_by_bvid[first_bvid].metadata_json["transcription_source_asset_ids"] == [
        str(first_source_asset.id)
    ]
    assert queued_assets_by_bvid[second_bvid].metadata_json["transcription_source_asset_ids"] == [
        str(second_source_asset.id)
    ]

    audit_events = list(
        db.exec(
            select(AuditEvent).where(
                AuditEvent.action == "subtitle_transcription.backfill_enqueued"
            )
        ).all()
    )
    assert len(audit_events) == 2


def test_backfill_subtitle_transcription_tasks_respects_limit_and_skips_existing_assets(
    db: Session,
) -> None:
    first_bvid = random_bvid()
    second_bvid = random_bvid()
    create_video(db, bvid=first_bvid)
    create_video(db, bvid=second_bvid)
    first_job = create_ingest_job(
        db,
        bvid=first_bvid,
        transcribe_subtitles=False,
    )
    second_job = create_ingest_job(
        db,
        bvid=second_bvid,
        transcribe_subtitles=False,
    )
    shared_cid = 606
    first_source_asset = create_uploaded_asset(
        db,
        job_id=first_job.id,
        bvid=first_bvid,
        cid=shared_cid,
        asset_type="source_archive",
        filename="first.mp4",
    )
    second_source_asset = create_uploaded_asset(
        db,
        job_id=second_job.id,
        bvid=second_bvid,
        cid=shared_cid,
        asset_type="source_archive",
        filename="second.mp4",
    )

    first_job.options = {
        **first_job.options,
        "transcribe_subtitles": True,
    }
    db.add(first_job)
    db.commit()

    existing_subtitle_assets = enqueue_subtitle_transcription_tasks(
        db,
        job=first_job,
        source_assets=[first_source_asset],
    )
    assert len(existing_subtitle_assets) == 1
    db.commit()

    queued_assets = backfill_subtitle_transcription_tasks(
        db,
        cid=shared_cid,
        limit=1,
    )
    db.commit()

    assert len(queued_assets) == 1
    assert queued_assets[0].bvid == second_bvid
    assert queued_assets[0].metadata_json["transcription_source_asset_id"] == str(
        second_source_asset.id
    )


def test_backfill_subtitle_transcription_tasks_can_replace_ready_asset(
    db: Session,
) -> None:
    bvid = random_bvid()
    shared_cid = 808
    create_video(db, bvid=bvid)
    job = create_ingest_job(
        db,
        bvid=bvid,
        transcribe_subtitles=False,
    )
    source_asset = create_uploaded_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=shared_cid,
        asset_type="source_archive",
        filename="episode.mp4",
    )
    previous_subtitle_asset = create_ready_subtitle_asset(
        db,
        job_id=job.id,
        bvid=bvid,
        cid=shared_cid,
        source_asset_id=source_asset.id,
    )

    queued_assets = backfill_subtitle_transcription_tasks(
        db,
        bvid=bvid,
        cid=shared_cid,
        replace_existing_ready=True,
    )
    db.commit()

    assert len(queued_assets) == 1
    assert queued_assets[0].metadata_json["replaces_subtitle_asset_id"] == str(
        previous_subtitle_asset.id
    )

    audit_event = db.exec(
        select(AuditEvent).where(
            AuditEvent.action == "subtitle_transcription.backfill_enqueued",
            AuditEvent.resource_id == str(queued_assets[0].id),
        )
    ).first()
    assert audit_event is not None
    assert audit_event.payload["replaces_subtitle_asset_id"] == str(previous_subtitle_asset.id)

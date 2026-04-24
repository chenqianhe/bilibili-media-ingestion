from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.downloader.base import (
    DownloaderExecutionError,
    DownloadPlan,
    DownloadResult,
)
from app.ingest_models import (
    IngestJob,
    MediaAsset,
    Uploader,
    Video,
)
from app.services.download_ingest import process_download_ingest_job
from app.workers.download_ingest import process_next_download_ingest_job
from tests.utils.utils import random_bvid


class StaticDownloadAdapter:
    def __init__(
        self,
        *,
        bvid: str,
        files: list[tuple[str, bytes]] | None = None,
    ) -> None:
        self.bvid = bvid
        self.files = files or [("source.mp4", b"fake-video-binary")]

    def extract_info(self, input_url: str) -> DownloadPlan:
        return DownloadPlan(
            bvid=self.bvid,
            webpage_url=input_url,
            title="Downloaded source",
        )

    def download(self, plan: DownloadPlan, output_dir: str) -> DownloadResult:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        local_files: list[str] = []
        for filename, content in self.files:
            path = output_path / filename
            path.write_bytes(content)
            local_files.append(str(path))

        info_json_path = output_path / "source.info.json"
        info_json_path.write_text('{"extractor":"test"}', encoding="utf-8")

        return DownloadResult(
            bvid=plan.bvid,
            cid=plan.cid,
            local_files=local_files,
            info_json_path=str(info_json_path),
            title=plan.title,
        )


class FailingDownloadAdapter(StaticDownloadAdapter):
    def download(self, plan: DownloadPlan, output_dir: str) -> DownloadResult:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        (output_path / "partial.mp4").write_bytes(b"partial")
        raise DownloaderExecutionError("downloader failed after writing partial output")


class DispatchingDownloadAdapter:
    def __init__(self, payloads: dict[str, list[tuple[str, bytes]]]) -> None:
        self.payloads = payloads

    def extract_info(self, input_url: str) -> DownloadPlan:
        bvid = input_url.rstrip("/").split("/")[-1]
        return DownloadPlan(bvid=bvid, webpage_url=input_url, title=bvid)

    def download(self, plan: DownloadPlan, output_dir: str) -> DownloadResult:
        files = self.payloads[plan.bvid]
        return StaticDownloadAdapter(bvid=plan.bvid, files=files).download(
            plan, output_dir
        )


class RecordingDownloadAdapter(StaticDownloadAdapter):
    def __init__(self, *, bvid: str) -> None:
        super().__init__(bvid=bvid)
        self.plans: list[DownloadPlan] = []

    def download(self, plan: DownloadPlan, output_dir: str) -> DownloadResult:
        self.plans.append(plan.model_copy(deep=True))
        return super().download(plan, output_dir)


def create_video(db: Session, *, bvid: str, owner_mid: int = 42) -> Video:
    uploader = db.get(Uploader, owner_mid)
    if uploader is None:
        uploader = Uploader(
            mid=owner_mid,
            name=f"Uploader {owner_mid}",
            raw={"source": "test"},
        )
        db.add(uploader)
        db.commit()

    video = Video(
        bvid=bvid,
        aid=int.from_bytes(bvid.encode("utf-8"), "little") % 2_000_000_000 + 1,
        title=f"Video {bvid}",
        duration_seconds=330,
        owner_mid=owner_mid,
        owner_name=f"Uploader {owner_mid}",
        raw={"source": "test"},
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video

def create_download_ready_job(
    db: Session,
    *,
    bvid: str,
    priority: int = 100,
    download_video: bool = True,
    max_height: int | None = 720,
) -> IngestJob:
    job = IngestJob(
        input_text=f"https://www.bilibili.com/video/{bvid}",
        normalized_bvid=bvid,
        requested_by="tester@example.com",
        status="metadata_ready",
        phase="metadata stored; ready for download worker",
        priority=priority,
        options={
            "download_video": download_video,
            "max_height": max_height,
            "store_source_archive": True,
        },
        progress={"next_step": "downloader_worker"},
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        finished_at=None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_process_download_ingest_job_persists_media_assets(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "app.services.download_ingest.settings.S3_BUCKET",
        "bili-media-dev",
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_download_ready_job(db, bvid=bvid)

    processed_job = process_download_ingest_job(
        session=db,
        job_id=job.id,
        adapter=StaticDownloadAdapter(bvid=bvid),
    )

    assert processed_job.status == "source_downloaded"
    assert processed_job.phase == "source media downloaded; ready for upload worker"
    assert processed_job.progress["next_step"] == "upload_worker"

    assets = list(
        db.exec(select(MediaAsset).where(MediaAsset.job_id == job.id)).all()
    )
    assert len(assets) == 1
    asset = assets[0]
    assert asset.asset_type == "source_archive"
    assert asset.status == "downloaded"
    assert asset.s3_bucket == "bili-media-dev"
    assert asset.s3_key is not None
    assert asset.s3_key.startswith(f"media/source/bvid={bvid}/")
    assert asset.sha256 is not None
    assert asset.size_bytes == len(b"fake-video-binary")
    assert Path(asset.metadata_json["local_path"]).is_file()


def test_process_download_ingest_job_defaults_to_best_available_quality(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_download_ready_job(db, bvid=bvid, max_height=None)
    adapter = RecordingDownloadAdapter(bvid=bvid)

    processed_job = process_download_ingest_job(
        session=db,
        job_id=job.id,
        adapter=adapter,
    )

    assert processed_job.status == "source_downloaded"
    assert len(adapter.plans) == 1
    assert adapter.plans[0].max_height is None
    assert adapter.plans[0].format_selector == "bv*+ba/best"

    asset = db.exec(select(MediaAsset).where(MediaAsset.job_id == job.id)).one()
    assert asset.metadata_json["requested_max_height"] is None
    assert asset.metadata_json["format_selector"] == "bv*+ba/best"


def test_process_download_ingest_job_applies_requested_height_cap(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_download_ready_job(db, bvid=bvid, max_height=720)
    adapter = RecordingDownloadAdapter(bvid=bvid)

    processed_job = process_download_ingest_job(
        session=db,
        job_id=job.id,
        adapter=adapter,
    )

    assert processed_job.status == "source_downloaded"
    assert len(adapter.plans) == 1
    assert adapter.plans[0].max_height == 720
    assert adapter.plans[0].format_selector == "bv*[height<=720]+ba/b[height<=720]/best"


def test_process_download_ingest_job_splits_video_and_audio_assets(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_download_ready_job(db, bvid=bvid)

    processed_job = process_download_ingest_job(
        session=db,
        job_id=job.id,
        adapter=StaticDownloadAdapter(
            bvid=bvid,
            files=[
                ("video.mp4", b"video-track"),
                ("audio.m4a", b"audio-track"),
            ],
        ),
    )

    assert processed_job.status == "source_downloaded"
    assets = list(
        db.exec(select(MediaAsset).where(MediaAsset.job_id == job.id)).all()
    )
    assert {asset.asset_type for asset in assets} == {
        "source_video_stream",
        "source_audio_stream",
    }


def test_process_download_ingest_job_rolls_back_and_cleans_workspace_on_failure(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    bvid = random_bvid()
    create_video(db, bvid=bvid)
    job = create_download_ready_job(db, bvid=bvid)

    processed_job = process_download_ingest_job(
        session=db,
        job_id=job.id,
        adapter=FailingDownloadAdapter(bvid=bvid),
    )

    assert processed_job.status == "failed"
    assert processed_job.error_code == "source_download_failed"
    assert processed_job.retry_count == 1
    assert not (tmp_path / "jobs" / str(job.id) / "source").exists()
    assets = list(
        db.exec(select(MediaAsset).where(MediaAsset.job_id == job.id)).all()
    )
    assert assets == []


def test_process_next_download_ingest_job_uses_priority_order(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    lower_priority_bvid = random_bvid()
    higher_priority_bvid = random_bvid()
    create_video(db, bvid=lower_priority_bvid)
    create_video(db, bvid=higher_priority_bvid)
    lower_priority_job = create_download_ready_job(
        db, bvid=lower_priority_bvid, priority=-20_002
    )
    higher_priority_job = create_download_ready_job(
        db, bvid=higher_priority_bvid, priority=-20_003
    )

    adapter = DispatchingDownloadAdapter(
        {
            lower_priority_bvid: [("lower.mp4", b"lower")],
            higher_priority_bvid: [("higher.mp4", b"higher")],
        }
    )
    processed_job = process_next_download_ingest_job(session=db, adapter=adapter)

    assert processed_job is not None
    assert processed_job.id == higher_priority_job.id
    assert processed_job.status == "source_downloaded"

    second_processed_job = process_next_download_ingest_job(session=db, adapter=adapter)
    assert second_processed_job is not None
    assert second_processed_job.id == lower_priority_job.id
    assert second_processed_job.status == "source_downloaded"


def test_process_next_download_ingest_job_skips_jobs_without_download_requested(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    metadata_only_bvid = random_bvid()
    download_bvid = random_bvid()
    create_video(db, bvid=metadata_only_bvid)
    create_video(db, bvid=download_bvid)

    metadata_only_job = create_download_ready_job(
        db,
        bvid=metadata_only_bvid,
        priority=-20_001,
        download_video=False,
    )
    downloadable_job = create_download_ready_job(
        db,
        bvid=download_bvid,
        priority=-20_000,
        download_video=True,
    )

    processed_job = process_next_download_ingest_job(
        session=db,
        adapter=StaticDownloadAdapter(bvid=download_bvid),
    )

    assert processed_job is not None
    assert processed_job.id == downloadable_job.id
    assert processed_job.status == "source_downloaded"

    untouched_job = db.get(IngestJob, metadata_only_job.id)
    assert untouched_job is not None
    assert untouched_job.status == "metadata_ready"


def test_process_next_download_ingest_job_reclaims_stale_downloading_job(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    stale_bvid = random_bvid()
    create_video(db, bvid=stale_bvid)
    stale_job = create_download_ready_job(db, bvid=stale_bvid)
    stale_transition_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    stale_job.priority = -20_000
    stale_job.status = "downloading"
    stale_job.phase = "downloading source media"
    stale_job.progress = {
        "current_step": "source_downloading",
        "last_transition_at": stale_transition_at.isoformat(),
        "next_step": "downloader_worker",
    }
    db.add(stale_job)
    db.commit()
    db.refresh(stale_job)

    processed_job = process_next_download_ingest_job(
        session=db,
        adapter=StaticDownloadAdapter(bvid=stale_bvid),
    )

    assert processed_job is not None
    assert processed_job.id == stale_job.id
    assert processed_job.status == "source_downloaded"
    assert processed_job.progress["reclaim"]["count"] == 1
    assert processed_job.progress["reclaim"]["previous_status"] == "downloading"


def test_process_next_download_ingest_job_skips_fresh_downloading_jobs(
    db: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "app.services.download_ingest.settings.INGEST_TMP_DIR",
        str(tmp_path),
    )

    fresh_bvid = random_bvid()
    queued_bvid = random_bvid()
    create_video(db, bvid=fresh_bvid)
    create_video(db, bvid=queued_bvid)

    fresh_job = create_download_ready_job(db, bvid=fresh_bvid, priority=1)
    fresh_transition_at = datetime.now(timezone.utc)
    fresh_job.priority = -20_000
    fresh_job.status = "downloading"
    fresh_job.phase = "downloading source media"
    fresh_job.progress = {
        "current_step": "source_downloading",
        "last_transition_at": fresh_transition_at.isoformat(),
        "next_step": "downloader_worker",
    }
    db.add(fresh_job)

    queued_job = create_download_ready_job(db, bvid=queued_bvid, priority=-19_999)
    db.add(queued_job)
    db.commit()
    db.refresh(fresh_job)
    db.refresh(queued_job)

    processed_job = process_next_download_ingest_job(
        session=db,
        adapter=StaticDownloadAdapter(bvid=queued_bvid),
    )

    assert processed_job is not None
    assert processed_job.id == queued_job.id
    assert processed_job.status == "source_downloaded"
    skipped_job = db.get(IngestJob, fresh_job.id)
    assert skipped_job is not None
    assert skipped_job.status == "downloading"
    assert "reclaim" not in skipped_job.progress

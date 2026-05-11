from __future__ import annotations

import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import settings
from app.crawler.bilibili_auxiliary import (
    BilibiliAuxiliaryError,
    BilibiliAuxiliaryProvider,
)
from app.crawler.bilibili_metadata import (
    BilibiliMetadataError,
    BilibiliMetadataProvider,
    BilibiliUploaderMetadata,
    BilibiliVideoMetadata,
)
from app.crawler.bilibili_web import BilibiliWebClient
from app.ingest_models import IngestJob, Uploader, Video, VideoPage, VideoStatSnapshot
from app.services.audit import record_audit_event
from app.services.auxiliary_ingest import (
    fetch_requested_auxiliary_data,
    requested_auxiliary_flags,
)
from app.services.image_asset_ingest import (
    store_remote_image_asset,
    strip_url_fields,
)
from app.services.postgres_sanitize import (
    sanitize_postgres_json,
    sanitize_postgres_text,
)
from app.uploader.base import ObjectStorageClient


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _merge_progress(job: IngestJob, *, payload: dict[str, object]) -> None:
    progress = dict(job.progress)
    progress.update(payload)
    job.progress = sanitize_postgres_json(progress)


def _upsert_uploader(
    session: Session,
    *,
    metadata: BilibiliUploaderMetadata | None,
) -> Uploader | None:
    if metadata is None:
        return None

    uploader = session.get(Uploader, metadata.mid)
    if uploader is None:
        uploader = Uploader(mid=metadata.mid)

    uploader.name = sanitize_postgres_text(metadata.name)
    uploader.avatar_url = None
    uploader.avatar_s3_key = None
    if metadata.raw:
        uploader.raw = sanitize_postgres_json(strip_url_fields(metadata.raw))
    uploader.last_seen_at = _now_utc()
    session.add(uploader)
    return uploader


def _upsert_video(
    session: Session,
    *,
    metadata: BilibiliVideoMetadata,
    uploader: Uploader | None,
    crawled_at: datetime,
) -> Video:
    video = session.get(Video, metadata.bvid)
    if video is None:
        video = Video(bvid=metadata.bvid, title=sanitize_postgres_text(metadata.title))

    video.aid = metadata.aid
    video.title = sanitize_postgres_text(metadata.title)
    video.description = sanitize_postgres_text(metadata.description)
    video.duration_seconds = metadata.duration_seconds
    video.pubdate = metadata.pubdate
    video.owner_mid = uploader.mid if uploader else None
    video.owner_name = sanitize_postgres_text(uploader.name) if uploader else None
    video.cover_url = None
    video.cover_s3_key = None
    video.category = sanitize_postgres_text(metadata.category)
    video.tags = list(
        dict.fromkeys(sanitize_postgres_text(tag) for tag in metadata.tags)
    )
    video.stat = sanitize_postgres_json(metadata.stat.as_dict())
    if metadata.raw:
        video.raw = sanitize_postgres_json(strip_url_fields(metadata.raw))
    video.last_crawled_at = crawled_at
    session.add(video)
    return video


def _sync_video_image_assets(
    session: Session,
    *,
    job: IngestJob,
    video: Video,
    metadata: BilibiliVideoMetadata,
    uploader: Uploader | None,
    comment_image_web_client: BilibiliWebClient | None = None,
    comment_image_storage_client: ObjectStorageClient | None = None,
    image_temp_dir: Path | None = None,
) -> None:
    if (
        comment_image_web_client is None
        or comment_image_storage_client is None
        or image_temp_dir is None
    ):
        return

    if metadata.cover_url:
        cover_asset = store_remote_image_asset(
            session,
            job=job,
            bvid=metadata.bvid,
            asset_type="cover",
            source_url=metadata.cover_url,
            web_client=comment_image_web_client,
            storage_client=comment_image_storage_client,
            temp_dir=image_temp_dir / "cover",
            referer=f"https://www.bilibili.com/video/{metadata.bvid}/",
            cid=None,
            variant="video-cover",
            source_type="bilibili_video_cover",
            fallback_stem="cover",
            metadata_json={"raw_video": metadata.raw},
            upload_metadata={"bvid": metadata.bvid},
        )
        video.cover_asset_id = cover_asset.id

    if (
        uploader is not None
        and metadata.owner is not None
        and metadata.owner.avatar_url
    ):
        avatar_asset = store_remote_image_asset(
            session,
            job=job,
            bvid=metadata.bvid,
            asset_type="avatar",
            source_url=metadata.owner.avatar_url,
            web_client=comment_image_web_client,
            storage_client=comment_image_storage_client,
            temp_dir=image_temp_dir / f"uploader-mid-{metadata.owner.mid}",
            referer=f"https://www.bilibili.com/video/{metadata.bvid}/",
            cid=None,
            variant=f"owner-mid={metadata.owner.mid}",
            source_type="bilibili_uploader_avatar",
            fallback_stem="avatar",
            metadata_json={
                "owner_mid": metadata.owner.mid,
                "raw_uploader": metadata.owner.raw,
            },
            upload_metadata={"owner_mid": str(metadata.owner.mid)},
        )
        uploader.avatar_asset_id = avatar_asset.id

    session.add(video)
    if uploader is not None:
        session.add(uploader)


def _sync_video_pages(
    session: Session, *, metadata: BilibiliVideoMetadata, crawled_at: datetime
) -> None:
    del crawled_at
    existing_pages = list(
        session.exec(select(VideoPage).where(VideoPage.bvid == metadata.bvid)).all()
    )
    existing_by_cid = {page.cid: page for page in existing_pages}
    seen_cids: set[int] = set()

    for page_metadata in metadata.pages:
        page = existing_by_cid.get(page_metadata.cid)
        if page is None:
            page = VideoPage(
                bvid=metadata.bvid,
                cid=page_metadata.cid,
                page_no=page_metadata.page_no,
            )
        page.aid = metadata.aid
        page.page_no = page_metadata.page_no
        page.part_title = sanitize_postgres_text(page_metadata.part_title)
        page.duration_seconds = page_metadata.duration_seconds
        page.raw = sanitize_postgres_json(page_metadata.raw)
        session.add(page)
        seen_cids.add(page_metadata.cid)

    for page in existing_pages:
        if page.cid not in seen_cids:
            session.delete(page)


def _record_stat_snapshot(
    session: Session, *, metadata: BilibiliVideoMetadata, crawled_at: datetime
) -> None:
    stat_payload = metadata.stat.as_dict()
    if not stat_payload:
        return

    snapshot = VideoStatSnapshot(
        bvid=metadata.bvid,
        view_count=metadata.stat.view_count,
        like_count=metadata.stat.like_count,
        coin_count=metadata.stat.coin_count,
        favorite_count=metadata.stat.favorite_count,
        reply_count=metadata.stat.reply_count,
        share_count=metadata.stat.share_count,
        danmaku_count=metadata.stat.danmaku_count,
        crawled_at=crawled_at,
    )
    session.add(snapshot)


def _start_metadata_fetch(
    session: Session,
    *,
    job: IngestJob,
    started_at: datetime,
) -> None:
    job.started_at = job.started_at or started_at
    job.finished_at = None
    job.status = "metadata_fetching"
    job.phase = "fetching video metadata"
    job.error_code = None
    job.error_message = None
    _merge_progress(
        job,
        payload={
            "current_step": "metadata_fetching",
            "last_transition_at": started_at.isoformat(),
        },
    )
    session.add(job)


def _complete_metadata_fetch(
    session: Session,
    *,
    job: IngestJob,
    metadata: BilibiliVideoMetadata,
    uploader: Uploader | None,
    crawled_at: datetime,
    auxiliary_summary: dict[str, object] | None = None,
) -> None:
    owner_mid = uploader.mid if uploader else None
    download_requested = bool(job.options.get("download_video"))

    next_step = "job_complete"
    if download_requested:
        job.status = "metadata_ready"
        job.phase = "metadata stored; ready for download worker"
        job.finished_at = None
        next_step = "downloader_worker"
    else:
        job.status = "metadata_ready"
        job.phase = "metadata stored; video download not requested"
        job.finished_at = crawled_at

    _merge_progress(
        job,
        payload={
            "current_step": "metadata_ready",
            "last_transition_at": crawled_at.isoformat(),
            "next_step": next_step,
            "metadata": {
                "title": metadata.title,
                "page_count": len(metadata.pages),
                "owner_mid": owner_mid,
                "last_crawled_at": crawled_at.isoformat(),
            },
            **({"auxiliary": auxiliary_summary} if auxiliary_summary else {}),
        },
    )
    session.add(job)


def _fail_metadata_fetch(
    session: Session,
    *,
    job: IngestJob,
    error_code: str,
    message: str,
    failed_at: datetime,
) -> None:
    job.status = "failed"
    job.phase = "metadata fetch failed"
    job.error_code = error_code
    job.error_message = sanitize_postgres_text(message)
    job.finished_at = failed_at
    job.retry_count += 1
    _merge_progress(
        job,
        payload={
            "current_step": "metadata_fetch_failed",
            "last_transition_at": failed_at.isoformat(),
        },
    )
    session.add(job)


def process_metadata_ingest_job(
    *,
    session: Session,
    job_id: uuid.UUID,
    provider: BilibiliMetadataProvider,
    auxiliary_provider: BilibiliAuxiliaryProvider | None = None,
    comment_image_web_client: BilibiliWebClient | None = None,
    comment_image_storage_client: ObjectStorageClient | None = None,
) -> IngestJob:
    job = session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Ingest job {job_id} not found")
    if not job.normalized_bvid:
        raise ValueError(f"Ingest job {job_id} has no normalized BVID")
    image_temp_dir = (
        Path(settings.INGEST_TMP_DIR) / "jobs" / str(job.id) / "metadata-images"
    )

    started_at = _now_utc()
    _start_metadata_fetch(session, job=job, started_at=started_at)
    record_audit_event(
        session=session,
        actor=job.requested_by,
        action="ingest_job.metadata_started",
        resource_type="ingest_job",
        resource_id=str(job.id),
        message="Started metadata ingestion",
        payload={"bvid": job.normalized_bvid},
    )
    session.commit()
    session.refresh(job)

    try:
        try:
            metadata = provider.fetch_video_metadata(bvid=job.normalized_bvid)
            if metadata.bvid != job.normalized_bvid:
                raise ValueError(
                    "Metadata provider returned a different BVID than the ingest job"
                )

            crawled_at = _now_utc()
            uploader = _upsert_uploader(
                session,
                metadata=metadata.owner,
            )
            video = _upsert_video(
                session,
                metadata=metadata,
                uploader=uploader,
                crawled_at=crawled_at,
            )
            session.flush()
            _sync_video_image_assets(
                session,
                job=job,
                video=video,
                metadata=metadata,
                uploader=uploader,
                comment_image_web_client=comment_image_web_client,
                comment_image_storage_client=comment_image_storage_client,
                image_temp_dir=image_temp_dir,
            )
            _sync_video_pages(session, metadata=metadata, crawled_at=crawled_at)
            _record_stat_snapshot(session, metadata=metadata, crawled_at=crawled_at)
            auxiliary_summary: dict[str, object] = {}
            if any(requested_auxiliary_flags(job).values()):
                if auxiliary_provider is None:
                    raise ValueError(
                        "Auxiliary provider is required when auxiliary ingest options are enabled"
                    )
                auxiliary_summary = fetch_requested_auxiliary_data(
                    session=session,
                    job=job,
                    metadata=metadata,
                    provider=auxiliary_provider,
                    crawled_at=crawled_at,
                    comment_image_web_client=comment_image_web_client,
                    comment_image_storage_client=comment_image_storage_client,
                )
            _complete_metadata_fetch(
                session,
                job=job,
                metadata=metadata,
                uploader=uploader,
                crawled_at=crawled_at,
                auxiliary_summary=auxiliary_summary,
            )
            record_audit_event(
                session=session,
                actor=job.requested_by,
                action="ingest_job.metadata_completed",
                resource_type="ingest_job",
                resource_id=str(job.id),
                message="Completed metadata ingestion",
                payload={
                    "bvid": metadata.bvid,
                    "page_count": len(metadata.pages),
                    "status": job.status,
                    **({"auxiliary": auxiliary_summary} if auxiliary_summary else {}),
                },
            )
        except Exception as exc:
            session.rollback()
            failed_job = session.get(IngestJob, job_id)
            if failed_job is None:
                raise
            failed_at = _now_utc()
            _fail_metadata_fetch(
                session,
                job=failed_job,
                error_code=(
                    exc.error_code
                    if isinstance(exc, (BilibiliMetadataError, BilibiliAuxiliaryError))
                    else "metadata_fetch_failed"
                ),
                message=str(exc),
                failed_at=failed_at,
            )
            record_audit_event(
                session=session,
                actor=failed_job.requested_by,
                action="ingest_job.metadata_failed",
                resource_type="ingest_job",
                resource_id=str(failed_job.id),
                message="Metadata ingestion failed",
                payload={"bvid": failed_job.normalized_bvid, "error": str(exc)},
            )
            job = failed_job

        session.commit()
        session.refresh(job)
        return job
    finally:
        if image_temp_dir.exists():
            shutil.rmtree(image_temp_dir, ignore_errors=True)

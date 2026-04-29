from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import settings
from app.crawler.bilibili_auxiliary import (
    BilibiliCommentImageMetadata,
    BilibiliCommentMetadata,
)
from app.crawler.bilibili_web import BilibiliWebClient, BilibiliWebError
from app.ingest_models import IngestJob, MediaAsset, VideoComment, VideoCommentImage
from app.services.image_asset_ingest import strip_url_fields, store_remote_image_asset
from app.uploader.base import ObjectStorageClient, ObjectStorageError

_COMMENT_IMAGE_ASSET_TYPE = "comment_image"


def _store_comment_image_asset(
    session: Session,
    *,
    job: IngestJob,
    bvid: str,
    comment_rpid: int,
    ordinal: int,
    image: BilibiliCommentImageMetadata,
    web_client: BilibiliWebClient,
    storage_client: ObjectStorageClient,
    temp_dir: Path,
) -> MediaAsset:
    return store_remote_image_asset(
        session,
        job=job,
        bvid=bvid,
        asset_type=_COMMENT_IMAGE_ASSET_TYPE,
        source_url=image.source_url,
        web_client=web_client,
        storage_client=storage_client,
        temp_dir=temp_dir / f"comment-{comment_rpid}-{ordinal}",
        referer=f"https://www.bilibili.com/video/{bvid}/",
        cid=None,
        variant=f"comment-rpid={comment_rpid}-ordinal={ordinal}",
        source_type="bilibili_comment_image",
        fallback_stem=f"comment-{comment_rpid}-{ordinal}",
        width=image.width,
        height=image.height,
        metadata_json={
            "comment_rpid": comment_rpid,
            "ordinal": ordinal,
            "raw_image": image.raw,
        },
        upload_metadata={
            "comment_rpid": str(comment_rpid),
            "ordinal": str(ordinal),
        },
    )


def _count_comment_image_statuses(
    session: Session,
    *,
    bvid: str,
) -> dict[str, int]:
    rows = list(
        session.exec(
            select(VideoCommentImage.storage_status, func.count())
            .where(VideoCommentImage.bvid == bvid)
            .group_by(VideoCommentImage.storage_status)
        ).all()
    )
    counts = {
        "total": 0,
        "ready": 0,
        "failed": 0,
        "skipped": 0,
    }
    for status, count in rows:
        normalized_status = str(status or "")
        resolved_count = int(count or 0)
        counts["total"] += resolved_count
        if normalized_status in {"ready", "failed", "skipped"}:
            counts[normalized_status] += resolved_count
    return counts


def merge_comment_images(
    session: Session,
    *,
    job: IngestJob,
    bvid: str,
    comments: list[BilibiliCommentMetadata],
    persisted_comments_by_rpid: dict[int, VideoComment],
    crawled_at: datetime,
    web_client: BilibiliWebClient | None = None,
    storage_client: ObjectStorageClient | None = None,
) -> dict[str, int]:
    fetched_count = 0
    temp_dir = Path(settings.INGEST_TMP_DIR) / "jobs" / str(job.id) / "comment-images"
    comment_ids = [
        persisted_comments_by_rpid[comment.rpid].id
        for comment in comments
        if comment.rpid in persisted_comments_by_rpid
    ]
    existing_images = list(
        session.exec(
            select(VideoCommentImage)
            .where(VideoCommentImage.comment_id.in_(comment_ids))
            .order_by(VideoCommentImage.comment_id.asc(), VideoCommentImage.ordinal.asc())
        ).all()
    ) if comment_ids else []
    existing_by_comment_id: dict[object, dict[int, VideoCommentImage]] = {}
    for existing_image in existing_images:
        existing_by_comment_id.setdefault(existing_image.comment_id, {})[
            existing_image.ordinal
        ] = existing_image

    try:
        for comment in comments:
            persisted_comment = persisted_comments_by_rpid.get(comment.rpid)
            if persisted_comment is None:
                raise ValueError(
                    f"Persisted comment row missing for bvid={bvid} rpid={comment.rpid}"
                )
            existing_by_ordinal = existing_by_comment_id.get(persisted_comment.id, {})
            for ordinal, image in enumerate(comment.images):
                fetched_count += 1
                existing_image = existing_by_ordinal.get(ordinal)
                asset: MediaAsset | None = None
                if (
                    existing_image is not None
                    and existing_image.storage_status == "ready"
                    and existing_image.asset_id is not None
                ):
                    storage_status = existing_image.storage_status
                    error_message = existing_image.error_message
                    asset_id = existing_image.asset_id
                else:
                    storage_status = (
                        existing_image.storage_status
                        if existing_image is not None
                        else "skipped"
                    )
                    error_message = (
                        existing_image.error_message
                        if existing_image is not None
                        else None
                    )
                    asset_id = (
                        existing_image.asset_id
                        if existing_image is not None
                        else None
                    )

                if (
                    storage_client is not None
                    and web_client is not None
                    and not (
                        existing_image is not None
                        and existing_image.storage_status == "ready"
                        and existing_image.asset_id is not None
                    )
                ):
                    try:
                        asset = _store_comment_image_asset(
                            session,
                            job=job,
                            bvid=bvid,
                            comment_rpid=comment.rpid,
                            ordinal=ordinal,
                            image=image,
                            web_client=web_client,
                            storage_client=storage_client,
                            temp_dir=temp_dir,
                        )
                    except (BilibiliWebError, ObjectStorageError, ValueError) as exc:
                        storage_status = "failed"
                        error_message = str(exc)
                        asset_id = None
                    else:
                        storage_status = "ready"
                        error_message = None
                        asset_id = asset.id

                raw_payload = strip_url_fields(dict(image.raw))
                if error_message is not None:
                    raw_payload["storage_error"] = error_message
                if asset_id is not None:
                    raw_payload["asset_id"] = str(asset_id)

                if existing_image is None:
                    session.add(
                        VideoCommentImage(
                            comment_id=persisted_comment.id,
                            rpid=comment.rpid,
                            bvid=bvid,
                            ordinal=ordinal,
                            source_url=None,
                            width=image.width,
                            height=image.height,
                            asset_id=asset_id,
                            storage_status=storage_status,
                            error_message=error_message,
                            raw=raw_payload,
                            crawled_at=crawled_at,
                        )
                    )
                    continue

                existing_image.rpid = comment.rpid
                existing_image.bvid = bvid
                existing_image.source_url = None
                existing_image.width = image.width
                existing_image.height = image.height
                existing_image.asset_id = asset_id
                existing_image.storage_status = storage_status
                existing_image.error_message = error_message
                existing_image.raw = raw_payload
                existing_image.crawled_at = crawled_at
                session.add(existing_image)
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

    session.flush()
    status_counts = _count_comment_image_statuses(session, bvid=bvid)
    return {
        "fetched_count": fetched_count,
        "total_count": status_counts["total"],
        "ready_count": status_counts["ready"],
        "failed_count": status_counts["failed"],
        "skipped_count": status_counts["skipped"],
    }

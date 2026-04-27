import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlmodel import delete, func, select

from app.api.deps import (
    CurrentUser,
    ObjectStorageClientDep,
    SessionDep,
    get_current_active_superuser,
)
from app.core.config import settings
from app.ingest_models import (
    AuxiliarySourceJobPublic,
    IngestJob,
    MediaAsset,
    MediaAssetPublic,
    SubtitleTranscriptionRequest,
    Video,
    VideoAssetsPublic,
    VideoComment,
    VideoCommentContextPublic,
    VideoCommentImage,
    VideoCommentImageEntryPublic,
    VideoCommentImagePublic,
    VideoCommentImagesPublic,
    VideoCommentPublic,
    VideoCommentsCompletenessPublic,
    VideoCommentsPublic,
    VideoDanmaku,
    VideoDanmakuCompletenessPublic,
    VideoDanmakuEntriesPublic,
    VideoDanmakuEntryPublic,
    VideoDanmakuPageCoveragePublic,
    VideoDetailPublic,
    VideoPage,
    VideosPublic,
    VideoStatSnapshot,
    VideoSubtitle,
    VideoSubtitlePublic,
    VideoSubtitlesCompletenessPublic,
    VideoSubtitlesPublic,
    VideoSummaryPublic,
)
from app.models import Message
from app.services.audit import record_audit_event
from app.services.signed_urls import build_media_playback_url
from app.services.subtitle_transcription import backfill_subtitle_transcription_tasks

router = APIRouter(prefix="/videos", tags=["videos"])


def _to_media_asset_public(asset: MediaAsset) -> MediaAssetPublic:
    return MediaAssetPublic(
        asset_id=asset.id,
        asset_type=asset.asset_type,
        variant=asset.variant,
        status=asset.status,
        cid=asset.cid,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        container_format=asset.container_format,
        video_codec=asset.video_codec,
        audio_codec=asset.audio_codec,
        width=asset.width,
        height=asset.height,
        duration_seconds=asset.duration_seconds,
        created_at=asset.created_at,
        ready_at=asset.ready_at,
    )


def _load_assets_by_id(
    *, session: SessionDep, asset_ids: set[uuid.UUID] | list[uuid.UUID]
) -> dict[uuid.UUID, MediaAsset]:
    normalized_ids = {asset_id for asset_id in asset_ids if asset_id is not None}
    if not normalized_ids:
        return {}
    return {
        asset.id: asset
        for asset in session.exec(
            select(MediaAsset).where(MediaAsset.id.in_(normalized_ids))
        ).all()
    }


def _build_asset_playback_url(asset: MediaAsset | None) -> str | None:
    if asset is None or asset.status not in {"ready", "uploaded"}:
        return None
    return build_media_playback_url(
        asset_id=asset.id,
        expires_in=settings.SIGNED_URL_EXPIRE_SECONDS,
    )


def _require_video(*, session: SessionDep, bvid: str) -> Video:
    video = session.get(Video, bvid)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _load_video_assets(*, session: SessionDep, bvid: str) -> list[MediaAsset]:
    return list(
        session.exec(
            select(MediaAsset)
            .where(MediaAsset.bvid == bvid)
            .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
        ).all()
    )


def _delete_video_storage_objects(
    *,
    session: SessionDep,
    storage_client: ObjectStorageClientDep,
    assets: list[MediaAsset],
) -> list[dict[str, str]]:
    deleted_objects: list[dict[str, str]] = []
    seen_locations: set[tuple[str, str]] = set()
    deleted_asset_ids = {asset.id for asset in assets}
    for asset in assets:
        if not asset.s3_bucket or not asset.s3_key:
            continue
        location = (asset.s3_bucket, asset.s3_key)
        if location in seen_locations:
            continue
        still_referenced = session.exec(
            select(MediaAsset.id)
            .where(
                MediaAsset.s3_bucket == asset.s3_bucket,
                MediaAsset.s3_key == asset.s3_key,
                ~MediaAsset.id.in_(deleted_asset_ids),
            )
            .limit(1)
        ).first()
        if still_referenced is not None:
            seen_locations.add(location)
            continue
        try:
            storage_client.delete_object(
                bucket=asset.s3_bucket,
                key=asset.s3_key,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to delete stored media for asset {asset.id}",
            ) from exc
        seen_locations.add(location)
        deleted_objects.append(
            {
                "bucket": asset.s3_bucket,
                "key": asset.s3_key,
            }
        )
    return deleted_objects


def _delete_video_rows(*, session: SessionDep, bvid: str) -> dict[str, int]:
    deleted_counts: dict[str, int] = {}
    delete_steps = (
        ("comment_images", delete(VideoCommentImage).where(VideoCommentImage.bvid == bvid)),
        ("comments", delete(VideoComment).where(VideoComment.bvid == bvid)),
        ("subtitles", delete(VideoSubtitle).where(VideoSubtitle.bvid == bvid)),
        ("danmaku", delete(VideoDanmaku).where(VideoDanmaku.bvid == bvid)),
        ("stat_snapshots", delete(VideoStatSnapshot).where(VideoStatSnapshot.bvid == bvid)),
        ("media_assets", delete(MediaAsset).where(MediaAsset.bvid == bvid)),
        ("pages", delete(VideoPage).where(VideoPage.bvid == bvid)),
    )
    for label, statement in delete_steps:
        result = session.execute(statement)
        deleted_counts[label] = int(result.rowcount or 0)
    return deleted_counts


def _to_video_summary_public(
    video: Video,
    *,
    cover_asset: MediaAsset | None = None,
) -> VideoSummaryPublic:
    return VideoSummaryPublic(
        bvid=video.bvid,
        aid=video.aid,
        title=video.title,
        owner_mid=video.owner_mid,
        owner_name=video.owner_name,
        duration_seconds=video.duration_seconds,
        pubdate=video.pubdate,
        category=video.category,
        cover_url=_build_asset_playback_url(cover_asset),
        tags=list(video.tags),
        takedown_status=video.takedown_status,
        last_crawled_at=video.last_crawled_at,
    )


def _to_video_detail_public(
    video: Video,
    *,
    cover_asset: MediaAsset | None = None,
) -> VideoDetailPublic:
    return VideoDetailPublic(
        **_to_video_summary_public(video, cover_asset=cover_asset).model_dump(),
        description=video.description,
        stat=dict(video.stat),
    )


def _to_video_comment_context_public(comment: VideoComment) -> VideoCommentContextPublic:
    return VideoCommentContextPublic(
        rpid=comment.rpid,
        oid=comment.oid,
        mid=comment.mid,
        uname=comment.uname,
        root=comment.root,
        parent=comment.parent,
        message=comment.message,
        like_count=comment.like_count,
        reply_count=comment.reply_count,
        ctime=comment.ctime,
    )


def _to_video_comment_image_public(
    image: VideoCommentImage,
    *,
    asset: MediaAsset | None,
) -> VideoCommentImagePublic:
    return VideoCommentImagePublic(
        source_url=_build_asset_playback_url(asset),
        width=image.width,
        height=image.height,
        asset_id=image.asset_id,
        storage_status=image.storage_status,
        error_message=image.error_message,
        asset=_to_media_asset_public(asset) if asset is not None else None,
    )


def _resolve_comment_thread_root_rpid(
    *,
    session: SessionDep,
    bvid: str,
    candidate_rpid: int | None,
) -> int | None:
    if candidate_rpid is None:
        return None
    comment = session.exec(
        select(VideoComment)
        .where(VideoComment.bvid == bvid, VideoComment.rpid == candidate_rpid)
        .limit(1)
    ).first()
    if comment is None:
        return candidate_rpid
    if comment.root is not None and comment.root != comment.rpid:
        return comment.root
    return comment.rpid


def _build_video_comment_filters(
    *,
    bvid: str,
    root: int | None = None,
    parent: int | None = None,
) -> list[object]:
    filters: list[object] = [VideoComment.bvid == bvid]
    if root is not None:
        filters.append(or_(VideoComment.rpid == root, VideoComment.root == root))
    if parent is not None:
        filters.append(VideoComment.parent == parent)
    return filters


def _build_video_comment_thread_root_filters(
    *,
    bvid: str,
    root: int | None = None,
) -> list[object]:
    filters: list[object] = [
        VideoComment.bvid == bvid,
        or_(VideoComment.root.is_(None), VideoComment.root == VideoComment.rpid),
    ]
    if root is not None:
        filters.append(VideoComment.rpid == root)
    return filters


def _load_video_comment_image_public_map(
    *,
    session: SessionDep,
    comments: list[VideoComment],
) -> dict[uuid.UUID, list[VideoCommentImagePublic]]:
    comment_ids = [comment.id for comment in comments]
    if not comment_ids:
        return {}

    images = list(
        session.exec(
            select(VideoCommentImage)
            .where(VideoCommentImage.comment_id.in_(comment_ids))
            .order_by(
                VideoCommentImage.comment_id.asc(),
                VideoCommentImage.ordinal.asc(),
            )
        ).all()
    )
    assets_by_id = _load_assets_by_id(
        session=session,
        asset_ids={image.asset_id for image in images if image.asset_id is not None},
    )

    images_by_comment_id: dict[uuid.UUID, list[VideoCommentImagePublic]] = {}
    for image in images:
        asset = assets_by_id.get(image.asset_id) if image.asset_id else None
        images_by_comment_id.setdefault(image.comment_id, []).append(
            _to_video_comment_image_public(image, asset=asset)
        )
    return images_by_comment_id


def _to_video_comment_public(
    comment: VideoComment,
    *,
    images_by_comment_id: dict[uuid.UUID, list[VideoCommentImagePublic]],
) -> VideoCommentPublic:
    return VideoCommentPublic(
        rpid=comment.rpid,
        oid=comment.oid,
        mid=comment.mid,
        uname=comment.uname,
        root=comment.root,
        parent=comment.parent,
        message=comment.message,
        like_count=comment.like_count,
        reply_count=comment.reply_count,
        ctime=comment.ctime,
        images=images_by_comment_id.get(comment.id, []),
    )


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _find_auxiliary_summary(
    *, session: SessionDep, bvid: str, auxiliary_key: str
) -> tuple[IngestJob, dict[str, Any]] | None:
    jobs = list(
        session.exec(
            select(IngestJob)
            .where(IngestJob.normalized_bvid == bvid)
            .order_by(IngestJob.created_at.desc(), IngestJob.id.desc())
            .limit(50)
        ).all()
    )
    for job in jobs:
        progress = _as_dict(job.progress)
        auxiliary = _as_dict(progress.get("auxiliary")) if progress else None
        summary = _as_dict(auxiliary.get(auxiliary_key)) if auxiliary else None
        if summary is not None:
            return job, summary
    return None


def _to_auxiliary_source_job_public(job: IngestJob) -> AuxiliarySourceJobPublic:
    progress = _as_dict(job.progress)
    metadata = _as_dict(progress.get("metadata")) if progress else None
    crawled_at = (
        _as_datetime(metadata.get("last_crawled_at")) if metadata else None
    ) or job.finished_at
    return AuxiliarySourceJobPublic(
        job_id=job.id,
        status=job.status,
        phase=job.phase,
        crawled_at=crawled_at,
    )


def _to_video_comments_completeness_public(
    *, session: SessionDep, bvid: str
) -> VideoCommentsCompletenessPublic | None:
    result = _find_auxiliary_summary(
        session=session, bvid=bvid, auxiliary_key="comments"
    )
    if result is None:
        return None
    job, summary = result
    stored_count = _as_int(summary.get("stored_count"))
    if stored_count is None:
        stored_count = _as_int(summary.get("count"))
    return VideoCommentsCompletenessPublic(
        partial=_as_bool(summary.get("partial")),
        expected_count=_as_int(summary.get("expected_count")),
        fetched_count=_as_int(summary.get("fetched_count")),
        stored_count=stored_count,
        fallback_used=_as_bool(summary.get("fallback_used")),
        image_count=_as_int(summary.get("image_count")),
        stored_image_count=_as_int(summary.get("stored_image_count")),
        failed_image_count=_as_int(summary.get("failed_image_count")),
        skipped_image_count=_as_int(summary.get("skipped_image_count")),
        source_job=_to_auxiliary_source_job_public(job),
    )


def _to_video_danmaku_completeness_public(
    *, session: SessionDep, bvid: str
) -> VideoDanmakuCompletenessPublic | None:
    result = _find_auxiliary_summary(
        session=session, bvid=bvid, auxiliary_key="danmaku"
    )
    if result is None:
        return None
    job, summary = result
    pages = summary.get("pages")
    page_items = [
        VideoDanmakuPageCoveragePublic(
            cid=_as_int(page.get("cid")) or 0,
            count=_as_int(page.get("count")),
            source=_as_str(page.get("source")),
            history_used=_as_bool(page.get("history_used")),
            snapshot_used=_as_bool(page.get("snapshot_used")),
            indexed_month_count=_as_int(page.get("indexed_month_count")),
            expected_days_count=_as_int(page.get("expected_days_count")),
            fetched_days_count=_as_int(page.get("fetched_days_count")),
            partial=_as_bool(page.get("partial")),
        )
        for page in pages
        if isinstance(page, dict) and _as_int(page.get("cid")) is not None
    ] if isinstance(pages, list) else []
    return VideoDanmakuCompletenessPublic(
        partial=_as_bool(summary.get("partial")),
        stored_count=_as_int(summary.get("stored_count")),
        duplicate_count=_as_int(summary.get("duplicate_count")),
        cid_count=_as_int(summary.get("cid_count")),
        filled_cid_count=_as_int(summary.get("filled_cid_count")),
        crawl_source=_as_str(summary.get("source")),
        history_used=_as_bool(summary.get("history_used")),
        snapshot_used=_as_bool(summary.get("snapshot_used")),
        indexed_month_count=_as_int(summary.get("indexed_month_count")),
        expected_days_count=_as_int(summary.get("expected_days_count")),
        fetched_days_count=_as_int(summary.get("fetched_days_count")),
        pages=page_items,
        source_job=_to_auxiliary_source_job_public(job),
    )


def _to_video_subtitles_completeness_public(
    *, session: SessionDep, bvid: str
) -> VideoSubtitlesCompletenessPublic | None:
    result = _find_auxiliary_summary(
        session=session, bvid=bvid, auxiliary_key="subtitles"
    )
    if result is None:
        return None
    job, summary = result
    return VideoSubtitlesCompletenessPublic(
        partial=_as_bool(summary.get("partial")),
        stored_count=_as_int(summary.get("count")),
        cid_count=_as_int(summary.get("cid_count")),
        languages=_as_str_list(summary.get("languages")),
        source_job=_to_auxiliary_source_job_public(job),
    )


@router.get("/", response_model=VideosPublic)
def read_videos(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    q: str | None = Query(default=None, min_length=1, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    del current_user

    filters: list[object] = []
    if q is not None:
        pattern = f"%{q.strip()}%"
        filters.append(or_(Video.bvid.ilike(pattern), Video.title.ilike(pattern)))

    total_count = (
        session.exec(
            select(func.count())
            .select_from(Video)
            .where(*filters)
        ).one()
        or 0
    )
    videos = list(
        session.exec(
            select(Video)
            .where(*filters)
            .order_by(Video.last_crawled_at.desc(), Video.pubdate.desc(), Video.bvid.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    assets_by_id = _load_assets_by_id(
        session=session,
        asset_ids={
            video.cover_asset_id
            for video in videos
            if video.cover_asset_id is not None
        },
    )
    return VideosPublic(
        data=[
            _to_video_summary_public(
                video,
                cover_asset=assets_by_id.get(video.cover_asset_id)
                if video.cover_asset_id is not None
                else None,
            )
            for video in videos
        ],
        count=int(total_count),
        limit=limit,
        offset=offset,
    )


@router.get("/{bvid}", response_model=VideoDetailPublic)
def read_video(
    *, session: SessionDep, current_user: CurrentUser, bvid: str
) -> Any:
    del current_user
    video = _require_video(session=session, bvid=bvid)
    cover_asset = None
    if video.cover_asset_id is not None:
        cover_asset = _load_assets_by_id(
            session=session,
            asset_ids={video.cover_asset_id},
        ).get(video.cover_asset_id)
    return _to_video_detail_public(video, cover_asset=cover_asset)


@router.delete(
    "/{bvid}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=Message,
)
def delete_video(
    *,
    session: SessionDep,
    storage_client: ObjectStorageClientDep,
    current_user: CurrentUser,
    bvid: str,
) -> Message:
    video = _require_video(session=session, bvid=bvid)
    assets = _load_video_assets(session=session, bvid=bvid)
    deleted_objects = _delete_video_storage_objects(
        session=session,
        storage_client=storage_client,
        assets=assets,
    )
    deleted_counts = _delete_video_rows(session=session, bvid=bvid)
    session.delete(video)
    record_audit_event(
        session=session,
        actor=current_user.email,
        action="video.deleted",
        resource_type="video",
        resource_id=bvid,
        message="Deleted a video and its stored artifacts",
        payload={
            "title": video.title,
            "deleted_object_count": len(deleted_objects),
            "deleted_row_counts": deleted_counts,
        },
    )
    session.commit()
    return Message(message="Video deleted successfully")


@router.get("/{bvid}/assets", response_model=VideoAssetsPublic)
def read_video_assets(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    bvid: str,
    asset_type: str | None = Query(default=None, min_length=1, max_length=64),
) -> Any:
    del current_user
    _require_video(session=session, bvid=bvid)

    statement = select(MediaAsset).where(MediaAsset.bvid == bvid)
    if asset_type is not None:
        statement = statement.where(MediaAsset.asset_type == asset_type)
    statement = statement.order_by(MediaAsset.created_at.desc())

    assets = list(session.exec(statement).all())
    return VideoAssetsPublic(
        bvid=bvid,
        assets=[_to_media_asset_public(asset) for asset in assets],
    )


@router.get("/{bvid}/comments", response_model=VideoCommentsPublic)
def read_video_comments(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    bvid: str,
    root: int | None = Query(default=None, ge=1),
    parent: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Any:
    del current_user
    _require_video(session=session, bvid=bvid)

    effective_root = _resolve_comment_thread_root_rpid(
        session=session,
        bvid=bvid,
        candidate_rpid=root,
    )
    filters = _build_video_comment_filters(
        bvid=bvid,
        root=effective_root,
        parent=parent,
    )

    total_count = (
        session.exec(
            select(func.count())
            .select_from(VideoComment)
            .where(*filters)
        ).one()
        or 0
    )
    thread_count = (
        session.exec(
            select(
                func.count(
                    func.distinct(func.coalesce(VideoComment.root, VideoComment.rpid))
                )
            )
            .select_from(VideoComment)
            .where(*filters)
        ).one()
        or 0
    )

    comments: list[VideoComment]
    if parent is None:
        thread_root_filters = _build_video_comment_thread_root_filters(
            bvid=bvid,
            root=effective_root,
        )
        root_comments = list(
            session.exec(
                select(VideoComment)
                .where(*thread_root_filters)
                .order_by(VideoComment.ctime.desc(), VideoComment.rpid.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )
        root_rpids = [comment.rpid for comment in root_comments]
        comments = []
        if root_rpids:
            descendants_by_root: dict[int, list[VideoComment]] = {}
            selected_root_rpids = set(root_rpids)
            descendants = list(
                session.exec(
                    select(VideoComment)
                    .where(
                        VideoComment.bvid == bvid,
                        VideoComment.root.in_(root_rpids),
                    )
                    .order_by(VideoComment.ctime.desc(), VideoComment.rpid.desc())
                ).all()
            )
            for comment in descendants:
                if comment.rpid in selected_root_rpids or comment.root is None:
                    continue
                descendants_by_root.setdefault(comment.root, []).append(comment)

            for root_comment in root_comments:
                comments.append(root_comment)
                comments.extend(descendants_by_root.get(root_comment.rpid, []))
    else:
        comments = list(
            session.exec(
                select(VideoComment)
                .where(*filters)
                .order_by(VideoComment.ctime.desc(), VideoComment.rpid.desc())
                .offset(offset)
                .limit(limit)
            ).all()
        )

    images_by_comment_id = _load_video_comment_image_public_map(
        session=session,
        comments=comments,
    )

    return VideoCommentsPublic(
        bvid=bvid,
        count=int(total_count),
        thread_count=int(thread_count),
        limit=limit,
        offset=offset,
        completeness=_to_video_comments_completeness_public(
            session=session,
            bvid=bvid,
        ),
        comments=[
            _to_video_comment_public(
                comment,
                images_by_comment_id=images_by_comment_id,
            )
            for comment in comments
        ],
    )


@router.get("/{bvid}/comment-images", response_model=VideoCommentImagesPublic)
def read_video_comment_images(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    bvid: str,
    rpid: int | None = Query(default=None, ge=1),
    root: int | None = Query(default=None, ge=1),
    parent: int | None = Query(default=None, ge=1),
    storage_status: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=60, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Any:
    del current_user
    _require_video(session=session, bvid=bvid)

    filters: list[object] = [VideoCommentImage.bvid == bvid]
    if rpid is not None:
        filters.append(VideoComment.rpid == rpid)
    if root is not None:
        filters.append(or_(VideoComment.rpid == root, VideoComment.root == root))
    if parent is not None:
        filters.append(VideoComment.parent == parent)
    if storage_status is not None:
        filters.append(VideoCommentImage.storage_status == storage_status)

    total_count = (
        session.exec(
            select(func.count())
            .select_from(VideoCommentImage)
            .join(VideoComment, VideoComment.id == VideoCommentImage.comment_id)
            .where(*filters)
        ).one()
        or 0
    )
    image_rows = list(
        session.exec(
            select(VideoCommentImage, VideoComment)
            .join(VideoComment, VideoComment.id == VideoCommentImage.comment_id)
            .where(*filters)
            .order_by(
                VideoComment.ctime.desc(),
                VideoComment.rpid.desc(),
                VideoCommentImage.ordinal.asc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )

    assets_by_id = _load_assets_by_id(
        session=session,
        asset_ids={
            image.asset_id
            for image, _comment in image_rows
            if image.asset_id is not None
        },
    )

    return VideoCommentImagesPublic(
        bvid=bvid,
        count=int(total_count),
        limit=limit,
        offset=offset,
        completeness=_to_video_comments_completeness_public(
            session=session,
            bvid=bvid,
        ),
        images=[
            VideoCommentImageEntryPublic(
                **_to_video_comment_image_public(
                    image,
                    asset=assets_by_id.get(image.asset_id)
                    if image.asset_id is not None
                    else None,
                ).model_dump(),
                image_id=image.id,
                ordinal=image.ordinal,
                crawled_at=image.crawled_at,
                comment=_to_video_comment_context_public(comment),
            )
            for image, comment in image_rows
        ],
    )


@router.get("/{bvid}/danmaku", response_model=VideoDanmakuEntriesPublic)
def read_video_danmaku(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    bvid: str,
    cid: int | None = Query(default=None, ge=1),
    source: str | None = Query(default=None, min_length=1, max_length=32),
    history_date: date | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> Any:
    del current_user
    _require_video(session=session, bvid=bvid)

    filters: list[object] = [VideoDanmaku.bvid == bvid]
    if cid is not None:
        filters.append(VideoDanmaku.cid == cid)
    if source is not None:
        filters.append(VideoDanmaku.source == source)
    if history_date is not None:
        filters.append(VideoDanmaku.history_date == history_date)

    total_count = (
        session.exec(
            select(func.count())
            .select_from(VideoDanmaku)
            .where(*filters)
        ).one()
        or 0
    )
    danmaku_entries = list(
        session.exec(
            select(VideoDanmaku)
            .where(*filters)
            .order_by(
                VideoDanmaku.history_date.asc(),
                VideoDanmaku.sent_at.asc(),
                VideoDanmaku.cid.asc(),
                VideoDanmaku.time_offset_seconds.asc(),
                VideoDanmaku.danmaku_id.asc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
    )

    return VideoDanmakuEntriesPublic(
        bvid=bvid,
        count=int(total_count),
        limit=limit,
        offset=offset,
        completeness=_to_video_danmaku_completeness_public(
            session=session,
            bvid=bvid,
        ),
        danmaku=[
            VideoDanmakuEntryPublic(
                danmaku_id=entry.danmaku_id,
                cid=entry.cid,
                time_offset_seconds=entry.time_offset_seconds,
                mode=entry.mode,
                font_size=entry.font_size,
                color=entry.color,
                content=entry.content,
                sent_at=entry.sent_at,
                source=entry.source,
                history_date=entry.history_date,
            )
            for entry in danmaku_entries
        ],
    )


@router.get("/{bvid}/subtitles", response_model=VideoSubtitlesPublic)
def read_video_subtitles(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    bvid: str,
    cid: int | None = Query(default=None, ge=1),
    lang: str | None = Query(default=None, min_length=1, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    del current_user
    _require_video(session=session, bvid=bvid)

    filters: list[object] = [VideoSubtitle.bvid == bvid]
    if cid is not None:
        filters.append(VideoSubtitle.cid == cid)
    if lang is not None:
        filters.append(VideoSubtitle.lang == lang)

    total_count = (
        session.exec(
            select(func.count())
            .select_from(VideoSubtitle)
            .where(*filters)
        ).one()
        or 0
    )
    subtitles = list(
        session.exec(
            select(VideoSubtitle)
            .where(*filters)
            .order_by(VideoSubtitle.crawled_at.desc(), VideoSubtitle.cid.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )

    return VideoSubtitlesPublic(
        bvid=bvid,
        count=int(total_count),
        limit=limit,
        offset=offset,
        completeness=_to_video_subtitles_completeness_public(
            session=session,
            bvid=bvid,
        ),
        subtitles=[
            VideoSubtitlePublic(
                subtitle_id=subtitle.id,
                cid=subtitle.cid,
                lang=subtitle.lang,
                source=subtitle.source,
                content=subtitle.content,
                asset_id=subtitle.asset_id,
                crawled_at=subtitle.crawled_at,
            )
            for subtitle in subtitles
        ],
    )


@router.post(
    "/{bvid}/subtitles/transcriptions",
    response_model=VideoAssetsPublic,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_video_subtitle_transcription_tasks(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    bvid: str,
    payload: SubtitleTranscriptionRequest,
) -> Any:
    del current_user
    _require_video(session=session, bvid=bvid)

    queued_assets = backfill_subtitle_transcription_tasks(
        session,
        bvid=bvid,
        cid=payload.cid,
        limit=payload.limit,
        replace_existing_ready=payload.replace_existing_ready,
    )
    session.commit()
    for asset in queued_assets:
        session.refresh(asset)

    return VideoAssetsPublic(
        bvid=bvid,
        assets=[_to_media_asset_public(asset) for asset in queued_assets],
    )

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import Session, delete, select

from app.crawler.bilibili_auxiliary import (
    BilibiliAuxiliaryProvider,
    BilibiliCommentMetadata,
    BilibiliDanmakuMetadata,
    BilibiliSubtitleMetadata,
)
from app.crawler.bilibili_metadata import BilibiliVideoMetadata
from app.crawler.bilibili_web import BilibiliWebClient
from app.ingest_models import (
    IngestJob,
    VideoComment,
    VideoDanmaku,
    VideoSubtitle,
)
from app.services.comment_image_ingest import merge_comment_images
from app.services.image_asset_ingest import strip_url_fields
from app.services.text_sanitization import strip_nul_bytes, strip_nul_text
from app.uploader.base import ObjectStorageClient


def _coerce_scalar_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if hasattr(value, "__getitem__"):
        return int(value[0])
    return int(value)


def requested_auxiliary_flags(job: IngestJob) -> dict[str, bool]:
    return {
        "comments": bool(job.options.get("fetch_comments")),
        "danmaku": bool(job.options.get("fetch_danmaku")),
        "subtitles": bool(job.options.get("fetch_subtitles")),
    }


def fetch_requested_auxiliary_data(
    *,
    session: Session,
    job: IngestJob,
    metadata: BilibiliVideoMetadata,
    provider: BilibiliAuxiliaryProvider,
    crawled_at: datetime,
    comment_image_web_client: BilibiliWebClient | None = None,
    comment_image_storage_client: ObjectStorageClient | None = None,
) -> dict[str, object]:
    flags = requested_auxiliary_flags(job)
    requested = [name for name, enabled in flags.items() if enabled]
    if not requested:
        return {}

    summary: dict[str, object] = {"requested": requested}

    if flags["comments"]:
        if metadata.aid is None:
            raise ValueError(
                f"Video {metadata.bvid} is missing aid metadata required for comments"
            )
        comment_fetch = provider.fetch_video_comments(
            bvid=metadata.bvid,
            aid=metadata.aid,
        )
        comments = comment_fetch.comments
        fetched_comment_count = comment_fetch.summary.fetched_count or len(comments)
        comment_merge_summary = _merge_comments(
            session,
            job=job,
            bvid=metadata.bvid,
            comments=comments,
            crawled_at=crawled_at,
            comment_image_web_client=comment_image_web_client,
            comment_image_storage_client=comment_image_storage_client,
        )
        summary["comments"] = {
            "count": comment_merge_summary["stored_count"],
            "expected_count": comment_fetch.summary.expected_count,
            "fetched_count": fetched_comment_count,
            "stored_count": comment_merge_summary["stored_count"],
            "fallback_used": comment_fetch.summary.fallback_used,
            "partial": comment_fetch.summary.partial,
            "image_count": comment_merge_summary["image_count"],
            "stored_image_count": comment_merge_summary["stored_image_count"],
            "failed_image_count": comment_merge_summary["failed_image_count"],
            "skipped_image_count": comment_merge_summary["skipped_image_count"],
        }

    if flags["danmaku"]:
        danmaku_entries: list[BilibiliDanmakuMetadata] = []
        danmaku_pages: list[dict[str, object]] = []
        danmaku_start_date = _resolve_danmaku_start_date(metadata)
        danmaku_end_date = _to_bilibili_site_date(crawled_at)
        for page in metadata.pages:
            danmaku_fetch = provider.fetch_video_danmaku(
                bvid=metadata.bvid,
                cid=page.cid,
                start_date=danmaku_start_date,
                end_date=danmaku_end_date,
            )
            danmaku_entries.extend(danmaku_fetch.entries)
            danmaku_pages.append(
                {
                    "cid": page.cid,
                    "count": len(danmaku_fetch.entries),
                    "source": danmaku_fetch.summary.source,
                    "history_used": danmaku_fetch.summary.history_used,
                    "snapshot_used": danmaku_fetch.summary.snapshot_used,
                    "indexed_month_count": danmaku_fetch.summary.indexed_month_count,
                    "expected_days_count": danmaku_fetch.summary.expected_days_count,
                    "fetched_days_count": danmaku_fetch.summary.fetched_days_count,
                    "partial": danmaku_fetch.summary.partial,
                }
            )
        danmaku_merge_summary = _merge_danmaku(
            session,
            bvid=metadata.bvid,
            entries=danmaku_entries,
            crawled_at=crawled_at,
        )
        stored_danmaku_cids = _load_stored_danmaku_cids(session, bvid=metadata.bvid)
        expected_days_count: int | None
        if all(
            page_summary["expected_days_count"] is not None
            for page_summary in danmaku_pages
        ):
            expected_days_count = sum(
                int(page_summary["expected_days_count"])
                for page_summary in danmaku_pages
            )
        else:
            expected_days_count = None
        danmaku_sources = {
            str(page_summary["source"]) for page_summary in danmaku_pages
        }
        summary["danmaku"] = {
            "count": danmaku_merge_summary["stored_count"],
            "stored_count": danmaku_merge_summary["stored_count"],
            "duplicate_count": danmaku_merge_summary["duplicate_count"],
            "cid_count": len(metadata.pages),
            "filled_cid_count": len(
                {page.cid for page in metadata.pages} & stored_danmaku_cids
            ),
            "source": (
                next(iter(danmaku_sources)) if len(danmaku_sources) == 1 else "mixed"
            ),
            "history_used": any(
                bool(page_summary["history_used"]) for page_summary in danmaku_pages
            ),
            "snapshot_used": any(
                bool(page_summary["snapshot_used"]) for page_summary in danmaku_pages
            ),
            "indexed_month_count": sum(
                int(page_summary["indexed_month_count"])
                for page_summary in danmaku_pages
            ),
            "expected_days_count": expected_days_count,
            "fetched_days_count": sum(
                int(page_summary["fetched_days_count"])
                for page_summary in danmaku_pages
            ),
            "partial": any(
                bool(page_summary["partial"]) for page_summary in danmaku_pages
            ),
            "pages": danmaku_pages,
        }

    if flags["subtitles"]:
        subtitle_tracks: list[BilibiliSubtitleMetadata] = []
        for page in metadata.pages:
            subtitle_tracks.extend(
                provider.fetch_video_subtitles(
                    bvid=metadata.bvid,
                    cid=page.cid,
                )
            )
        _replace_subtitles(
            session,
            bvid=metadata.bvid,
            tracks=subtitle_tracks,
            crawled_at=crawled_at,
        )
        summary["subtitles"] = {
            "count": len(subtitle_tracks),
            "cid_count": len({track.cid for track in subtitle_tracks}),
            "languages": sorted(
                language
                for track in subtitle_tracks
                if (language := strip_nul_text(track.lang))
            ),
        }

    return summary


def _merge_comments(
    session: Session,
    *,
    job: IngestJob,
    bvid: str,
    comments: list[BilibiliCommentMetadata],
    crawled_at: datetime,
    comment_image_web_client: BilibiliWebClient | None = None,
    comment_image_storage_client: ObjectStorageClient | None = None,
) -> dict[str, int]:
    persisted_comments = list(
        session.exec(select(VideoComment).where(VideoComment.bvid == bvid)).all()
    )
    persisted_comments_by_rpid = {
        persisted_comment.rpid: persisted_comment
        for persisted_comment in persisted_comments
    }
    for comment in comments:
        persisted_comment = persisted_comments_by_rpid.get(comment.rpid)
        if persisted_comment is None:
            persisted_comment = VideoComment(rpid=comment.rpid, bvid=bvid)
            persisted_comments_by_rpid[comment.rpid] = persisted_comment

        persisted_comment.oid = comment.oid
        persisted_comment.mid = comment.mid
        persisted_comment.uname = strip_nul_text(comment.uname)
        persisted_comment.root = comment.root
        persisted_comment.parent = comment.parent
        persisted_comment.message = strip_nul_text(comment.message)
        persisted_comment.like_count = comment.like_count
        persisted_comment.reply_count = comment.reply_count
        persisted_comment.ctime = comment.ctime
        persisted_comment.raw = strip_nul_bytes(strip_url_fields(comment.raw))
        persisted_comment.crawled_at = crawled_at
        session.add(persisted_comment)
    session.flush()
    comment_image_summary = merge_comment_images(
        session,
        job=job,
        bvid=bvid,
        comments=comments,
        persisted_comments_by_rpid=persisted_comments_by_rpid,
        crawled_at=crawled_at,
        web_client=comment_image_web_client,
        storage_client=comment_image_storage_client,
    )
    return {
        "stored_count": len(persisted_comments_by_rpid),
        "image_count": comment_image_summary["total_count"],
        "stored_image_count": comment_image_summary["ready_count"],
        "failed_image_count": comment_image_summary["failed_count"],
        "skipped_image_count": comment_image_summary["skipped_count"],
    }


def _load_stored_danmaku_cids(session: Session, *, bvid: str) -> set[int]:
    stored_cids: set[int] = set()
    for raw_cid in session.exec(
        select(VideoDanmaku.cid).where(VideoDanmaku.bvid == bvid).distinct()
    ).all():
        stored_cids.add(_coerce_scalar_int(raw_cid))
    return stored_cids


def _merge_danmaku(
    session: Session,
    *,
    bvid: str,
    entries: list[BilibiliDanmakuMetadata],
    crawled_at: datetime,
) -> dict[str, int]:
    existing_entries = list(
        session.exec(select(VideoDanmaku).where(VideoDanmaku.bvid == bvid)).all()
    )
    existing_by_key: dict[tuple[object, ...], VideoDanmaku] = {}
    existing_by_fallback_key: dict[tuple[object, ...], VideoDanmaku] = {}
    for existing_entry in existing_entries:
        existing_by_key.setdefault(
            _persisted_danmaku_dedupe_key(existing_entry),
            existing_entry,
        )
        existing_by_fallback_key.setdefault(
            _persisted_danmaku_fallback_key(existing_entry),
            existing_entry,
        )
    seen_keys: set[tuple[object, ...]] = set()
    duplicate_count = 0
    for entry in entries:
        dedupe_key = _danmaku_dedupe_key(entry)
        if dedupe_key in seen_keys:
            duplicate_count += 1
            continue
        seen_keys.add(dedupe_key)
        fallback_key = _incoming_danmaku_fallback_key(entry)
        persisted_entry = existing_by_key.get(dedupe_key)
        if persisted_entry is None and entry.id is not None:
            persisted_entry = existing_by_fallback_key.get(fallback_key)
        if persisted_entry is None:
            persisted_entry = VideoDanmaku(bvid=bvid, cid=entry.cid)
        persisted_entry.danmaku_id = entry.id
        persisted_entry.bvid = bvid
        persisted_entry.cid = entry.cid
        persisted_entry.time_offset_seconds = entry.time_offset_seconds
        persisted_entry.mode = entry.mode
        persisted_entry.font_size = entry.font_size
        persisted_entry.color = entry.color
        persisted_entry.content = strip_nul_text(entry.content)
        persisted_entry.sent_at = entry.sent_at
        persisted_entry.source = strip_nul_text(entry.source) or ""
        persisted_entry.history_date = entry.history_date
        persisted_entry.raw = strip_nul_bytes(entry.raw)
        persisted_entry.crawled_at = crawled_at
        session.add(persisted_entry)
        existing_by_key[_persisted_danmaku_dedupe_key(persisted_entry)] = (
            persisted_entry
        )
        existing_by_fallback_key[fallback_key] = persisted_entry
    session.flush()
    stored_count = (
        session.exec(
            select(func.count())
            .select_from(VideoDanmaku)
            .where(VideoDanmaku.bvid == bvid)
        ).one()
        or 0
    )
    return {
        "stored_count": _coerce_scalar_int(stored_count),
        "duplicate_count": duplicate_count,
    }


def _incoming_danmaku_fallback_key(
    entry: BilibiliDanmakuMetadata,
) -> tuple[object, ...]:
    return (
        "fallback",
        entry.cid,
        entry.time_offset_seconds,
        entry.sent_at,
        entry.mode,
        entry.font_size,
        entry.color,
        strip_nul_text(entry.content),
    )


def _persisted_danmaku_fallback_key(
    entry: VideoDanmaku,
) -> tuple[object, ...]:
    return (
        "fallback",
        entry.cid,
        entry.time_offset_seconds,
        entry.sent_at,
        entry.mode,
        entry.font_size,
        entry.color,
        entry.content,
    )


def _persisted_danmaku_dedupe_key(
    entry: VideoDanmaku,
) -> tuple[object, ...]:
    if entry.danmaku_id is not None:
        return ("id", entry.cid, entry.danmaku_id)
    return _persisted_danmaku_fallback_key(entry)


def _replace_subtitles(
    session: Session,
    *,
    bvid: str,
    tracks: list[BilibiliSubtitleMetadata],
    crawled_at: datetime,
) -> None:
    session.exec(delete(VideoSubtitle).where(VideoSubtitle.bvid == bvid))
    for track in tracks:
        session.add(
            VideoSubtitle(
                bvid=bvid,
                cid=track.cid,
                lang=strip_nul_text(track.lang),
                source=strip_nul_text(track.source),
                content=strip_nul_text(track.content),
                raw=strip_nul_bytes(track.raw),
                crawled_at=crawled_at,
            )
        )


def _resolve_danmaku_start_date(metadata: BilibiliVideoMetadata) -> date | None:
    if metadata.pubdate is None:
        return None
    return _to_bilibili_site_date(metadata.pubdate)


def _danmaku_dedupe_key(
    entry: BilibiliDanmakuMetadata,
) -> tuple[object, ...]:
    if entry.id is not None:
        return ("id", entry.cid, entry.id)
    return _incoming_danmaku_fallback_key(entry)


def _to_bilibili_site_date(value: datetime) -> date:
    site_timezone = timezone(timedelta(hours=8))
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(site_timezone).date()

from __future__ import annotations

import json
import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.crawler.bilibili_auxiliary import (
    BilibiliAuxiliaryNotFoundError,
    BilibiliAuxiliaryProvider,
    BilibiliAuxiliaryRateLimitedError,
    BilibiliAuxiliaryResponseError,
    BilibiliAuxiliaryTransportError,
    BilibiliCommentFetchResult,
    BilibiliCommentFetchSummary,
    BilibiliCommentImageMetadata,
    BilibiliCommentMetadata,
    BilibiliDanmakuFetchResult,
    BilibiliDanmakuFetchSummary,
    BilibiliDanmakuMetadata,
    BilibiliSubtitleMetadata,
)
from app.crawler.bilibili_danmaku_proto import (
    BilibiliDanmakuProtoError,
    parse_danmaku_segment,
)
from app.crawler.bilibili_web import (
    BilibiliWebClient,
    BilibiliWebNotFoundError,
    BilibiliWebRateLimitedError,
    BilibiliWebResponseError,
    BilibiliWebTransportError,
)

logger = logging.getLogger(__name__)
_BILIBILI_SITE_TIMEZONE = timezone(timedelta(hours=8))


def _coerce_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _coerce_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _coerce_datetime(value: object) -> datetime | None:
    timestamp = _coerce_int(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _coerce_positive_int(value: object) -> int | None:
    resolved = _coerce_int(value)
    if resolved is None or resolved <= 0:
        return None
    return resolved


def _normalize_url(base_url: str, value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("//"):
        return f"https:{value}"
    return urljoin(base_url, value)


def _is_ai_subtitle_url(value: str) -> bool:
    try:
        parsed = httpx.URL(value)
    except Exception:
        return "/ai_subtitle/" in value.lower()

    host = (parsed.host or "").lower()
    path = parsed.path.lower()
    return host == "aisubtitle.hdslb.com" or "/ai_subtitle/" in path


def _to_bilibili_site_date(value: datetime) -> date:
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(_BILIBILI_SITE_TIMEZONE).date()


def _iter_months(start_date: date, end_date: date) -> list[str]:
    current = date(start_date.year, start_date.month, 1)
    final = date(end_date.year, end_date.month, 1)
    months: list[str] = []
    while current <= final:
        months.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def _format_srt_timestamp(seconds: float) -> str:
    total_milliseconds = max(0, round(seconds * 1000))
    hours = total_milliseconds // 3_600_000
    minutes = (total_milliseconds % 3_600_000) // 60_000
    secs = (total_milliseconds % 60_000) // 1_000
    milliseconds = total_milliseconds % 1_000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def _subtitle_body_to_srt(payload: list[object]) -> str:
    sections: list[str] = []
    cue_number = 1
    for item in payload:
        if not isinstance(item, dict):
            raise BilibiliAuxiliaryResponseError("Subtitle body item was not an object")

        content = _coerce_str(item.get("content"))
        start_seconds = _coerce_float(item.get("from"))
        end_seconds = _coerce_float(item.get("to"))
        if content is None or start_seconds is None or end_seconds is None:
            continue

        start_timestamp = _format_srt_timestamp(start_seconds)
        end_timestamp = _format_srt_timestamp(max(start_seconds, end_seconds))
        sections.append(
            f"{cue_number}\n{start_timestamp} --> {end_timestamp}\n{content}"
        )
        cue_number += 1

    return "\n\n".join(sections)


def _parse_comment_images(
    payload: object,
    *,
    base_url: str,
) -> list[BilibiliCommentImageMetadata]:
    if payload in (None, []):
        return []
    if not isinstance(payload, list):
        raise BilibiliAuxiliaryResponseError(
            "Bilibili comment pictures payload was not a list"
        )

    images: list[BilibiliCommentImageMetadata] = []
    for item in payload:
        if not isinstance(item, dict):
            raise BilibiliAuxiliaryResponseError(
                "Bilibili comment picture payload was not an object"
            )

        source_url = _normalize_url(base_url, _coerce_str(item.get("img_src")))
        if source_url is None:
            continue

        images.append(
            BilibiliCommentImageMetadata(
                source_url=source_url,
                width=_coerce_positive_int(item.get("img_width"))
                or _coerce_positive_int(item.get("width")),
                height=_coerce_positive_int(item.get("img_height"))
                or _coerce_positive_int(item.get("height")),
                raw=item,
            )
        )

    return images


def _extend_reply_items(
    *,
    target: list[object],
    payload: object,
    field_name: str,
    bvid: str,
) -> None:
    if payload in (None, []):
        return
    if not isinstance(payload, list):
        raise BilibiliAuxiliaryResponseError(
            f"Bilibili comments {field_name} payload was invalid for {bvid}"
        )
    target.extend(payload)


def _extend_reply_item(
    *,
    target: list[object],
    payload: object,
    field_name: str,
    bvid: str,
) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise BilibiliAuxiliaryResponseError(
            f"Bilibili comments {field_name} payload was invalid for {bvid}"
        )
    target.append(payload)


class BilibiliHttpAuxiliaryProvider(BilibiliAuxiliaryProvider):
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        retry_attempts: int | None = None,
        cookie_header: str | None = None,
    ) -> None:
        self._web_client = BilibiliWebClient(
            client=client,
            base_url=base_url,
            timeout=timeout,
            cookie_header=cookie_header,
        )
        self._base_url = self._web_client.base_url
        self._retry_attempts = retry_attempts or settings.BILIBILI_METADATA_RETRY_ATTEMPTS
        self._comment_empty_page_retry_attempts = max(
            1,
            settings.BILIBILI_COMMENT_EMPTY_PAGE_RETRY_ATTEMPTS,
        )

    def close(self) -> None:
        self._web_client.close()

    def fetch_video_comments(
        self,
        *,
        bvid: str,
        aid: int,
    ) -> BilibiliCommentFetchResult:
        expected_comment_count = self._fetch_comment_count(aid=aid)
        fallback_used = False
        try:
            comments = self._fetch_video_comments_wbi(bvid=bvid, aid=aid)
        except (
            BilibiliAuxiliaryNotFoundError,
            BilibiliAuxiliaryResponseError,
        ) as exc:
            fallback_used = True
            logger.warning(
                "Falling back to legacy Bilibili reply endpoint for %s: %s",
                bvid,
                exc,
            )
            comments = self._fetch_video_comments_legacy(bvid=bvid, aid=aid)

        fetched_comment_count = len(comments)
        partial = (
            expected_comment_count is not None
            and fetched_comment_count < expected_comment_count
        )
        if partial:
            logger.warning(
                "Fetched %s/%s comments for %s; results may be partial",
                fetched_comment_count,
                expected_comment_count,
                bvid,
            )

        return BilibiliCommentFetchResult(
            comments=comments,
            summary=BilibiliCommentFetchSummary(
                expected_count=expected_comment_count,
                fetched_count=fetched_comment_count,
                fallback_used=fallback_used,
                partial=partial,
            ),
        )

    def _fetch_video_comments_wbi(
        self,
        *,
        bvid: str,
        aid: int,
    ) -> list[BilibiliCommentMetadata]:
        collected: dict[int, BilibiliCommentMetadata] = {}
        empty_page_attempts = 0
        next_offset = ""
        seen_offsets = {next_offset}
        video_referer = f"https://www.bilibili.com/video/{bvid}/"

        while True:
            payload = self._request_json(
                "/x/v2/reply/wbi/main",
                params={
                    "oid": aid,
                    "type": 1,
                    "mode": 2,
                    "pagination_str": self._build_pagination_str(next_offset),
                    "plat": 1,
                    "seek_rpid": "",
                    "web_location": 1315875,
                },
                context=f"comments for {bvid} offset {next_offset or 'initial'}",
                use_wbi=True,
                referer=video_referer,
            )
            if not isinstance(payload, dict):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili comments payload was invalid for {bvid}"
                )

            reply_items = self._extract_main_reply_items(payload, bvid=bvid)
            if not reply_items:
                empty_page_attempts += 1
                if empty_page_attempts >= self._comment_empty_page_retry_attempts:
                    logger.warning(
                        "Stopping comment crawl for %s after %s empty WBI pages at offset %s",
                        bvid,
                        empty_page_attempts,
                        next_offset or "<initial>",
                    )
                    break
                logger.warning(
                    "Retrying empty WBI comment page for %s at offset %s (%s/%s)",
                    bvid,
                    next_offset or "<initial>",
                    empty_page_attempts,
                    self._comment_empty_page_retry_attempts,
                )
                continue

            empty_page_attempts = 0
            self._collect_main_comment_items(
                reply_items,
                oid=aid,
                collected=collected,
                referer=video_referer,
            )

            cursor_payload = payload.get("cursor")
            is_end = bool(cursor_payload.get("is_end")) if isinstance(cursor_payload, dict) else False
            candidate_offset = self._parse_cursor_offset(cursor_payload)
            if is_end or candidate_offset is None or candidate_offset in seen_offsets:
                break

            next_offset = candidate_offset
            seen_offsets.add(next_offset)

        return list(collected.values())

    def _fetch_video_comments_legacy(
        self,
        *,
        bvid: str,
        aid: int,
    ) -> list[BilibiliCommentMetadata]:
        collected: dict[int, BilibiliCommentMetadata] = {}
        page_number = 1
        total_pages: int | None = None
        video_referer = f"https://www.bilibili.com/video/{bvid}/"

        while total_pages is None or page_number <= total_pages:
            payload = self._request_json(
                "/x/v2/reply",
                params={
                    "oid": aid,
                    "type": 1,
                    "pn": page_number,
                    "sort": 2,
                },
                context=f"legacy comments for {bvid} page {page_number}",
                referer=video_referer,
            )
            if not isinstance(payload, dict):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili comments payload was invalid for {bvid}"
                )

            page_payload = payload.get("page")
            if isinstance(page_payload, dict):
                page_count = _coerce_int(page_payload.get("count"))
                page_size = _coerce_int(page_payload.get("size")) or 20
                if page_count is not None and page_size > 0:
                    total_pages = max(1, math.ceil(page_count / page_size))

            reply_items = self._extract_main_reply_items(payload, bvid=bvid)
            if not reply_items:
                break

            self._collect_main_comment_items(
                reply_items,
                oid=aid,
                collected=collected,
                referer=video_referer,
            )
            page_number += 1

        return list(collected.values())

    def fetch_video_danmaku(
        self,
        *,
        bvid: str,
        cid: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BilibiliDanmakuFetchResult:
        history_entries: list[BilibiliDanmakuMetadata] = []
        indexed_month_count = 0
        expected_days_count: int | None = None
        fetched_days_count = 0
        history_used = False
        partial = False

        if start_date is not None and end_date is not None and start_date <= end_date:
            (
                history_dates,
                indexed_month_count,
                expected_days_count,
                history_partial,
            ) = self._fetch_video_danmaku_history_dates(
                bvid=bvid,
                cid=cid,
                start_date=start_date,
                end_date=end_date,
            )
            partial = history_partial
            if history_dates:
                history_used = True
                for history_date in history_dates:
                    day_entries = self._fetch_video_danmaku_history_day(
                        bvid=bvid,
                        cid=cid,
                        history_date=history_date,
                    )
                    if day_entries is None:
                        partial = True
                        continue
                    fetched_days_count += 1
                    history_entries.extend(day_entries)
        else:
            partial = True

        snapshot_entries = self._fetch_video_danmaku_snapshot(
            bvid=bvid,
            cid=cid,
        )
        snapshot_used = bool(snapshot_entries)
        source = "snapshot_xml"
        if history_used and snapshot_used:
            source = "history_and_snapshot"
        elif history_used:
            source = "history_proto"

        return BilibiliDanmakuFetchResult(
            entries=[*history_entries, *snapshot_entries],
            summary=BilibiliDanmakuFetchSummary(
                source=source,
                history_used=history_used,
                snapshot_used=snapshot_used,
                indexed_month_count=indexed_month_count,
                expected_days_count=expected_days_count,
                fetched_days_count=fetched_days_count,
                partial=partial,
            ),
        )

    def _fetch_video_danmaku_history_dates(
        self,
        *,
        bvid: str,
        cid: int,
        start_date: date,
        end_date: date,
    ) -> tuple[list[date], int, int | None, bool]:
        history_dates: set[date] = set()
        indexed_months = _iter_months(start_date, end_date)
        expected_known = True
        partial = False
        video_referer = f"https://www.bilibili.com/video/{bvid}/"

        for month in indexed_months:
            payload = self._request_optional_json(
                "/x/v2/dm/history/index",
                params={
                    "type": 1,
                    "oid": cid,
                    "month": month,
                },
                context=f"danmaku history index for {bvid} cid {cid} month {month}",
                referer=video_referer,
            )
            if payload is None:
                expected_known = False
                partial = True
                continue

            try:
                month_dates = self._parse_danmaku_history_index_dates(
                    payload,
                    cid=cid,
                    month=month,
                )
            except BilibiliAuxiliaryResponseError as exc:
                logger.warning(
                    "Skipping invalid danmaku history index for %s cid %s month %s: %s",
                    bvid,
                    cid,
                    month,
                    exc,
                )
                expected_known = False
                partial = True
                continue

            history_dates.update(
                item for item in month_dates if start_date <= item <= end_date
            )

        expected_days_count = len(history_dates) if expected_known else None
        return sorted(history_dates), len(indexed_months), expected_days_count, partial

    def _parse_danmaku_history_index_dates(
        self,
        payload: object,
        *,
        cid: int,
        month: str,
    ) -> list[date]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise BilibiliAuxiliaryResponseError(
                f"Bilibili danmaku history index was invalid for cid {cid} month {month}"
            )

        dates: list[date] = []
        for item in payload:
            item_text = _coerce_str(item)
            if item_text is None:
                continue
            try:
                dates.append(date.fromisoformat(item_text))
            except ValueError as exc:
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili danmaku history date was invalid for cid {cid} month {month}"
                ) from exc
        return dates

    def _fetch_video_danmaku_history_day(
        self,
        *,
        bvid: str,
        cid: int,
        history_date: date,
    ) -> list[BilibiliDanmakuMetadata] | None:
        video_referer = f"https://www.bilibili.com/video/{bvid}/"
        response = self._request_optional_bytes(
            "/x/v2/dm/web/history/seg.so",
            params={
                "type": 1,
                "oid": cid,
                "date": history_date.isoformat(),
            },
            context=(
                f"danmaku history segment for {bvid} cid {cid} date "
                f"{history_date.isoformat()}"
            ),
            referer=video_referer,
        )
        if response is None:
            return None

        payload, _content_type = response
        if not payload:
            return []

        try:
            parsed_entries = parse_danmaku_segment(payload)
        except BilibiliDanmakuProtoError as exc:
            logger.warning(
                "Skipping invalid danmaku history protobuf for %s cid %s date %s: %s",
                bvid,
                cid,
                history_date.isoformat(),
                exc,
            )
            return None

        entries: list[BilibiliDanmakuMetadata] = []
        for item in parsed_entries:
            progress_milliseconds = _coerce_int(item.get("progress"))
            entries.append(
                BilibiliDanmakuMetadata(
                    id=_coerce_int(item.get("id")),
                    cid=cid,
                    time_offset_seconds=(
                        progress_milliseconds / 1000
                        if progress_milliseconds is not None
                        else None
                    ),
                    mode=_coerce_int(item.get("mode")),
                    font_size=_coerce_int(item.get("fontsize")),
                    color=_coerce_int(item.get("color")),
                    content=_coerce_str(item.get("content")),
                    sent_at=_coerce_datetime(item.get("ctime")),
                    source="history_proto",
                    history_date=history_date,
                    raw=item,
                )
            )

        return entries

    def _fetch_video_danmaku_snapshot(
        self,
        *,
        bvid: str,
        cid: int,
    ) -> list[BilibiliDanmakuMetadata]:
        video_referer = f"https://www.bilibili.com/video/{bvid}/"
        try:
            xml_text = self._request_text(
                f"https://comment.bilibili.com/{cid}.xml",
                context=f"danmaku for {bvid} cid {cid}",
                referer=video_referer,
            )
        except BilibiliAuxiliaryNotFoundError:
            logger.warning("Skipping missing danmaku XML for %s cid %s", bvid, cid)
            return []

        if not xml_text.strip():
            return []

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise BilibiliAuxiliaryResponseError(
                f"Bilibili danmaku XML was invalid for {bvid} cid {cid}"
            ) from exc

        entries: list[BilibiliDanmakuMetadata] = []
        for element in root.findall("d"):
            params = element.attrib.get("p")
            if not params:
                continue

            pieces = params.split(",")
            sent_at = _coerce_datetime(pieces[4]) if len(pieces) > 4 else None
            entries.append(
                BilibiliDanmakuMetadata(
                    id=_coerce_int(pieces[7]) if len(pieces) > 7 else None,
                    cid=cid,
                    time_offset_seconds=_coerce_float(pieces[0]) if pieces else None,
                    mode=_coerce_int(pieces[1]) if len(pieces) > 1 else None,
                    font_size=_coerce_int(pieces[2]) if len(pieces) > 2 else None,
                    color=_coerce_int(pieces[3]) if len(pieces) > 3 else None,
                    content=_coerce_str(element.text),
                    sent_at=sent_at,
                    source="snapshot_xml",
                    history_date=None,
                    raw={"p": params},
                )
            )

        return entries

    def fetch_video_subtitles(
        self,
        *,
        bvid: str,
        cid: int,
    ) -> list[BilibiliSubtitleMetadata]:
        video_referer = f"https://www.bilibili.com/video/{bvid}/"
        payload = self._request_json(
            "/x/player/v2",
            params={"bvid": bvid, "cid": cid},
            context=f"subtitle listing for {bvid} cid {cid}",
            referer=video_referer,
        )
        if not isinstance(payload, dict):
            raise BilibiliAuxiliaryResponseError(
                f"Bilibili subtitle payload was invalid for {bvid} cid {cid}"
            )

        subtitle_payload = payload.get("subtitle")
        if not isinstance(subtitle_payload, dict):
            return []

        subtitle_items = subtitle_payload.get("subtitles")
        if subtitle_items in (None, []):
            return []
        if not isinstance(subtitle_items, list):
            raise BilibiliAuxiliaryResponseError(
                f"Bilibili subtitle listing was invalid for {bvid} cid {cid}"
            )

        subtitles: list[BilibiliSubtitleMetadata] = []
        for item in subtitle_items:
            if not isinstance(item, dict):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili subtitle track was invalid for {bvid} cid {cid}"
                )

            subtitle_url = _normalize_url(
                self._base_url,
                _coerce_str(item.get("subtitle_url")),
            )
            if subtitle_url is None:
                logger.warning(
                    "Skipping subtitle track without URL for %s cid %s: %s",
                    bvid,
                    cid,
                    {
                        "id": item.get("id"),
                        "lan": item.get("lan"),
                        "lan_doc": item.get("lan_doc"),
                    },
                )
                continue
            if _is_ai_subtitle_url(subtitle_url):
                logger.info(
                    "Skipping AI subtitle track for %s cid %s: %s",
                    bvid,
                    cid,
                    {
                        "id": item.get("id"),
                        "lan": item.get("lan"),
                        "lan_doc": item.get("lan_doc"),
                        "subtitle_url": subtitle_url,
                    },
                )
                continue

            track_payload = self._request_json(
                subtitle_url,
                params={},
                context=f"subtitle track for {bvid} cid {cid}",
                unwrap_bilibili_data=False,
                referer=video_referer,
            )
            if not isinstance(track_payload, dict):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili subtitle track payload was invalid for {bvid} cid {cid}"
                )

            body_payload = track_payload.get("body", [])
            if not isinstance(body_payload, list):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili subtitle track body was invalid for {bvid} cid {cid}"
                )

            subtitles.append(
                BilibiliSubtitleMetadata(
                    cid=cid,
                    lang=_coerce_str(item.get("lan"))
                    or _coerce_str(item.get("lan_doc")),
                    source="bilibili_player_v2",
                    content=_subtitle_body_to_srt(body_payload),
                    raw={
                        "track": item,
                        "body": track_payload,
                    },
                )
            )

        return subtitles

    def _fetch_comment_count(self, *, aid: int) -> int | None:
        payload = self._request_optional_json(
            "/x/v2/reply/count",
            params={"oid": aid, "type": 1},
            context=f"comment count for aid {aid}",
        )
        if payload is None:
            return None
        if not isinstance(payload, dict):
            raise BilibiliAuxiliaryResponseError(
                f"Bilibili comment count payload was invalid for aid {aid}"
            )
        return _coerce_int(payload.get("count"))

    def _request_optional_json(
        self,
        path: str,
        *,
        params: dict[str, object],
        context: str,
        referer: str | None = None,
    ) -> object | None:
        try:
            return self._request_json(
                path,
                params=params,
                context=context,
                referer=referer,
            )
        except (
            BilibiliAuxiliaryNotFoundError,
            BilibiliAuxiliaryRateLimitedError,
            BilibiliAuxiliaryResponseError,
            BilibiliAuxiliaryTransportError,
        ) as exc:
            logger.warning("Skipping optional %s: %s", context, exc)
            return None

    def _request_optional_bytes(
        self,
        url: str,
        *,
        params: dict[str, object],
        context: str,
        referer: str | None = None,
    ) -> tuple[bytes, str | None] | None:
        try:
            return self._request_bytes(
                url,
                params=params,
                context=context,
                referer=referer,
            )
        except (
            BilibiliAuxiliaryNotFoundError,
            BilibiliAuxiliaryRateLimitedError,
            BilibiliAuxiliaryResponseError,
            BilibiliAuxiliaryTransportError,
        ) as exc:
            logger.warning("Skipping optional %s: %s", context, exc)
            return None

    def _extract_main_reply_items(
        self,
        payload: dict[str, object],
        *,
        bvid: str,
    ) -> list[object]:
        reply_items: list[object] = []

        top_payload = payload.get("top")
        if top_payload is not None:
            if not isinstance(top_payload, dict):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili comments top payload was invalid for {bvid}"
                )
            for field_name in ("admin", "upper", "vote"):
                _extend_reply_item(
                    target=reply_items,
                    payload=top_payload.get(field_name),
                    field_name=f"top.{field_name}",
                    bvid=bvid,
                )

        upper_payload = payload.get("upper")
        if upper_payload is not None:
            if not isinstance(upper_payload, dict):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili comments upper payload was invalid for {bvid}"
                )
            for field_name in ("top", "vote"):
                _extend_reply_item(
                    target=reply_items,
                    payload=upper_payload.get(field_name),
                    field_name=f"upper.{field_name}",
                    bvid=bvid,
                )

        for field_name in ("top_replies", "hots", "replies"):
            _extend_reply_items(
                target=reply_items,
                payload=payload.get(field_name),
                field_name=field_name,
                bvid=bvid,
            )
        return reply_items

    def _extract_child_reply_items(
        self,
        payload: dict[str, object],
        *,
        root_rpid: int,
    ) -> list[object]:
        reply_items: list[object] = []
        for field_name in ("top_replies", "hots", "replies"):
            _extend_reply_items(
                target=reply_items,
                payload=payload.get(field_name),
                field_name=field_name,
                bvid=str(root_rpid),
            )
        return reply_items

    def _collect_main_comment_items(
        self,
        reply_items: list[object],
        *,
        oid: int,
        collected: dict[int, BilibiliCommentMetadata],
        referer: str | None,
    ) -> None:
        for item in reply_items:
            parsed = self._collect_comment_tree(
                item,
                oid=oid,
                collected=collected,
            )
            root_rpid = parsed.rpid
            initial_child_count = (
                len(item.get("replies", []))
                if isinstance(item, dict) and isinstance(item.get("replies"), list)
                else 0
            )
            if (parsed.reply_count or 0) > initial_child_count:
                for child in self._fetch_child_comments(
                    oid=oid,
                    root_rpid=root_rpid,
                    referer=referer,
                ):
                    collected[child.rpid] = child

    def _build_pagination_str(self, offset: str) -> str:
        return json.dumps({"offset": offset}, separators=(",", ":"))

    def _parse_cursor_offset(self, payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        pagination_reply = payload.get("pagination_reply")
        if not isinstance(pagination_reply, dict):
            return None
        return _coerce_str(pagination_reply.get("next_offset"))

    def _collect_comment_tree(
        self,
        payload: object,
        *,
        oid: int,
        collected: dict[int, BilibiliCommentMetadata],
    ) -> BilibiliCommentMetadata:
        parsed = self._parse_comment(payload, oid=oid)
        collected[parsed.rpid] = parsed

        if isinstance(payload, dict):
            nested_replies = payload.get("replies")
            if isinstance(nested_replies, list):
                for item in nested_replies:
                    nested = self._collect_comment_tree(
                        item,
                        oid=oid,
                        collected=collected,
                    )
                    collected[nested.rpid] = nested

        return parsed

    def _fetch_child_comments(
        self,
        *,
        oid: int,
        root_rpid: int,
        referer: str | None,
    ) -> list[BilibiliCommentMetadata]:
        children: dict[int, BilibiliCommentMetadata] = {}
        page_number = 1
        total_pages: int | None = None

        while total_pages is None or page_number <= total_pages:
            payload = self._request_json(
                "/x/v2/reply/reply",
                params={
                    "oid": oid,
                    "type": 1,
                    "root": root_rpid,
                    "ps": 20,
                    "pn": page_number,
                    "web_location": "333.788",
                },
                context=f"child comments for oid {oid} root {root_rpid} page {page_number}",
                referer=referer,
            )
            if not isinstance(payload, dict):
                raise BilibiliAuxiliaryResponseError(
                    f"Bilibili child comments payload was invalid for root {root_rpid}"
                )

            page_payload = payload.get("page")
            if isinstance(page_payload, dict):
                page_count = _coerce_int(page_payload.get("count"))
                page_size = _coerce_int(page_payload.get("size")) or 10
                if page_count is not None and page_size > 0:
                    total_pages = max(1, math.ceil(page_count / page_size))

            reply_items = self._extract_child_reply_items(
                payload,
                root_rpid=root_rpid,
            )
            if not reply_items:
                break

            for item in reply_items:
                parsed = self._collect_comment_tree(
                    item,
                    oid=oid,
                    collected=children,
                )
                children[parsed.rpid] = parsed

            page_number += 1

        return list(children.values())

    def _parse_comment(self, payload: object, *, oid: int) -> BilibiliCommentMetadata:
        if not isinstance(payload, dict):
            raise BilibiliAuxiliaryResponseError("Bilibili comment payload was not an object")

        rpid = _coerce_int(payload.get("rpid"))
        if rpid is None:
            raise BilibiliAuxiliaryResponseError("Bilibili comment was missing an rpid")

        member = payload.get("member")
        content = payload.get("content")
        member_payload = member if isinstance(member, dict) else {}
        content_payload = content if isinstance(content, dict) else {}

        return BilibiliCommentMetadata(
            rpid=rpid,
            oid=oid,
            mid=_coerce_int(member_payload.get("mid")),
            uname=_coerce_str(member_payload.get("uname"))
            or _coerce_str(member_payload.get("name")),
            root=_coerce_positive_int(payload.get("root")),
            parent=_coerce_positive_int(payload.get("parent")),
            message=_coerce_str(content_payload.get("message")),
            like_count=_coerce_int(payload.get("like")),
            reply_count=_coerce_int(payload.get("rcount"))
            or _coerce_int(payload.get("reply_count")),
            ctime=_coerce_datetime(payload.get("ctime")),
            images=_parse_comment_images(
                content_payload.get("pictures"),
                base_url=self._base_url,
            ),
            raw=payload,
        )

    def _request_json(
        self,
        url: str,
        *,
        params: dict[str, object],
        context: str,
        use_wbi: bool = False,
        referer: str | None = None,
        unwrap_bilibili_data: bool = True,
    ) -> object:
        return self._call_with_retry(
            lambda: self._request_json_once(
                url,
                params=params,
                context=context,
                use_wbi=use_wbi,
                referer=referer,
                unwrap_bilibili_data=unwrap_bilibili_data,
            )
        )

    def _request_text(
        self,
        url: str,
        *,
        context: str,
        referer: str | None = None,
    ) -> str:
        return self._call_with_retry(
            lambda: self._request_text_once(
                url,
                context=context,
                referer=referer,
            )
        )

    def _request_bytes(
        self,
        url: str,
        *,
        params: dict[str, object],
        context: str,
        referer: str | None = None,
    ) -> tuple[bytes, str | None]:
        return self._call_with_retry(
            lambda: self._request_bytes_once(
                url,
                params=params,
                context=context,
                referer=referer,
            )
        )

    def _call_with_retry(self, func: Callable[[], object]) -> object:
        for attempt in Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(
                (
                    BilibiliAuxiliaryRateLimitedError,
                    BilibiliAuxiliaryTransportError,
                )
            ),
            reraise=True,
        ):
            with attempt:
                return func()
        raise AssertionError("Retry loop exited unexpectedly")

    def _request_json_once(
        self,
        url: str,
        *,
        params: dict[str, object],
        context: str,
        use_wbi: bool,
        referer: str | None,
        unwrap_bilibili_data: bool,
    ) -> object:
        try:
            return self._web_client.request_json(
                url,
                params=params,
                context=context,
                use_wbi=use_wbi,
                referer=referer,
                unwrap_bilibili_data=unwrap_bilibili_data,
            )
        except BilibiliWebNotFoundError as exc:
            raise BilibiliAuxiliaryNotFoundError(str(exc)) from exc
        except BilibiliWebRateLimitedError as exc:
            raise BilibiliAuxiliaryRateLimitedError(str(exc)) from exc
        except BilibiliWebTransportError as exc:
            raise BilibiliAuxiliaryTransportError(str(exc)) from exc
        except BilibiliWebResponseError as exc:
            raise BilibiliAuxiliaryResponseError(str(exc)) from exc

    def _request_text_once(
        self,
        url: str,
        *,
        context: str,
        referer: str | None,
    ) -> str:
        try:
            return self._web_client.request_text(
                url,
                context=context,
                referer=referer,
            )
        except BilibiliWebNotFoundError as exc:
            raise BilibiliAuxiliaryNotFoundError(str(exc)) from exc
        except BilibiliWebRateLimitedError as exc:
            raise BilibiliAuxiliaryRateLimitedError(str(exc)) from exc
        except BilibiliWebTransportError as exc:
            raise BilibiliAuxiliaryTransportError(str(exc)) from exc
        except BilibiliWebResponseError as exc:
            raise BilibiliAuxiliaryResponseError(str(exc)) from exc

    def _request_bytes_once(
        self,
        url: str,
        *,
        params: dict[str, object],
        context: str,
        referer: str | None,
    ) -> tuple[bytes, str | None]:
        try:
            return self._web_client.request_bytes(
                url,
                params=params,
                context=context,
                referer=referer,
            )
        except BilibiliWebNotFoundError as exc:
            raise BilibiliAuxiliaryNotFoundError(str(exc)) from exc
        except BilibiliWebRateLimitedError as exc:
            raise BilibiliAuxiliaryRateLimitedError(str(exc)) from exc
        except BilibiliWebTransportError as exc:
            raise BilibiliAuxiliaryTransportError(str(exc)) from exc
        except BilibiliWebResponseError as exc:
            raise BilibiliAuxiliaryResponseError(str(exc)) from exc

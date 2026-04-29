from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import (
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.crawler.bilibili_metadata import (
    BilibiliMetadataNotFoundError,
    BilibiliMetadataProvider,
    BilibiliMetadataRateLimitedError,
    BilibiliMetadataResponseError,
    BilibiliMetadataTransportError,
    BilibiliUploaderMetadata,
    BilibiliVideoMetadata,
    BilibiliVideoPageMetadata,
    BilibiliVideoStatMetadata,
)
from app.crawler.bilibili_web import (
    BilibiliWebClient,
    BilibiliWebNotFoundError,
    BilibiliWebRateLimitedError,
    BilibiliWebResponseError,
    BilibiliWebTransportError,
)

logger = logging.getLogger(__name__)


def _coerce_int(value: Any) -> int | None:
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


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _coerce_datetime(value: Any) -> datetime | None:
    timestamp = _coerce_int(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


class BilibiliHttpMetadataProvider(BilibiliMetadataProvider):
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
        self._retry_attempts = retry_attempts or settings.BILIBILI_METADATA_RETRY_ATTEMPTS

    def close(self) -> None:
        self._web_client.close()

    def fetch_video_metadata(self, *, bvid: str) -> BilibiliVideoMetadata:
        video_referer = f"https://www.bilibili.com/video/{bvid}/"
        view_context = f"view metadata for {bvid}"
        try:
            view_payload = self._request_json(
                "/x/web-interface/wbi/view",
                params={"bvid": bvid},
                context=view_context,
                use_wbi=True,
                referer=video_referer,
            )
        except (
            BilibiliMetadataNotFoundError,
            BilibiliMetadataResponseError,
        ) as exc:
            logger.warning(
                "Falling back to legacy Bilibili view endpoint for %s: %s",
                bvid,
                exc,
            )
            view_payload = self._request_json(
                "/x/web-interface/view",
                params={"bvid": bvid},
                context=view_context,
                referer=video_referer,
            )
        if not isinstance(view_payload, dict):
            raise BilibiliMetadataResponseError(
                f"Bilibili metadata source returned an invalid view payload for {bvid}"
            )

        tags_payload = self._request_optional_json(
            "/x/tag/archive/tags",
            params={"bvid": bvid},
            context=f"tag metadata for {bvid}",
            referer=video_referer,
        )
        tags = self._parse_tags(tags_payload)

        pages_payload = view_payload.get("pages", [])
        if not isinstance(pages_payload, list):
            raise BilibiliMetadataResponseError(
                f"Bilibili metadata source returned invalid page data for {bvid}"
            )

        title = _coerce_str(view_payload.get("title"))
        if title is None:
            raise BilibiliMetadataResponseError(
                f"Bilibili metadata source did not return a title for {bvid}"
            )

        owner = self._parse_owner(view_payload.get("owner"))

        return BilibiliVideoMetadata(
            bvid=_coerce_str(view_payload.get("bvid")) or bvid,
            aid=_coerce_int(view_payload.get("aid")),
            title=title,
            description=_coerce_str(view_payload.get("desc")),
            duration_seconds=_coerce_int(view_payload.get("duration")),
            pubdate=_coerce_datetime(view_payload.get("pubdate")),
            owner=owner,
            cover_url=_coerce_str(view_payload.get("pic")),
            category=_coerce_str(view_payload.get("tname")),
            tags=tags,
            stat=self._parse_stat(view_payload.get("stat")),
            pages=self._parse_pages(pages_payload, bvid=bvid),
            raw={"view": view_payload, "tags": tags_payload or []},
        )

    def _request_optional_json(
        self,
        path: str,
        *,
        params: dict[str, Any],
        context: str,
        referer: str | None = None,
    ) -> Any | None:
        try:
            return self._request_json(
                path,
                params=params,
                context=context,
                referer=referer,
            )
        except (
            BilibiliMetadataNotFoundError,
            BilibiliMetadataRateLimitedError,
            BilibiliMetadataResponseError,
            BilibiliMetadataTransportError,
        ) as exc:
            logger.warning("Skipping optional %s: %s", context, exc)
            return None

    def _request_json(
        self,
        path: str,
        *,
        params: dict[str, Any],
        context: str,
        use_wbi: bool = False,
        referer: str | None = None,
    ) -> Any:
        return self._call_with_retry(
            lambda: self._request_json_once(
                path,
                params=params,
                context=context,
                use_wbi=use_wbi,
                referer=referer,
            )
        )

    def _call_with_retry(self, func: Callable[[], Any]) -> Any:
        for attempt in Retrying(
            stop=stop_after_attempt(self._retry_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type(
                (
                    BilibiliMetadataRateLimitedError,
                    BilibiliMetadataTransportError,
                )
            ),
            reraise=True,
        ):
            with attempt:
                return func()
        raise AssertionError("Retry loop exited unexpectedly")

    def _request_json_once(
        self,
        path: str,
        *,
        params: dict[str, Any],
        context: str,
        use_wbi: bool,
        referer: str | None,
    ) -> Any:
        try:
            return self._web_client.request_json(
                path,
                params=params,
                context=context,
                use_wbi=use_wbi,
                referer=referer,
            )
        except BilibiliWebNotFoundError as exc:
            raise BilibiliMetadataNotFoundError(str(exc)) from exc
        except BilibiliWebRateLimitedError as exc:
            raise BilibiliMetadataRateLimitedError(str(exc)) from exc
        except BilibiliWebTransportError as exc:
            raise BilibiliMetadataTransportError(str(exc)) from exc
        except BilibiliWebResponseError as exc:
            raise BilibiliMetadataResponseError(str(exc)) from exc

    def _parse_owner(self, payload: Any) -> BilibiliUploaderMetadata | None:
        if not isinstance(payload, dict):
            return None

        mid = _coerce_int(payload.get("mid"))
        if mid is None:
            return None

        return BilibiliUploaderMetadata(
            mid=mid,
            name=_coerce_str(payload.get("name")),
            avatar_url=_coerce_str(payload.get("face")),
            raw=payload,
        )

    def _parse_pages(
        self, payload: list[Any], *, bvid: str
    ) -> list[BilibiliVideoPageMetadata]:
        pages: list[BilibiliVideoPageMetadata] = []
        for item in payload:
            if not isinstance(item, dict):
                raise BilibiliMetadataResponseError(
                    f"Bilibili page payload was invalid for {bvid}"
                )

            cid = _coerce_int(item.get("cid"))
            page_no = _coerce_int(item.get("page"))
            if cid is None or page_no is None:
                raise BilibiliMetadataResponseError(
                    f"Bilibili page payload was missing cid/page for {bvid}"
                )

            pages.append(
                BilibiliVideoPageMetadata(
                    cid=cid,
                    page_no=page_no,
                    part_title=_coerce_str(item.get("part")),
                    duration_seconds=_coerce_int(item.get("duration")),
                    raw=item,
                )
            )
        return pages

    def _parse_stat(self, payload: Any) -> BilibiliVideoStatMetadata:
        if not isinstance(payload, dict):
            return BilibiliVideoStatMetadata()

        return BilibiliVideoStatMetadata(
            view_count=_coerce_int(payload.get("view")),
            like_count=_coerce_int(payload.get("like")),
            coin_count=_coerce_int(payload.get("coin")),
            favorite_count=_coerce_int(payload.get("favorite")),
            reply_count=_coerce_int(payload.get("reply")),
            share_count=_coerce_int(payload.get("share")),
            danmaku_count=_coerce_int(payload.get("danmaku")),
        )

    def _parse_tags(self, payload: Any) -> list[str]:
        items: list[Any]
        if payload is None:
            return []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            tags_payload = payload.get("tags") or payload.get("list") or []
            if not isinstance(tags_payload, list):
                return []
            items = tags_payload
        else:
            return []

        tags: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            name = _coerce_str(item.get("tag_name"))
            if name is None or name in seen:
                continue
            seen.add(name)
            tags.append(name)
        return tags

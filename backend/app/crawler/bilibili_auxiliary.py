from __future__ import annotations

from datetime import date, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class BilibiliAuxiliaryError(Exception):
    error_code = "auxiliary_fetch_failed"


class BilibiliAuxiliaryNotFoundError(BilibiliAuxiliaryError):
    error_code = "auxiliary_not_found"


class BilibiliAuxiliaryRateLimitedError(BilibiliAuxiliaryError):
    error_code = "auxiliary_rate_limited"


class BilibiliAuxiliaryTransportError(BilibiliAuxiliaryError):
    error_code = "auxiliary_source_unavailable"


class BilibiliAuxiliaryResponseError(BilibiliAuxiliaryError):
    error_code = "auxiliary_invalid_response"


class BilibiliCommentImageMetadata(BaseModel):
    source_url: str = Field(min_length=1, max_length=1024)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)
    raw: dict[str, Any] = Field(default_factory=dict)


class BilibiliCommentMetadata(BaseModel):
    rpid: int = Field(ge=1)
    oid: int | None = Field(default=None, ge=1)
    mid: int | None = Field(default=None, ge=1)
    uname: str | None = Field(default=None, max_length=255)
    root: int | None = Field(default=None, ge=1)
    parent: int | None = Field(default=None, ge=1)
    message: str | None = None
    like_count: int | None = Field(default=None, ge=0)
    reply_count: int | None = Field(default=None, ge=0)
    ctime: datetime | None = None
    images: list[BilibiliCommentImageMetadata] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class BilibiliCommentFetchSummary(BaseModel):
    expected_count: int | None = Field(default=None, ge=0)
    fetched_count: int = Field(default=0, ge=0)
    fallback_used: bool = False
    partial: bool = False


class BilibiliCommentFetchResult(BaseModel):
    comments: list[BilibiliCommentMetadata] = Field(default_factory=list)
    summary: BilibiliCommentFetchSummary = Field(
        default_factory=BilibiliCommentFetchSummary
    )


class BilibiliDanmakuMetadata(BaseModel):
    id: int | None = Field(default=None, ge=1)
    cid: int = Field(ge=1)
    time_offset_seconds: float | None = Field(default=None, ge=0)
    mode: int | None = Field(default=None, ge=0)
    font_size: int | None = Field(default=None, ge=0)
    color: int | None = Field(default=None, ge=0)
    content: str | None = None
    sent_at: datetime | None = None
    source: str = Field(default="snapshot_xml", max_length=32)
    history_date: date | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class BilibiliDanmakuFetchSummary(BaseModel):
    source: str = Field(default="snapshot_xml", max_length=32)
    history_used: bool = False
    snapshot_used: bool = False
    indexed_month_count: int = Field(default=0, ge=0)
    expected_days_count: int | None = Field(default=None, ge=0)
    fetched_days_count: int = Field(default=0, ge=0)
    partial: bool = False


class BilibiliDanmakuFetchResult(BaseModel):
    entries: list[BilibiliDanmakuMetadata] = Field(default_factory=list)
    summary: BilibiliDanmakuFetchSummary = Field(
        default_factory=BilibiliDanmakuFetchSummary
    )


class BilibiliSubtitleMetadata(BaseModel):
    cid: int = Field(ge=1)
    lang: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=64)
    content: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class BilibiliAuxiliaryProvider(Protocol):
    def fetch_video_comments(
        self,
        *,
        bvid: str,
        aid: int,
    ) -> BilibiliCommentFetchResult:
        ...

    def fetch_video_danmaku(
        self,
        *,
        bvid: str,
        cid: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BilibiliDanmakuFetchResult:
        ...

    def fetch_video_subtitles(
        self,
        *,
        bvid: str,
        cid: int,
    ) -> list[BilibiliSubtitleMetadata]:
        ...

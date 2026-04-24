from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class BilibiliMetadataError(Exception):
    error_code = "metadata_fetch_failed"


class BilibiliMetadataNotFoundError(BilibiliMetadataError):
    error_code = "metadata_not_found"


class BilibiliMetadataRateLimitedError(BilibiliMetadataError):
    error_code = "metadata_rate_limited"


class BilibiliMetadataTransportError(BilibiliMetadataError):
    error_code = "metadata_source_unavailable"


class BilibiliMetadataResponseError(BilibiliMetadataError):
    error_code = "metadata_invalid_response"


class BilibiliUploaderMetadata(BaseModel):
    mid: int
    name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=1024)
    raw: dict[str, Any] = Field(default_factory=dict)


class BilibiliVideoPageMetadata(BaseModel):
    cid: int
    page_no: int = Field(ge=1)
    part_title: str | None = Field(default=None, max_length=512)
    duration_seconds: int | None = Field(default=None, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)


class BilibiliVideoStatMetadata(BaseModel):
    view_count: int | None = Field(default=None, ge=0)
    like_count: int | None = Field(default=None, ge=0)
    coin_count: int | None = Field(default=None, ge=0)
    favorite_count: int | None = Field(default=None, ge=0)
    reply_count: int | None = Field(default=None, ge=0)
    share_count: int | None = Field(default=None, ge=0)
    danmaku_count: int | None = Field(default=None, ge=0)

    def as_dict(self) -> dict[str, int]:
        return self.model_dump(exclude_none=True)


class BilibiliVideoMetadata(BaseModel):
    bvid: str = Field(min_length=12, max_length=32)
    aid: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    pubdate: datetime | None = None
    owner: BilibiliUploaderMetadata | None = None
    cover_url: str | None = Field(default=None, max_length=1024)
    category: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(default_factory=list)
    stat: BilibiliVideoStatMetadata = Field(default_factory=BilibiliVideoStatMetadata)
    pages: list[BilibiliVideoPageMetadata] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class BilibiliMetadataProvider(Protocol):
    def fetch_video_metadata(self, *, bvid: str) -> BilibiliVideoMetadata:
        ...

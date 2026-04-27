import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, BigInteger, Column, Date, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel


def get_datetime_utc() -> datetime:
    return datetime.now(timezone.utc)


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    actor: str | None = Field(default=None, max_length=255, index=True)
    action: str = Field(max_length=128, index=True)
    resource_type: str = Field(max_length=128, index=True)
    resource_id: str | None = Field(default=None, max_length=255, index=True)
    message: str | None = Field(default=None)
    payload: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        index=True,
    )


class Uploader(SQLModel, table=True):
    __tablename__ = "uploaders"

    mid: int = Field(primary_key=True, sa_type=BigInteger)
    name: str | None = Field(default=None, max_length=255)
    avatar_url: str | None = Field(default=None, max_length=1024)
    avatar_s3_key: str | None = Field(default=None, max_length=1024)
    avatar_asset_id: uuid.UUID | None = Field(default=None, index=True)
    raw: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    first_seen_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    last_seen_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class Video(SQLModel, table=True):
    __tablename__ = "videos"

    bvid: str = Field(primary_key=True, max_length=32)
    aid: int | None = Field(default=None, sa_type=BigInteger, unique=True, index=True)
    title: str = Field(max_length=512)
    description: str | None = Field(default=None)
    duration_seconds: int | None = Field(default=None)
    pubdate: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        index=True,
    )
    owner_mid: int | None = Field(
        default=None,
        foreign_key="uploaders.mid",
        sa_type=BigInteger,
        index=True,
    )
    owner_name: str | None = Field(default=None, max_length=255)
    cover_url: str | None = Field(default=None, max_length=1024)
    cover_s3_key: str | None = Field(default=None, max_length=1024)
    cover_asset_id: uuid.UUID | None = Field(default=None, index=True)
    category: str | None = Field(default=None, max_length=255)
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    stat: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    raw: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    takedown_status: str = Field(default="active", max_length=32, index=True)
    first_seen_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    last_crawled_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class VideoPage(SQLModel, table=True):
    __tablename__ = "video_pages"
    __table_args__ = (
        UniqueConstraint("bvid", "cid", name="uq_video_pages_bvid_cid"),
        UniqueConstraint("bvid", "page_no", name="uq_video_pages_bvid_page_no"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    bvid: str = Field(foreign_key="videos.bvid", max_length=32, index=True)
    aid: int | None = Field(default=None, sa_type=BigInteger)
    cid: int = Field(sa_type=BigInteger, index=True)
    page_no: int = Field(index=True)
    part_title: str | None = Field(default=None, max_length=512)
    duration_seconds: int | None = Field(default=None)
    raw: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class IngestJob(SQLModel, table=True):
    __tablename__ = "ingest_jobs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    input_text: str = Field()
    normalized_bvid: str | None = Field(default=None, max_length=32, index=True)
    requested_by: str | None = Field(default=None, max_length=255, index=True)
    status: str = Field(default="pending", max_length=64, index=True)
    phase: str | None = Field(default=None, max_length=255)
    priority: int = Field(default=100)
    options: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    progress: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    idempotency_key: str | None = Field(default=None, max_length=128, unique=True)
    error_code: str | None = Field(default=None, max_length=128)
    error_message: str | None = Field(default=None)
    retry_count: int = Field(default=0)
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        index=True,
    )
    started_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    finished_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class MediaAsset(SQLModel, table=True):
    __tablename__ = "media_assets"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    bvid: str = Field(foreign_key="videos.bvid", max_length=32, index=True)
    cid: int | None = Field(default=None, sa_type=BigInteger, index=True)
    job_id: uuid.UUID | None = Field(default=None, foreign_key="ingest_jobs.id")
    asset_type: str = Field(max_length=64, index=True)
    variant: str | None = Field(default=None, max_length=128)
    status: str = Field(default="pending", max_length=32, index=True)
    s3_bucket: str | None = Field(default=None, max_length=255)
    s3_key: str | None = Field(default=None, max_length=1024)
    s3_region: str | None = Field(default=None, max_length=255)
    storage_class: str | None = Field(default=None, max_length=255)
    original_url_hash: str | None = Field(default=None, max_length=128)
    filename: str | None = Field(default=None, max_length=512)
    content_type: str | None = Field(default=None, max_length=255)
    container_format: str | None = Field(default=None, max_length=64)
    video_codec: str | None = Field(default=None, max_length=64)
    audio_codec: str | None = Field(default=None, max_length=64)
    width: int | None = Field(default=None)
    height: int | None = Field(default=None)
    fps: float | None = Field(default=None)
    bitrate: int | None = Field(default=None, sa_type=BigInteger)
    duration_seconds: float | None = Field(default=None)
    size_bytes: int | None = Field(default=None, sa_type=BigInteger)
    sha256: str | None = Field(default=None, max_length=64)
    etag: str | None = Field(default=None, max_length=255)
    metadata_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column("metadata", JSON, nullable=False),
    )
    created_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    ready_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    deleted_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class VideoStatSnapshot(SQLModel, table=True):
    __tablename__ = "video_stat_snapshots"

    id: int | None = Field(default=None, primary_key=True)
    bvid: str = Field(foreign_key="videos.bvid", max_length=32, index=True)
    view_count: int | None = Field(default=None, sa_type=BigInteger)
    like_count: int | None = Field(default=None, sa_type=BigInteger)
    coin_count: int | None = Field(default=None, sa_type=BigInteger)
    favorite_count: int | None = Field(default=None, sa_type=BigInteger)
    reply_count: int | None = Field(default=None, sa_type=BigInteger)
    share_count: int | None = Field(default=None, sa_type=BigInteger)
    danmaku_count: int | None = Field(default=None, sa_type=BigInteger)
    crawled_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        index=True,
    )


class VideoSubtitle(SQLModel, table=True):
    __tablename__ = "video_subtitles"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    bvid: str = Field(foreign_key="videos.bvid", max_length=32, index=True)
    cid: int | None = Field(default=None, sa_type=BigInteger, index=True)
    lang: str | None = Field(default=None, max_length=32)
    source: str | None = Field(default=None, max_length=64)
    content: str | None = Field(default=None)
    raw: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    asset_id: uuid.UUID | None = Field(default=None, foreign_key="media_assets.id")
    crawled_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class VideoComment(SQLModel, table=True):
    __tablename__ = "video_comments"
    __table_args__ = (
        UniqueConstraint("bvid", "rpid", name="uq_video_comments_bvid_rpid"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    rpid: int = Field(sa_type=BigInteger)
    bvid: str = Field(foreign_key="videos.bvid", max_length=32, index=True)
    oid: int | None = Field(default=None, sa_type=BigInteger)
    mid: int | None = Field(default=None, sa_type=BigInteger, index=True)
    uname: str | None = Field(default=None, max_length=255)
    root: int | None = Field(default=None, sa_type=BigInteger)
    parent: int | None = Field(default=None, sa_type=BigInteger)
    message: str | None = Field(default=None)
    like_count: int | None = Field(default=None, sa_type=BigInteger)
    reply_count: int | None = Field(default=None, sa_type=BigInteger)
    ctime: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        index=True,
    )
    raw: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    crawled_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class VideoCommentImage(SQLModel, table=True):
    __tablename__ = "video_comment_images"
    __table_args__ = (
        UniqueConstraint(
            "comment_id",
            "ordinal",
            name="uq_video_comment_images_comment_id_ordinal",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    comment_id: uuid.UUID = Field(
        foreign_key="video_comments.id",
        index=True,
    )
    rpid: int = Field(
        sa_type=BigInteger,
        index=True,
    )
    bvid: str = Field(foreign_key="videos.bvid", max_length=32, index=True)
    ordinal: int = Field(ge=0)
    source_url: str | None = Field(default=None, max_length=1024)
    width: int | None = Field(default=None)
    height: int | None = Field(default=None)
    asset_id: uuid.UUID | None = Field(default=None, foreign_key="media_assets.id")
    storage_status: str = Field(default="skipped", max_length=32)
    error_message: str | None = Field(default=None)
    raw: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    crawled_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
        index=True,
    )


class VideoDanmaku(SQLModel, table=True):
    __tablename__ = "video_danmaku"
    __table_args__ = (
        UniqueConstraint(
            "bvid",
            "cid",
            "danmaku_id",
            name="uq_video_danmaku_bvid_cid_danmaku_id",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    danmaku_id: int | None = Field(default=None, sa_type=BigInteger, index=True)
    bvid: str = Field(foreign_key="videos.bvid", max_length=32, index=True)
    cid: int = Field(sa_type=BigInteger, index=True)
    time_offset_seconds: float | None = Field(default=None)
    mode: int | None = Field(default=None)
    font_size: int | None = Field(default=None)
    color: int | None = Field(default=None)
    content: str | None = Field(default=None)
    sent_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )
    source: str = Field(default="snapshot_xml", max_length=32, index=True)
    history_date: date | None = Field(default=None, sa_type=Date(), index=True)
    raw: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False),
    )
    crawled_at: datetime = Field(
        default_factory=get_datetime_utc,
        sa_type=DateTime(timezone=True),  # type: ignore[arg-type]
    )


class IngestVideoOptions(SQLModel):
    download_video: bool = False
    max_height: int | None = Field(default=None, ge=144, le=4320)
    store_source_archive: bool = True
    create_normalized_mp4: bool = True
    create_hls: bool = False
    fetch_comments: bool = False
    fetch_danmaku: bool = False
    fetch_subtitles: bool = True
    transcribe_subtitles: bool = True
    force_refresh: bool = False


class IngestVideoRequest(SQLModel):
    input: str = Field(min_length=1, max_length=2048)
    options: IngestVideoOptions = Field(default_factory=IngestVideoOptions)


class IngestJobPublic(SQLModel):
    job_id: uuid.UUID
    bvid: str | None = None
    status: str
    phase: str | None = None


class JobErrorPublic(SQLModel):
    code: str | None = None
    message: str | None = None


class IngestJobDetail(IngestJobPublic):
    requested_by: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    error: JobErrorPublic | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IngestJobSummaryPublic(IngestJobPublic):
    requested_by: str | None = None
    error: JobErrorPublic | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class IngestJobsPublic(SQLModel):
    data: list[IngestJobSummaryPublic]
    count: int
    limit: int
    offset: int


class MediaAssetPublic(SQLModel):
    asset_id: uuid.UUID
    asset_type: str
    variant: str | None = None
    status: str
    cid: int | None = None
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    container_format: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    created_at: datetime
    ready_at: datetime | None = None


class MediaAssetDetailPublic(MediaAssetPublic):
    bvid: str
    s3_bucket: str | None = None
    s3_key: str | None = None


class VideoAssetsPublic(SQLModel):
    bvid: str
    assets: list[MediaAssetPublic]


class SubtitleTranscriptionRequest(SQLModel):
    cid: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1, le=200)
    replace_existing_ready: bool = False


class VideoCommentImagePublic(SQLModel):
    source_url: str | None = None
    width: int | None = None
    height: int | None = None
    asset_id: uuid.UUID | None = None
    storage_status: str
    error_message: str | None = None
    asset: MediaAssetPublic | None = None


class VideoCommentPublic(SQLModel):
    rpid: int
    oid: int | None = None
    mid: int | None = None
    uname: str | None = None
    root: int | None = None
    parent: int | None = None
    message: str | None = None
    like_count: int | None = None
    reply_count: int | None = None
    ctime: datetime | None = None
    images: list[VideoCommentImagePublic] = Field(default_factory=list)


class VideoCommentContextPublic(SQLModel):
    rpid: int
    oid: int | None = None
    mid: int | None = None
    uname: str | None = None
    root: int | None = None
    parent: int | None = None
    message: str | None = None
    like_count: int | None = None
    reply_count: int | None = None
    ctime: datetime | None = None


class AuxiliarySourceJobPublic(SQLModel):
    job_id: uuid.UUID
    status: str
    phase: str | None = None
    crawled_at: datetime | None = None


class VideoCommentsCompletenessPublic(SQLModel):
    partial: bool | None = None
    expected_count: int | None = None
    fetched_count: int | None = None
    stored_count: int | None = None
    fallback_used: bool | None = None
    image_count: int | None = None
    stored_image_count: int | None = None
    failed_image_count: int | None = None
    skipped_image_count: int | None = None
    source_job: AuxiliarySourceJobPublic


class VideoCommentsPublic(SQLModel):
    bvid: str
    count: int
    thread_count: int | None = None
    limit: int
    offset: int
    completeness: VideoCommentsCompletenessPublic | None = None
    comments: list[VideoCommentPublic]


class VideoCommentImageEntryPublic(VideoCommentImagePublic):
    image_id: uuid.UUID
    ordinal: int
    crawled_at: datetime
    comment: VideoCommentContextPublic


class VideoCommentImagesPublic(SQLModel):
    bvid: str
    count: int
    limit: int
    offset: int
    completeness: VideoCommentsCompletenessPublic | None = None
    images: list[VideoCommentImageEntryPublic]


class VideoDanmakuEntryPublic(SQLModel):
    danmaku_id: int | None = None
    cid: int
    time_offset_seconds: float | None = None
    mode: int | None = None
    font_size: int | None = None
    color: int | None = None
    content: str | None = None
    sent_at: datetime | None = None
    source: str
    history_date: date | None = None


class VideoDanmakuPageCoveragePublic(SQLModel):
    cid: int
    count: int | None = None
    source: str | None = None
    history_used: bool | None = None
    snapshot_used: bool | None = None
    indexed_month_count: int | None = None
    expected_days_count: int | None = None
    fetched_days_count: int | None = None
    partial: bool | None = None


class VideoDanmakuCompletenessPublic(SQLModel):
    partial: bool | None = None
    stored_count: int | None = None
    duplicate_count: int | None = None
    cid_count: int | None = None
    filled_cid_count: int | None = None
    crawl_source: str | None = None
    history_used: bool | None = None
    snapshot_used: bool | None = None
    indexed_month_count: int | None = None
    expected_days_count: int | None = None
    fetched_days_count: int | None = None
    pages: list[VideoDanmakuPageCoveragePublic] = Field(default_factory=list)
    source_job: AuxiliarySourceJobPublic


class VideoDanmakuEntriesPublic(SQLModel):
    bvid: str
    count: int
    limit: int
    offset: int
    completeness: VideoDanmakuCompletenessPublic | None = None
    danmaku: list[VideoDanmakuEntryPublic]


class VideoSubtitlePublic(SQLModel):
    subtitle_id: uuid.UUID
    cid: int | None = None
    lang: str | None = None
    source: str | None = None
    content: str | None = None
    asset_id: uuid.UUID | None = None
    crawled_at: datetime


class VideoSubtitlesCompletenessPublic(SQLModel):
    partial: bool | None = None
    stored_count: int | None = None
    cid_count: int | None = None
    languages: list[str] = Field(default_factory=list)
    source_job: AuxiliarySourceJobPublic


class VideoSubtitlesPublic(SQLModel):
    bvid: str
    count: int
    limit: int
    offset: int
    completeness: VideoSubtitlesCompletenessPublic | None = None
    subtitles: list[VideoSubtitlePublic]


class VideoSummaryPublic(SQLModel):
    bvid: str
    aid: int | None = None
    title: str
    owner_mid: int | None = None
    owner_name: str | None = None
    duration_seconds: int | None = None
    pubdate: datetime | None = None
    category: str | None = None
    cover_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    takedown_status: str
    last_crawled_at: datetime | None = None


class VideoDetailPublic(VideoSummaryPublic):
    description: str | None = None
    stat: dict[str, Any] = Field(default_factory=dict)


class VideosPublic(SQLModel):
    data: list[VideoSummaryPublic]
    count: int
    limit: int
    offset: int


class SignedUrlRequest(SQLModel):
    expires_in: int = Field(default=900, ge=60, le=86400)


class SignedUrlResponse(SQLModel):
    url: str
    expires_in: int


class MediaAssetDownloadDescriptor(SQLModel):
    asset_id: uuid.UUID
    bvid: str
    s3_bucket: str | None = None
    s3_key: str | None = None
    filename: str | None = None
    content_type: str | None = None
    expires_at: datetime

import hashlib
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.crawler.bilibili_auxiliary import (
    BilibiliAuxiliaryResponseError,
    BilibiliCommentFetchResult,
    BilibiliCommentFetchSummary,
    BilibiliCommentImageMetadata,
    BilibiliCommentMetadata,
    BilibiliDanmakuFetchResult,
    BilibiliDanmakuFetchSummary,
    BilibiliDanmakuMetadata,
    BilibiliSubtitleMetadata,
)
from app.crawler.bilibili_metadata import (
    BilibiliMetadataNotFoundError,
    BilibiliUploaderMetadata,
    BilibiliVideoMetadata,
    BilibiliVideoPageMetadata,
    BilibiliVideoStatMetadata,
)
from app.crawler.bilibili_web import BilibiliWebClient
from app.ingest_models import (
    IngestJob,
    MediaAsset,
    Uploader,
    Video,
    VideoComment,
    VideoCommentImage,
    VideoDanmaku,
    VideoPage,
    VideoStatSnapshot,
    VideoSubtitle,
)
from app.services.metadata_ingest import process_metadata_ingest_job
from app.uploader.base import ObjectStorageResult
from app.workers.metadata_ingest import process_next_metadata_ingest_job
from tests.utils.utils import random_bvid


class StaticMetadataProvider:
    def __init__(self, metadata: BilibiliVideoMetadata) -> None:
        self.metadata = metadata

    def fetch_video_metadata(self, *, bvid: str) -> BilibiliVideoMetadata:
        assert bvid == self.metadata.bvid
        return self.metadata


class StaticAuxiliaryProvider:
    def __init__(
        self,
        *,
        comments: list[BilibiliCommentMetadata] | None = None,
        comment_summary: BilibiliCommentFetchSummary | None = None,
        danmaku_by_cid: dict[int, list[BilibiliDanmakuMetadata]] | None = None,
        danmaku_summary_by_cid: dict[int, BilibiliDanmakuFetchSummary] | None = None,
        subtitles_by_cid: dict[int, list[BilibiliSubtitleMetadata]] | None = None,
    ) -> None:
        self.comments = comments or []
        self.comment_summary = comment_summary
        self.danmaku_by_cid = danmaku_by_cid or {}
        self.danmaku_summary_by_cid = danmaku_summary_by_cid or {}
        self.subtitles_by_cid = subtitles_by_cid or {}

    def fetch_video_comments(
        self,
        *,
        bvid: str,
        aid: int,
    ) -> BilibiliCommentFetchResult:
        del bvid, aid
        return BilibiliCommentFetchResult(
            comments=self.comments,
            summary=self.comment_summary
            or BilibiliCommentFetchSummary(fetched_count=len(self.comments)),
        )

    def fetch_video_danmaku(
        self,
        *,
        bvid: str,
        cid: int,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BilibiliDanmakuFetchResult:
        del bvid
        del start_date, end_date
        return BilibiliDanmakuFetchResult(
            entries=self.danmaku_by_cid.get(cid, []),
            summary=self.danmaku_summary_by_cid.get(
                cid,
                BilibiliDanmakuFetchSummary(
                    source="snapshot_xml",
                    history_used=False,
                    snapshot_used=bool(self.danmaku_by_cid.get(cid)),
                    indexed_month_count=0,
                    expected_days_count=0,
                    fetched_days_count=0,
                    partial=False,
                ),
            ),
        )

    def fetch_video_subtitles(
        self,
        *,
        bvid: str,
        cid: int,
    ) -> list[BilibiliSubtitleMetadata]:
        del bvid
        return self.subtitles_by_cid.get(cid, [])


class FailingAuxiliaryProvider(StaticAuxiliaryProvider):
    def fetch_video_comments(
        self,
        *,
        bvid: str,
        aid: int,
    ) -> BilibiliCommentFetchResult:
        del bvid, aid
        raise BilibiliAuxiliaryResponseError("comments payload was invalid")


class MissingMetadataProvider:
    def fetch_video_metadata(self, *, bvid: str) -> BilibiliVideoMetadata:
        raise BilibiliMetadataNotFoundError(f"{bvid} is not available on Bilibili")


class RecordingObjectStorageClient:
    def __init__(self, *, root_dir: Path) -> None:
        self.root_dir = root_dir

    def multipart_upload_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectStorageResult:
        del metadata
        remote_path = self.root_dir / bucket / key
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, remote_path)
        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=remote_path.stat().st_size,
            etag=hashlib.md5(remote_path.read_bytes()).hexdigest(),  # noqa: S324
            content_type=content_type,
        )


def asset_source_sha256(asset: MediaAsset) -> str | None:
    if asset.sha256:
        return asset.sha256
    source_sha256 = asset.metadata_json.get("source_sha256")
    if isinstance(source_sha256, str) and source_sha256.strip():
        return source_sha256
    return None


def build_metadata(
    *,
    bvid: str,
    title: str = "Test video",
    pages: list[BilibiliVideoPageMetadata] | None = None,
) -> BilibiliVideoMetadata:
    aid = int.from_bytes(bvid.encode("utf-8"), "little") % 2_000_000_000 + 1
    return BilibiliVideoMetadata(
        bvid=bvid,
        aid=aid,
        title=title,
        description="normalized metadata payload",
        duration_seconds=330,
        pubdate=datetime(2025, 1, 2, tzinfo=timezone.utc),
        owner=BilibiliUploaderMetadata(
            mid=42,
            name="Uploader 42",
            avatar_url="https://example.com/avatar.jpg",
            raw={"mid": 42, "face": "https://example.com/avatar.jpg"},
        ),
        cover_url="https://example.com/cover.jpg",
        category="tech",
        tags=["tech", "internal-archive", "tech"],
        stat=BilibiliVideoStatMetadata(
            view_count=1200,
            like_count=45,
            reply_count=12,
            danmaku_count=3,
        ),
        pages=pages
        or [
            BilibiliVideoPageMetadata(
                cid=101,
                page_no=1,
                part_title="Part 1",
                duration_seconds=120,
                raw={"cid": 101},
            ),
            BilibiliVideoPageMetadata(
                cid=202,
                page_no=2,
                part_title="Part 2",
                duration_seconds=210,
                raw={"cid": 202},
            ),
        ],
        raw={
            "source": "static-fixture",
            "pic": "https://example.com/cover.jpg",
            "owner": {"face": "https://example.com/avatar.jpg"},
        },
    )


def create_job(
    db: Session,
    *,
    bvid: str,
    download_video: bool,
    status: str = "pending",
    options: dict[str, object] | None = None,
) -> IngestJob:
    resolved_options = {"download_video": download_video}
    if options:
        resolved_options.update(options)
    job = IngestJob(
        input_text=bvid,
        normalized_bvid=bvid,
        requested_by="tester@example.com",
        status=status,
        phase="queued for metadata ingestion",
        options=resolved_options,
        progress={},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_process_metadata_ingest_job_persists_video_records(db: Session) -> None:
    bvid = random_bvid()
    job = create_job(db, bvid=bvid, download_video=False)

    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
    )

    assert processed_job.status == "metadata_ready", processed_job.error_message
    assert processed_job.phase == "metadata stored; video download not requested"
    assert processed_job.finished_at is not None
    assert processed_job.progress["next_step"] == "job_complete"

    video = db.get(Video, bvid)
    assert video is not None
    assert video.title == "Test video"
    assert video.owner_mid == 42
    assert video.cover_url is None
    assert video.cover_asset_id is None
    assert video.tags == ["tech", "internal-archive"]
    assert "pic" not in video.raw
    assert "face" not in str(video.raw)

    uploader = db.get(Uploader, 42)
    assert uploader is not None
    assert uploader.avatar_url is None
    assert uploader.avatar_asset_id is None
    assert "face" not in uploader.raw

    pages = list(
        db.exec(
            select(VideoPage)
            .where(VideoPage.bvid == bvid)
            .order_by(VideoPage.page_no)
        ).all()
    )
    assert [page.cid for page in pages] == [101, 202]

    snapshots = list(
        db.exec(select(VideoStatSnapshot).where(VideoStatSnapshot.bvid == bvid)).all()
    )
    assert len(snapshots) == 1
    assert snapshots[0].view_count == 1200


def test_process_metadata_ingest_job_persists_requested_auxiliary_records(
    db: Session,
) -> None:
    bvid = random_bvid()
    job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={
            "fetch_comments": True,
            "fetch_danmaku": True,
            "fetch_subtitles": True,
        },
    )

    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            comments=[
                BilibiliCommentMetadata(
                    rpid=101,
                    oid=987654321,
                    mid=42,
                    uname="Uploader 42",
                    root=None,
                    parent=None,
                    message="Top level reply",
                    like_count=3,
                    reply_count=1,
                    ctime=datetime(2025, 1, 2, tzinfo=timezone.utc),
                    raw={"rpid": 101},
                )
            ],
            comment_summary=BilibiliCommentFetchSummary(
                expected_count=3,
                fetched_count=1,
                fallback_used=True,
                partial=True,
            ),
            danmaku_by_cid={
                101: [
                    BilibiliDanmakuMetadata(
                        id=1001,
                        cid=101,
                        time_offset_seconds=1.5,
                        mode=1,
                        font_size=25,
                        color=16777215,
                        content="Hello",
                        sent_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                        source="history_proto",
                        history_date=date(2025, 1, 2),
                        raw={"p": "1.5,1,25,16777215,1735776000,0,hash-a,1001"},
                    )
                ]
            },
            danmaku_summary_by_cid={
                101: BilibiliDanmakuFetchSummary(
                    source="history_proto",
                    history_used=True,
                    snapshot_used=False,
                    indexed_month_count=1,
                    expected_days_count=1,
                    fetched_days_count=1,
                    partial=False,
                ),
                202: BilibiliDanmakuFetchSummary(
                    source="history_proto",
                    history_used=True,
                    snapshot_used=False,
                    indexed_month_count=1,
                    expected_days_count=0,
                    fetched_days_count=0,
                    partial=False,
                ),
            },
            subtitles_by_cid={
                101: [
                    BilibiliSubtitleMetadata(
                        cid=101,
                        lang="zh-CN",
                        source="bilibili_player_v2",
                        content="1\n00:00:00,000 --> 00:00:01,500\n第一句",
                        raw={"track": {"lan": "zh-CN"}},
                    )
                ]
            },
        ),
    )

    assert processed_job.status == "metadata_ready", processed_job.error_message
    assert processed_job.progress["auxiliary"]["requested"] == [
        "comments",
        "danmaku",
        "subtitles",
    ]
    assert processed_job.progress["auxiliary"]["comments"]["count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["expected_count"] == 3
    assert processed_job.progress["auxiliary"]["comments"]["fetched_count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["stored_count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["fallback_used"] is True
    assert processed_job.progress["auxiliary"]["comments"]["partial"] is True
    assert processed_job.progress["auxiliary"]["danmaku"]["count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["stored_count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["duplicate_count"] == 0
    assert processed_job.progress["auxiliary"]["danmaku"]["cid_count"] == 2
    assert processed_job.progress["auxiliary"]["danmaku"]["filled_cid_count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["source"] == "history_proto"
    assert processed_job.progress["auxiliary"]["danmaku"]["history_used"] is True
    assert processed_job.progress["auxiliary"]["danmaku"]["snapshot_used"] is False
    assert processed_job.progress["auxiliary"]["danmaku"]["indexed_month_count"] == 2
    assert processed_job.progress["auxiliary"]["danmaku"]["expected_days_count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["fetched_days_count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["partial"] is False
    assert processed_job.progress["auxiliary"]["subtitles"]["count"] == 1
    assert processed_job.progress["auxiliary"]["subtitles"]["languages"] == ["zh-CN"]

    comments = list(
        db.exec(select(VideoComment).where(VideoComment.bvid == bvid)).all()
    )
    assert len(comments) == 1
    assert comments[0].message == "Top level reply"

    danmaku_entries = list(
        db.exec(select(VideoDanmaku).where(VideoDanmaku.bvid == bvid)).all()
    )
    assert len(danmaku_entries) == 1
    assert danmaku_entries[0].content == "Hello"
    assert danmaku_entries[0].danmaku_id == 1001
    assert danmaku_entries[0].source == "history_proto"
    assert danmaku_entries[0].history_date == date(2025, 1, 2)

    subtitles = list(
        db.exec(select(VideoSubtitle).where(VideoSubtitle.bvid == bvid)).all()
    )
    assert len(subtitles) == 1
    assert subtitles[0].lang == "zh-CN"
    assert "第一句" in (subtitles[0].content or "")


def test_process_metadata_ingest_job_persists_and_uploads_comment_images(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bvid = random_bvid()
    job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_comments": True},
    )
    monkeypatch.setattr(settings, "S3_BUCKET", "bili-media-dev")
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_JITTER_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Referer"] == f"https://www.bilibili.com/video/{bvid}/"
        return httpx.Response(
            200,
            content=b"comment-image-binary",
            headers={"Content-Type": "image/jpeg"},
        )

    image_web_client = BilibiliWebClient(
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.bilibili.com",
        ),
        min_interval_seconds=0.0,
        request_jitter_seconds=0.0,
        cookie_header="SESSDATA=comment-image-test",
    )
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")

    try:
        processed_job = process_metadata_ingest_job(
            session=db,
            job_id=job.id,
            provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
            auxiliary_provider=StaticAuxiliaryProvider(
                comments=[
                    BilibiliCommentMetadata(
                        rpid=101,
                        oid=987654321,
                        mid=42,
                        uname="Uploader 42",
                        root=None,
                        parent=None,
                        message="Reply with image",
                        like_count=3,
                        reply_count=0,
                        ctime=datetime(2025, 1, 2, tzinfo=timezone.utc),
                        images=[
                            BilibiliCommentImageMetadata(
                                source_url="https://i0.hdslb.com/bfs/reply/example-1.jpg",
                                width=1280,
                                height=720,
                                raw={"img_src": "//i0.hdslb.com/bfs/reply/example-1.jpg"},
                            )
                        ],
                        raw={"rpid": 101},
                    )
                ]
            ),
            comment_image_web_client=image_web_client,
            comment_image_storage_client=storage_client,
        )
    finally:
        image_web_client.close()

    assert processed_job.progress["auxiliary"]["comments"]["image_count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["stored_image_count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["failed_image_count"] == 0
    assert processed_job.progress["auxiliary"]["comments"]["skipped_image_count"] == 0

    comment_images = list(
        db.exec(
            select(VideoCommentImage)
            .where(VideoCommentImage.bvid == bvid)
            .order_by(VideoCommentImage.ordinal)
        ).all()
    )
    assert len(comment_images) == 1
    assert comment_images[0].source_url is None
    assert comment_images[0].storage_status == "ready"
    assert comment_images[0].asset_id is not None
    assert "img_src" not in comment_images[0].raw

    asset = db.get(MediaAsset, comment_images[0].asset_id)
    assert asset is not None
    assert asset.asset_type == "comment_image"
    assert asset.status == "ready"
    assert asset.s3_key is not None
    assert (tmp_path / "remote" / "bili-media-dev" / asset.s3_key).is_file()

    video = db.get(Video, bvid)
    assert video is not None
    assert video.cover_asset_id is not None
    assert video.cover_url is None

    uploader = db.get(Uploader, 42)
    assert uploader is not None
    assert uploader.avatar_asset_id is not None
    assert uploader.avatar_url is None

    uploaded_assets = list(
        db.exec(
            select(MediaAsset)
            .where(MediaAsset.bvid == bvid)
            .order_by(MediaAsset.asset_type.asc(), MediaAsset.created_at.asc())
        ).all()
    )
    assert [asset.asset_type for asset in uploaded_assets] == [
        "avatar",
        "comment_image",
        "cover",
    ]


def test_process_metadata_ingest_job_reuses_existing_image_assets_without_repeating_sha256(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_bvid = random_bvid()
    second_bvid = random_bvid()
    first_job = create_job(db, bvid=first_bvid, download_video=False)
    second_job = create_job(db, bvid=second_bvid, download_video=False)
    monkeypatch.setattr(settings, "S3_BUCKET", "bili-media-dev")
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_MIN_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(settings, "BILIBILI_REQUEST_JITTER_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/cover.jpg"):
            return httpx.Response(
                200,
                content=b"shared-cover-binary",
                headers={"Content-Type": "image/jpeg"},
            )
        if request.url.path.endswith("/avatar.jpg"):
            return httpx.Response(
                200,
                content=b"shared-avatar-binary",
                headers={"Content-Type": "image/jpeg"},
            )
        raise AssertionError(f"Unexpected image request: {request.url}")

    image_web_client = BilibiliWebClient(
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://api.bilibili.com",
        ),
        min_interval_seconds=0.0,
        request_jitter_seconds=0.0,
        cookie_header="SESSDATA=image-reuse-test",
    )
    storage_client = RecordingObjectStorageClient(root_dir=tmp_path / "remote")

    try:
        first_processed_job = process_metadata_ingest_job(
            session=db,
            job_id=first_job.id,
            provider=StaticMetadataProvider(build_metadata(bvid=first_bvid)),
            comment_image_web_client=image_web_client,
            comment_image_storage_client=storage_client,
        )
        second_processed_job = process_metadata_ingest_job(
            session=db,
            job_id=second_job.id,
            provider=StaticMetadataProvider(build_metadata(bvid=second_bvid)),
            comment_image_web_client=image_web_client,
            comment_image_storage_client=storage_client,
        )
    finally:
        image_web_client.close()

    assert first_processed_job.status == "metadata_ready"
    assert second_processed_job.status == "metadata_ready"

    first_assets = {
        asset.asset_type: asset
        for asset in db.exec(
            select(MediaAsset)
            .where(MediaAsset.bvid == first_bvid)
            .order_by(MediaAsset.asset_type.asc())
        ).all()
    }
    second_assets = {
        asset.asset_type: asset
        for asset in db.exec(
            select(MediaAsset)
            .where(MediaAsset.bvid == second_bvid)
            .order_by(MediaAsset.asset_type.asc())
        ).all()
    }

    assert set(first_assets) == {"avatar", "cover"}
    assert set(second_assets) == {"avatar", "cover"}

    reused_avatar_asset = second_assets["avatar"]
    reused_cover_asset = second_assets["cover"]
    assert reused_avatar_asset.status == "ready"
    assert reused_cover_asset.status == "ready"
    assert reused_avatar_asset.s3_key == first_assets["avatar"].s3_key
    assert reused_cover_asset.s3_key == first_assets["cover"].s3_key
    assert reused_avatar_asset.sha256 is None
    assert reused_cover_asset.sha256 is None
    assert reused_avatar_asset.metadata_json["reused_from_asset_id"] == str(
        first_assets["avatar"].id
    )
    assert reused_cover_asset.metadata_json["reused_from_asset_id"] == str(
        first_assets["cover"].id
    )
    assert (
        reused_avatar_asset.metadata_json["source_sha256"]
        == asset_source_sha256(first_assets["avatar"])
    )
    assert (
        reused_cover_asset.metadata_json["source_sha256"]
        == asset_source_sha256(first_assets["cover"])
    )


def test_process_metadata_ingest_job_preserves_existing_comments_and_images_on_refresh(
    db: Session,
) -> None:
    bvid = random_bvid()
    first_job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_comments": True},
    )
    first_processed_job = process_metadata_ingest_job(
        session=db,
        job_id=first_job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            comments=[
                BilibiliCommentMetadata(
                    rpid=101,
                    message="Older reply",
                    images=[
                        BilibiliCommentImageMetadata(
                            source_url="https://i0.hdslb.com/bfs/reply/example-1.jpg",
                            width=1280,
                            height=720,
                            raw={"img_src": "//i0.hdslb.com/bfs/reply/example-1.jpg"},
                        )
                    ],
                    raw={"rpid": 101},
                )
            ]
        ),
    )

    second_job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_comments": True},
    )
    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=second_job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            comments=[
                BilibiliCommentMetadata(
                    rpid=202,
                    message="Newer reply",
                    raw={"rpid": 202},
                )
            ]
        ),
    )

    comments = list(
        db.exec(
            select(VideoComment)
            .where(VideoComment.bvid == bvid)
            .order_by(VideoComment.rpid.asc())
        ).all()
    )
    assert [comment.rpid for comment in comments] == [101, 202]
    assert [comment.message for comment in comments] == ["Older reply", "Newer reply"]

    comment_images = list(
        db.exec(
            select(VideoCommentImage)
            .where(VideoCommentImage.bvid == bvid)
            .order_by(VideoCommentImage.rpid.asc(), VideoCommentImage.ordinal.asc())
        ).all()
    )
    assert len(comment_images) == 1
    assert comment_images[0].rpid == 101
    assert comment_images[0].storage_status == "skipped"

    assert processed_job.progress["auxiliary"]["comments"]["fetched_count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["stored_count"] == 2
    assert processed_job.progress["auxiliary"]["comments"]["image_count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["stored_image_count"] == 0
    assert processed_job.progress["auxiliary"]["comments"]["failed_image_count"] == 0
    assert processed_job.progress["auxiliary"]["comments"]["skipped_image_count"] == 1


def test_process_metadata_ingest_job_deduplicates_history_and_snapshot_danmaku(
    db: Session,
) -> None:
    bvid = random_bvid()
    job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_danmaku": True},
    )

    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            danmaku_by_cid={
                101: [
                    BilibiliDanmakuMetadata(
                        id=1001,
                        cid=101,
                        time_offset_seconds=1.5,
                        mode=1,
                        font_size=25,
                        color=16777215,
                        content="Hello",
                        sent_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                        source="history_proto",
                        history_date=date(2025, 1, 2),
                        raw={"id": 1001, "content": "Hello"},
                    ),
                    BilibiliDanmakuMetadata(
                        id=1001,
                        cid=101,
                        time_offset_seconds=1.5,
                        mode=1,
                        font_size=25,
                        color=16777215,
                        content="Hello",
                        sent_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                        source="snapshot_xml",
                        history_date=None,
                        raw={"p": "1.5,1,25,16777215,1735776000,0,hash-a,1001"},
                    ),
                ]
            }
        ),
    )

    assert processed_job.progress["auxiliary"]["danmaku"]["count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["stored_count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["duplicate_count"] == 1

    danmaku_entries = list(
        db.exec(select(VideoDanmaku).where(VideoDanmaku.bvid == bvid)).all()
    )
    assert len(danmaku_entries) == 1
    assert danmaku_entries[0].danmaku_id == 1001
    assert danmaku_entries[0].source == "history_proto"


def test_process_metadata_ingest_job_deduplicates_danmaku_without_ids_across_refreshes(
    db: Session,
) -> None:
    bvid = random_bvid()
    first_job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_danmaku": True},
    )
    first_processed_job = process_metadata_ingest_job(
        session=db,
        job_id=first_job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            danmaku_by_cid={
                101: [
                    BilibiliDanmakuMetadata(
                        id=None,
                        cid=101,
                        time_offset_seconds=1.5,
                        mode=1,
                        font_size=25,
                        color=16777215,
                        content="Hello without id",
                        sent_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                        source="snapshot_xml",
                        history_date=None,
                        raw={"p": "1.5,1,25,16777215,1735776000,0,hash-a,"},
                    )
                ]
            }
        ),
    )

    second_job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_danmaku": True},
    )
    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=second_job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            danmaku_by_cid={
                101: [
                    BilibiliDanmakuMetadata(
                        id=None,
                        cid=101,
                        time_offset_seconds=1.5,
                        mode=1,
                        font_size=25,
                        color=16777215,
                        content="Hello without id",
                        sent_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
                        source="snapshot_xml",
                        history_date=None,
                        raw={"p": "1.5,1,25,16777215,1735776000,0,hash-a,"},
                    )
                ]
            }
        ),
    )

    danmaku_entries = list(
        db.exec(
            select(VideoDanmaku)
            .where(VideoDanmaku.bvid == bvid)
            .order_by(VideoDanmaku.cid.asc(), VideoDanmaku.time_offset_seconds.asc())
        ).all()
    )
    assert len(danmaku_entries) == 1
    assert danmaku_entries[0].content == "Hello without id"
    assert processed_job.progress["auxiliary"]["danmaku"]["stored_count"] == 1
    assert processed_job.progress["auxiliary"]["danmaku"]["duplicate_count"] == 0


def test_process_metadata_ingest_job_marks_download_ready_when_download_requested(
    db: Session,
) -> None:
    bvid = random_bvid()
    job = create_job(db, bvid=bvid, download_video=True)

    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
    )

    assert processed_job.status == "metadata_ready"
    assert processed_job.phase == "metadata stored; ready for download worker"
    assert processed_job.finished_at is None
    assert processed_job.progress["next_step"] == "downloader_worker"


def test_process_metadata_ingest_job_reconciles_video_pages_on_refresh(
    db: Session,
) -> None:
    bvid = random_bvid()
    first_job = create_job(db, bvid=bvid, download_video=False)
    process_metadata_ingest_job(
        session=db,
        job_id=first_job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
    )

    second_job = create_job(db, bvid=bvid, download_video=False)
    refreshed_metadata = build_metadata(
        bvid=bvid,
        title="Updated title",
        pages=[
            BilibiliVideoPageMetadata(
                cid=101,
                page_no=1,
                part_title="Updated Part 1",
                duration_seconds=180,
                raw={"cid": 101, "updated": True},
            )
        ],
    )
    process_metadata_ingest_job(
        session=db,
        job_id=second_job.id,
        provider=StaticMetadataProvider(refreshed_metadata),
    )

    video = db.get(Video, bvid)
    assert video is not None
    assert video.title == "Updated title"

    pages = list(
        db.exec(
            select(VideoPage)
            .where(VideoPage.bvid == bvid)
            .order_by(VideoPage.page_no)
        ).all()
    )
    assert len(pages) == 1
    assert pages[0].cid == 101
    assert pages[0].part_title == "Updated Part 1"


def test_process_metadata_ingest_job_merges_requested_auxiliary_records_on_refresh(
    db: Session,
) -> None:
    bvid = random_bvid()
    first_job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_comments": True, "fetch_danmaku": True, "fetch_subtitles": True},
    )
    first_processed_job = process_metadata_ingest_job(
        session=db,
        job_id=first_job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            comments=[
                BilibiliCommentMetadata(rpid=101, message="Old reply", raw={"rpid": 101})
            ],
            danmaku_by_cid={
                101: [
                    BilibiliDanmakuMetadata(
                        id=1001,
                        cid=101,
                        content="Old danmaku",
                        raw={"p": "1.0,1,25,16777215,1735776000,0,hash-a,1001"},
                    )
                ]
            },
            subtitles_by_cid={
                101: [
                    BilibiliSubtitleMetadata(
                        cid=101,
                        lang="zh-CN",
                        source="bilibili_player_v2",
                        content="旧字幕",
                        raw={"track": {"lan": "zh-CN"}},
                    )
                ]
            },
        ),
    )
    assert first_processed_job.status == "metadata_ready", first_processed_job.error_message

    second_job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_comments": True, "fetch_danmaku": True, "fetch_subtitles": True},
    )
    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=second_job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=StaticAuxiliaryProvider(
            comments=[
                BilibiliCommentMetadata(rpid=202, message="New reply", raw={"rpid": 202})
            ],
            danmaku_by_cid={
                202: [
                    BilibiliDanmakuMetadata(
                        id=2002,
                        cid=202,
                        content="New danmaku",
                        raw={"p": "2.0,1,25,16777215,1735777000,0,hash-b,2002"},
                    )
                ]
            },
            subtitles_by_cid={
                202: [
                    BilibiliSubtitleMetadata(
                        cid=202,
                        lang="en",
                        source="bilibili_player_v2",
                        content="New subtitle",
                        raw={"track": {"lan": "en"}},
                    )
                ]
            },
        ),
    )

    comments = list(
        db.exec(
            select(VideoComment)
            .where(VideoComment.bvid == bvid)
            .order_by(VideoComment.rpid.asc())
        ).all()
    )
    assert [comment.rpid for comment in comments] == [101, 202], processed_job.error_message
    assert [comment.message for comment in comments] == ["Old reply", "New reply"]

    danmaku_entries = list(
        db.exec(
            select(VideoDanmaku)
            .where(VideoDanmaku.bvid == bvid)
            .order_by(VideoDanmaku.danmaku_id.asc())
        ).all()
    )
    assert [entry.danmaku_id for entry in danmaku_entries] == [1001, 2002]
    assert [entry.content for entry in danmaku_entries] == [
        "Old danmaku",
        "New danmaku",
    ]

    subtitles = list(
        db.exec(select(VideoSubtitle).where(VideoSubtitle.bvid == bvid)).all()
    )
    assert len(subtitles) == 1
    assert subtitles[0].cid == 202
    assert subtitles[0].lang == "en"
    assert subtitles[0].content == "New subtitle"
    assert processed_job.progress["auxiliary"]["comments"]["fetched_count"] == 1
    assert processed_job.progress["auxiliary"]["comments"]["stored_count"] == 2
    assert processed_job.progress["auxiliary"]["danmaku"]["stored_count"] == 2


def test_process_metadata_ingest_job_classifies_provider_failures(
    db: Session,
) -> None:
    bvid = random_bvid()
    job = create_job(db, bvid=bvid, download_video=False)

    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=job.id,
        provider=MissingMetadataProvider(),
    )

    assert processed_job.status == "failed"
    assert processed_job.error_code == "metadata_not_found"
    assert processed_job.retry_count == 1
    assert processed_job.finished_at is not None


def test_process_metadata_ingest_job_classifies_auxiliary_failures(
    db: Session,
) -> None:
    bvid = random_bvid()
    job = create_job(
        db,
        bvid=bvid,
        download_video=False,
        options={"fetch_comments": True},
    )

    processed_job = process_metadata_ingest_job(
        session=db,
        job_id=job.id,
        provider=StaticMetadataProvider(build_metadata(bvid=bvid)),
        auxiliary_provider=FailingAuxiliaryProvider(),
    )

    assert processed_job.status == "failed"
    assert processed_job.error_code == "auxiliary_invalid_response"
    assert processed_job.retry_count == 1


def test_process_next_metadata_ingest_job_uses_priority_order(db: Session) -> None:
    lower_priority_bvid = random_bvid()
    higher_priority_bvid = random_bvid()
    lower_priority_job = create_job(
        db, bvid=lower_priority_bvid, download_video=False
    )
    lower_priority_job.priority = 20
    db.add(lower_priority_job)

    higher_priority_job = create_job(
        db, bvid=higher_priority_bvid, download_video=False
    )
    higher_priority_job.priority = 10
    db.add(higher_priority_job)
    db.commit()

    processed_job = process_next_metadata_ingest_job(
        session=db,
        provider=StaticMetadataProvider(build_metadata(bvid=higher_priority_bvid)),
    )

    assert processed_job is not None
    assert processed_job.id == higher_priority_job.id
    assert processed_job.status == "metadata_ready"

    second_processed_job = process_next_metadata_ingest_job(
        session=db,
        provider=StaticMetadataProvider(build_metadata(bvid=lower_priority_bvid)),
    )

    assert second_processed_job is not None
    assert second_processed_job.id == lower_priority_job.id


def test_process_next_metadata_ingest_job_reclaims_stale_metadata_fetching_job(
    db: Session,
) -> None:
    stale_bvid = random_bvid()
    stale_job = create_job(db, bvid=stale_bvid, download_video=False)
    stale_started_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    stale_job.priority = -10_000
    stale_job.status = "metadata_fetching"
    stale_job.phase = "fetching video metadata"
    stale_job.started_at = stale_started_at
    stale_job.progress = {
        "current_step": "metadata_fetching",
        "last_transition_at": stale_started_at.isoformat(),
    }
    db.add(stale_job)
    db.commit()
    db.refresh(stale_job)

    processed_job = process_next_metadata_ingest_job(
        session=db,
        provider=StaticMetadataProvider(build_metadata(bvid=stale_bvid)),
    )

    assert processed_job is not None
    assert processed_job.id == stale_job.id
    assert processed_job.status == "metadata_ready"
    assert processed_job.progress["reclaim"]["count"] == 1
    assert processed_job.progress["reclaim"]["previous_status"] == "metadata_fetching"


def test_process_next_metadata_ingest_job_skips_fresh_metadata_fetching_jobs(
    db: Session,
) -> None:
    fresh_bvid = random_bvid()
    fresh_job = create_job(db, bvid=fresh_bvid, download_video=False)
    fresh_started_at = datetime.now(timezone.utc)
    fresh_job.priority = -10_000
    fresh_job.status = "metadata_fetching"
    fresh_job.phase = "fetching video metadata"
    fresh_job.started_at = fresh_started_at
    fresh_job.progress = {
        "current_step": "metadata_fetching",
        "last_transition_at": fresh_started_at.isoformat(),
    }
    db.add(fresh_job)

    pending_bvid = random_bvid()
    pending_job = create_job(db, bvid=pending_bvid, download_video=False)
    pending_job.priority = -9_999
    db.add(pending_job)
    db.commit()
    db.refresh(fresh_job)
    db.refresh(pending_job)

    processed_job = process_next_metadata_ingest_job(
        session=db,
        provider=StaticMetadataProvider(build_metadata(bvid=pending_bvid)),
    )

    assert processed_job is not None
    assert processed_job.id == pending_job.id
    assert processed_job.status == "metadata_ready"
    skipped_job = db.get(IngestJob, fresh_job.id)
    assert skipped_job is not None
    assert skipped_job.status == "metadata_fetching"
    assert "reclaim" not in skipped_job.progress

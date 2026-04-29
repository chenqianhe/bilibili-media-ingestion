from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.deps import get_object_storage_client
from app.core.config import settings
from app.ingest_models import (
    AuditEvent,
    IngestJob,
    MediaAsset,
    Video,
    VideoComment,
    VideoCommentImage,
    VideoDanmaku,
    VideoPage,
    VideoStatSnapshot,
    VideoSubtitle,
)
from app.main import app
from tests.utils.utils import random_bvid


def _base_rpid(bvid: str) -> int:
    return int.from_bytes(bvid.encode("utf-8"), "little") % 1_500_000_000


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _playback_url_prefix(asset_id: object) -> str:
    return (
        f"{settings.BACKEND_PUBLIC_URL}{settings.API_V1_STR}"
        f"/media/assets/{asset_id}/playback?token="
    )


def _store_ingest_job(
    *,
    db: Session,
    bvid: str,
    created_at: datetime,
    progress: dict[str, object],
    status: str = "completed",
    phase: str = "ingest completed",
) -> IngestJob:
    job = IngestJob(
        input_text=bvid,
        normalized_bvid=bvid,
        requested_by="operator@example.com",
        status=status,
        phase=phase,
        options={},
        progress=progress,
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


class RecordingDeleteStorageClient:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []

    def download_file(self, **kwargs: object) -> None:
        raise NotImplementedError

    def multipart_upload_file(self, **kwargs: object) -> None:
        raise NotImplementedError

    def delete_object(self, *, bucket: str, key: str) -> None:
        self.deleted.append((bucket, key))


def test_read_video_comments_returns_images_and_asset_summaries(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    base_rpid = _base_rpid(bvid)

    cover_asset = MediaAsset(
        bvid=bvid,
        cid=None,
        asset_type="cover",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"images/covers/bvid={bvid}/cid=unknown/asset_id=test/cover.jpg",
        filename="cover.jpg",
        content_type="image/jpeg",
    )
    video = Video(
        bvid=bvid,
        title=bvid,
    )
    db.add(video)
    db.commit()
    db.add(cover_asset)
    video.cover_asset_id = cover_asset.id

    source_asset = MediaAsset(
        bvid=bvid,
        cid=123456,
        asset_type="source_archive",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/source/bvid={bvid}/cid=123456/asset_id=test/source.mp4",
        filename="source.mp4",
        content_type="video/mp4",
    )
    comment_image_asset = MediaAsset(
        bvid=bvid,
        cid=None,
        asset_type="comment_image",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=(
            f"images/comments/bvid={bvid}/cid=unknown/asset_id=test/comment-image.jpg"
        ),
        filename="comment-image.jpg",
        content_type="image/jpeg",
        width=640,
        height=360,
    )
    db.add(source_asset)
    db.add(comment_image_asset)
    db.commit()
    db.refresh(comment_image_asset)

    older_comment = VideoComment(
        rpid=base_rpid + 1,
        bvid=bvid,
        oid=987654321,
        mid=42,
        uname="Uploader 42",
        root=None,
        parent=None,
        message="Top level reply",
        like_count=3,
        reply_count=1,
        ctime=datetime(2025, 1, 2, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 1},
    )
    newer_comment = VideoComment(
        rpid=base_rpid + 2,
        bvid=bvid,
        oid=987654321,
        mid=99,
        uname="Child Reply",
        root=base_rpid + 1,
        parent=base_rpid + 1,
        message="Child reply",
        like_count=1,
        reply_count=0,
        ctime=datetime(2025, 1, 3, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 2},
    )
    db.add(older_comment)
    db.add(newer_comment)
    db.commit()
    db.refresh(older_comment)
    db.refresh(newer_comment)

    db.add(
        VideoCommentImage(
            comment_id=older_comment.id,
            rpid=older_comment.rpid,
            bvid=bvid,
            ordinal=0,
            source_url=None,
            width=640,
            height=360,
            asset_id=comment_image_asset.id,
            storage_status="ready",
            raw={"img_src": "//i0.hdslb.com/bfs/reply/example-1.jpg"},
            crawled_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
        )
    )
    db.add(
        VideoCommentImage(
            comment_id=older_comment.id,
            rpid=older_comment.rpid,
            bvid=bvid,
            ordinal=1,
            source_url=None,
            width=320,
            height=240,
            asset_id=None,
            storage_status="failed",
            error_message="upload failed",
            raw={"img_src": "//i0.hdslb.com/bfs/reply/example-2.jpg"},
            crawled_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
        )
    )
    db.commit()

    comments_crawled_at = datetime(2025, 1, 4, tzinfo=timezone.utc)
    comments_job = _store_ingest_job(
        db=db,
        bvid=bvid,
        created_at=comments_crawled_at,
        progress={
            "metadata": {"last_crawled_at": comments_crawled_at.isoformat()},
            "auxiliary": {
                "comments": {
                    "count": 2,
                    "expected_count": 5,
                    "fetched_count": 2,
                    "fallback_used": True,
                    "partial": True,
                    "image_count": 2,
                    "stored_image_count": 1,
                    "failed_image_count": 1,
                    "skipped_image_count": 0,
                }
            },
        },
    )
    _store_ingest_job(
        db=db,
        bvid=bvid,
        created_at=datetime(2025, 1, 5, tzinfo=timezone.utc),
        progress={"metadata": {"last_crawled_at": "2025-01-05T00:00:00+00:00"}},
        status="metadata_ready",
        phase="metadata stored without auxiliary refresh",
    )

    comments_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comments",
        headers=superuser_token_headers,
        params={"limit": 2, "offset": 0},
    )
    assert comments_response.status_code == 200
    comments_payload = comments_response.json()
    assert comments_payload["bvid"] == bvid
    assert comments_payload["count"] == 2
    assert comments_payload["thread_count"] == 1
    assert comments_payload["limit"] == 2
    assert comments_payload["offset"] == 0
    assert comments_payload["completeness"] == {
        "partial": True,
        "expected_count": 5,
        "fetched_count": 2,
        "stored_count": 2,
        "fallback_used": True,
        "image_count": 2,
        "stored_image_count": 1,
        "failed_image_count": 1,
        "skipped_image_count": 0,
        "source_job": {
            "job_id": str(comments_job.id),
            "status": "completed",
            "phase": "ingest completed",
            "crawled_at": _iso_z(comments_crawled_at),
        },
    }
    assert [item["rpid"] for item in comments_payload["comments"]] == [
        older_comment.rpid,
        newer_comment.rpid,
    ]
    assert comments_payload["comments"][1]["images"] == []

    older_images = comments_payload["comments"][0]["images"]
    assert len(older_images) == 2
    assert older_images[0]["source_url"].startswith(
        _playback_url_prefix(comment_image_asset.id)
    )
    assert older_images[0]["asset_id"] == str(comment_image_asset.id)
    assert older_images[0]["storage_status"] == "ready"
    assert older_images[0]["asset"]["asset_id"] == str(comment_image_asset.id)
    assert older_images[0]["asset"]["asset_type"] == "comment_image"
    assert older_images[1]["source_url"] is None
    assert older_images[1]["asset"] is None
    assert older_images[1]["storage_status"] == "failed"
    assert older_images[1]["error_message"] == "upload failed"

    assets_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/assets",
        headers=superuser_token_headers,
        params={"asset_type": "comment_image"},
    )
    assert assets_response.status_code == 200
    assets_payload = assets_response.json()
    assert assets_payload["bvid"] == bvid
    assert len(assets_payload["assets"]) == 1
    assert assets_payload["assets"][0]["asset_id"] == str(comment_image_asset.id)
    assert assets_payload["assets"][0]["asset_type"] == "comment_image"

    filtered_thread_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comments",
        headers=superuser_token_headers,
        params={"root": older_comment.rpid},
    )
    assert filtered_thread_response.status_code == 200
    filtered_thread_payload = filtered_thread_response.json()
    assert filtered_thread_payload["count"] == 2
    assert filtered_thread_payload["thread_count"] == 1
    assert [item["rpid"] for item in filtered_thread_payload["comments"]] == [
        older_comment.rpid,
        newer_comment.rpid,
    ]

    direct_children_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comments",
        headers=superuser_token_headers,
        params={"parent": older_comment.rpid},
    )
    assert direct_children_response.status_code == 200
    direct_children_payload = direct_children_response.json()
    assert direct_children_payload["count"] == 1
    assert direct_children_payload["thread_count"] == 1
    assert [item["rpid"] for item in direct_children_payload["comments"]] == [
        newer_comment.rpid,
    ]


def test_read_video_comments_paginates_by_top_level_thread(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    base_rpid = _base_rpid(bvid)

    video = Video(
        bvid=bvid,
        title=bvid,
    )
    db.add(video)
    db.commit()

    older_root = VideoComment(
        rpid=base_rpid + 20,
        bvid=bvid,
        oid=987654321,
        mid=11,
        uname="Older Root",
        root=None,
        parent=None,
        message="Older thread root",
        like_count=3,
        reply_count=1,
        ctime=datetime(2025, 2, 1, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 20},
    )
    older_child = VideoComment(
        rpid=base_rpid + 21,
        bvid=bvid,
        oid=987654321,
        mid=12,
        uname="Older Child",
        root=older_root.rpid,
        parent=older_root.rpid,
        message="Older thread child",
        like_count=1,
        reply_count=0,
        ctime=datetime(2025, 2, 3, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 21},
    )
    newer_root = VideoComment(
        rpid=base_rpid + 30,
        bvid=bvid,
        oid=987654321,
        mid=21,
        uname="Newer Root",
        root=None,
        parent=None,
        message="Newer thread root",
        like_count=5,
        reply_count=1,
        ctime=datetime(2025, 3, 1, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 30},
    )
    newer_child = VideoComment(
        rpid=base_rpid + 31,
        bvid=bvid,
        oid=987654321,
        mid=22,
        uname="Newer Child",
        root=newer_root.rpid,
        parent=newer_root.rpid,
        message="Newer thread child",
        like_count=2,
        reply_count=0,
        ctime=datetime(2025, 3, 4, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 31},
    )
    db.add(older_root)
    db.add(older_child)
    db.add(newer_root)
    db.add(newer_child)
    db.commit()

    first_page_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comments",
        headers=superuser_token_headers,
        params={"limit": 1, "offset": 0},
    )
    assert first_page_response.status_code == 200
    first_page_payload = first_page_response.json()
    assert first_page_payload["count"] == 4
    assert first_page_payload["thread_count"] == 2
    assert [item["rpid"] for item in first_page_payload["comments"]] == [
        newer_root.rpid,
        newer_child.rpid,
    ]

    second_page_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comments",
        headers=superuser_token_headers,
        params={"limit": 1, "offset": 1},
    )
    assert second_page_response.status_code == 200
    second_page_payload = second_page_response.json()
    assert second_page_payload["count"] == 4
    assert second_page_payload["thread_count"] == 2
    assert [item["rpid"] for item in second_page_payload["comments"]] == [
        older_root.rpid,
        older_child.rpid,
    ]


def test_read_video_comment_images_supports_filters_and_comment_context(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    base_rpid = _base_rpid(bvid)

    cover_asset = MediaAsset(
        bvid=bvid,
        cid=None,
        asset_type="cover",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"images/covers/bvid={bvid}/cid=unknown/asset_id=test/cover.jpg",
        filename="cover.jpg",
        content_type="image/jpeg",
    )
    video = Video(
        bvid=bvid,
        title=bvid,
    )
    db.add(video)
    db.commit()
    db.add(cover_asset)
    video.cover_asset_id = cover_asset.id

    comment_image_asset = MediaAsset(
        bvid=bvid,
        cid=None,
        asset_type="comment_image",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=(
            f"images/comments/bvid={bvid}/cid=unknown/asset_id=test/comment-image.jpg"
        ),
        filename="comment-image.jpg",
        content_type="image/jpeg",
        width=640,
        height=360,
    )
    db.add(comment_image_asset)
    db.commit()
    db.refresh(comment_image_asset)

    root_comment = VideoComment(
        rpid=base_rpid + 10,
        bvid=bvid,
        oid=987654321,
        mid=42,
        uname="Uploader 42",
        root=None,
        parent=None,
        message="Root with mixed image outcomes",
        like_count=8,
        reply_count=1,
        ctime=datetime(2025, 2, 1, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 10},
    )
    child_comment = VideoComment(
        rpid=base_rpid + 11,
        bvid=bvid,
        oid=987654321,
        mid=84,
        uname="Child 84",
        root=base_rpid + 10,
        parent=base_rpid + 10,
        message="Child comment image",
        like_count=2,
        reply_count=0,
        ctime=datetime(2025, 2, 2, tzinfo=timezone.utc),
        raw={"rpid": base_rpid + 11},
    )
    db.add(root_comment)
    db.add(child_comment)
    db.commit()
    db.refresh(root_comment)
    db.refresh(child_comment)

    db.add(
        VideoCommentImage(
            comment_id=root_comment.id,
            rpid=root_comment.rpid,
            bvid=bvid,
            ordinal=0,
            source_url=None,
            width=640,
            height=360,
            asset_id=comment_image_asset.id,
            storage_status="ready",
            raw={"img_src": "//i0.hdslb.com/bfs/reply/root-ready.jpg"},
            crawled_at=datetime(2025, 2, 3, tzinfo=timezone.utc),
        )
    )
    db.add(
        VideoCommentImage(
            comment_id=root_comment.id,
            rpid=root_comment.rpid,
            bvid=bvid,
            ordinal=1,
            source_url=None,
            width=320,
            height=240,
            asset_id=None,
            storage_status="failed",
            error_message="upload failed",
            raw={"img_src": "//i0.hdslb.com/bfs/reply/root-failed.jpg"},
            crawled_at=datetime(2025, 2, 3, tzinfo=timezone.utc),
        )
    )
    db.add(
        VideoCommentImage(
            comment_id=child_comment.id,
            rpid=child_comment.rpid,
            bvid=bvid,
            ordinal=0,
            source_url=None,
            width=1280,
            height=720,
            asset_id=None,
            storage_status="skipped",
            raw={"img_src": "//i0.hdslb.com/bfs/reply/child-skipped.jpg"},
            crawled_at=datetime(2025, 2, 3, tzinfo=timezone.utc),
        )
    )
    db.commit()

    comments_crawled_at = datetime(2025, 2, 3, tzinfo=timezone.utc)
    comments_job = _store_ingest_job(
        db=db,
        bvid=bvid,
        created_at=comments_crawled_at,
        progress={
            "metadata": {"last_crawled_at": comments_crawled_at.isoformat()},
            "auxiliary": {
                "comments": {
                    "count": 2,
                    "expected_count": 2,
                    "fetched_count": 2,
                    "fallback_used": False,
                    "partial": False,
                    "image_count": 3,
                    "stored_image_count": 1,
                    "failed_image_count": 1,
                    "skipped_image_count": 1,
                }
            },
        },
    )

    response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comment-images",
        headers=superuser_token_headers,
        params={"limit": 2, "offset": 0},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["bvid"] == bvid
    assert payload["count"] == 3
    assert payload["limit"] == 2
    assert payload["offset"] == 0
    assert payload["completeness"] == {
        "partial": False,
        "expected_count": 2,
        "fetched_count": 2,
        "stored_count": 2,
        "fallback_used": False,
        "image_count": 3,
        "stored_image_count": 1,
        "failed_image_count": 1,
        "skipped_image_count": 1,
        "source_job": {
            "job_id": str(comments_job.id),
            "status": "completed",
            "phase": "ingest completed",
            "crawled_at": _iso_z(comments_crawled_at),
        },
    }
    assert [item["comment"]["rpid"] for item in payload["images"]] == [
        child_comment.rpid,
        root_comment.rpid,
    ]
    assert payload["images"][0]["storage_status"] == "skipped"
    assert payload["images"][0]["ordinal"] == 0
    assert payload["images"][0]["comment"]["parent"] == root_comment.rpid
    assert payload["images"][0]["source_url"] is None
    assert payload["images"][0]["asset"] is None
    assert payload["images"][1]["storage_status"] == "ready"
    assert payload["images"][1]["source_url"].startswith(
        _playback_url_prefix(comment_image_asset.id)
    )
    assert payload["images"][1]["asset_id"] == str(comment_image_asset.id)
    assert payload["images"][1]["asset"]["asset_type"] == "comment_image"
    assert payload["images"][1]["comment"]["message"] == root_comment.message

    failed_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comment-images",
        headers=superuser_token_headers,
        params={"storage_status": "failed"},
    )
    assert failed_response.status_code == 200
    failed_payload = failed_response.json()
    assert failed_payload["count"] == 1
    assert failed_payload["images"][0]["storage_status"] == "failed"
    assert failed_payload["images"][0]["source_url"] is None
    assert failed_payload["images"][0]["error_message"] == "upload failed"
    assert failed_payload["images"][0]["comment"]["rpid"] == root_comment.rpid

    root_thread_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comment-images",
        headers=superuser_token_headers,
        params={"root": root_comment.rpid},
    )
    assert root_thread_response.status_code == 200
    root_thread_payload = root_thread_response.json()
    assert root_thread_payload["count"] == 3

    direct_child_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comment-images",
        headers=superuser_token_headers,
        params={"parent": root_comment.rpid},
    )
    assert direct_child_response.status_code == 200
    direct_child_payload = direct_child_response.json()
    assert direct_child_payload["count"] == 1
    assert direct_child_payload["images"][0]["comment"]["rpid"] == child_comment.rpid

    root_comment_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/comment-images",
        headers=superuser_token_headers,
        params={"rpid": root_comment.rpid},
    )
    assert root_comment_response.status_code == 200
    root_comment_payload = root_comment_response.json()
    assert root_comment_payload["count"] == 2
    assert [item["ordinal"] for item in root_comment_payload["images"]] == [0, 1]


def test_read_video_danmaku_supports_filters(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()

    video = Video(
        bvid=bvid,
        title=bvid,
    )
    db.add(video)
    db.commit()

    db.add(
        VideoDanmaku(
            danmaku_id=1001,
            bvid=bvid,
            cid=101,
            time_offset_seconds=1.5,
            mode=1,
            font_size=25,
            color=16777215,
            content="History row",
            sent_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
            source="history_proto",
            history_date=date(2025, 1, 2),
            raw={"id": 1001},
        )
    )
    db.add(
        VideoDanmaku(
            danmaku_id=1002,
            bvid=bvid,
            cid=101,
            time_offset_seconds=2.5,
            mode=1,
            font_size=25,
            color=16777215,
            content="Snapshot row",
            sent_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
            source="snapshot_xml",
            history_date=None,
            raw={"id": 1002},
        )
    )
    db.add(
        VideoDanmaku(
            danmaku_id=2001,
            bvid=bvid,
            cid=202,
            time_offset_seconds=3.5,
            mode=4,
            font_size=18,
            color=255,
            content="Other cid",
            sent_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
            source="history_proto",
            history_date=date(2025, 1, 4),
            raw={"id": 2001},
        )
    )
    db.commit()

    danmaku_crawled_at = datetime(2025, 1, 4, tzinfo=timezone.utc)
    danmaku_job = _store_ingest_job(
        db=db,
        bvid=bvid,
        created_at=danmaku_crawled_at,
        progress={
            "metadata": {"last_crawled_at": danmaku_crawled_at.isoformat()},
            "auxiliary": {
                "danmaku": {
                    "stored_count": 3,
                    "duplicate_count": 1,
                    "cid_count": 2,
                    "filled_cid_count": 2,
                    "source": "mixed",
                    "history_used": True,
                    "snapshot_used": True,
                    "indexed_month_count": 3,
                    "expected_days_count": 4,
                    "fetched_days_count": 3,
                    "partial": True,
                    "pages": [
                        {
                            "cid": 101,
                            "count": 2,
                            "source": "mixed",
                            "history_used": True,
                            "snapshot_used": True,
                            "indexed_month_count": 2,
                            "expected_days_count": 3,
                            "fetched_days_count": 2,
                            "partial": True,
                        },
                        {
                            "cid": 202,
                            "count": 1,
                            "source": "history_proto",
                            "history_used": True,
                            "snapshot_used": False,
                            "indexed_month_count": 1,
                            "expected_days_count": 1,
                            "fetched_days_count": 1,
                            "partial": False,
                        },
                    ],
                }
            },
        },
    )

    response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/danmaku",
        headers=superuser_token_headers,
        params={"cid": 101, "limit": 10, "offset": 0},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["bvid"] == bvid
    assert payload["count"] == 2
    assert payload["completeness"] == {
        "partial": True,
        "stored_count": 3,
        "duplicate_count": 1,
        "cid_count": 2,
        "filled_cid_count": 2,
        "crawl_source": "mixed",
        "history_used": True,
        "snapshot_used": True,
        "indexed_month_count": 3,
        "expected_days_count": 4,
        "fetched_days_count": 3,
        "pages": [
            {
                "cid": 101,
                "count": 2,
                "source": "mixed",
                "history_used": True,
                "snapshot_used": True,
                "indexed_month_count": 2,
                "expected_days_count": 3,
                "fetched_days_count": 2,
                "partial": True,
            },
            {
                "cid": 202,
                "count": 1,
                "source": "history_proto",
                "history_used": True,
                "snapshot_used": False,
                "indexed_month_count": 1,
                "expected_days_count": 1,
                "fetched_days_count": 1,
                "partial": False,
            },
        ],
        "source_job": {
            "job_id": str(danmaku_job.id),
            "status": "completed",
            "phase": "ingest completed",
            "crawled_at": _iso_z(danmaku_crawled_at),
        },
    }
    assert [item["danmaku_id"] for item in payload["danmaku"]] == [1001, 1002]
    assert payload["danmaku"][0]["source"] == "history_proto"
    assert payload["danmaku"][0]["history_date"] == "2025-01-02"
    assert payload["danmaku"][1]["source"] == "snapshot_xml"
    assert payload["danmaku"][1]["history_date"] is None

    filtered_response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/danmaku",
        headers=superuser_token_headers,
        params={"source": "history_proto", "history_date": "2025-01-04"},
    )
    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["count"] == 1
    assert [item["danmaku_id"] for item in filtered_payload["danmaku"]] == [2001]


def test_read_videos_and_video_detail(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    first_bvid = random_bvid()
    second_bvid = random_bvid()
    first_cover_asset = MediaAsset(
        bvid=first_bvid,
        cid=None,
        asset_type="cover",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"images/covers/bvid={first_bvid}/cid=unknown/asset_id=test/alpha.jpg",
        filename="alpha.jpg",
        content_type="image/jpeg",
    )
    second_cover_asset = MediaAsset(
        bvid=second_bvid,
        cid=None,
        asset_type="cover",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"images/covers/bvid={second_bvid}/cid=unknown/asset_id=test/beta.jpg",
        filename="beta.jpg",
        content_type="image/jpeg",
    )
    db.add(
        Video(
            bvid=first_bvid,
            aid=1001,
            title="Alpha Archive",
            description="First video",
            duration_seconds=120,
            owner_name="Uploader A",
            category="archive",
            tags=["alpha", "archive"],
            stat={"view": 10},
            takedown_status="active",
            last_crawled_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        )
    )
    db.add(
        Video(
            bvid=second_bvid,
            aid=1002,
            title="Beta Buffer",
            description="Second video",
            duration_seconds=240,
            owner_name="Uploader B",
            category="buffer",
            tags=["beta"],
            stat={"view": 20},
            takedown_status="active",
            last_crawled_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
        )
    )
    db.commit()
    db.add(first_cover_asset)
    db.add(second_cover_asset)
    first_video = db.get(Video, first_bvid)
    second_video = db.get(Video, second_bvid)
    assert first_video is not None
    assert second_video is not None
    first_video.cover_asset_id = first_cover_asset.id
    second_video.cover_asset_id = second_cover_asset.id
    db.add(first_video)
    db.add(second_video)
    db.commit()

    response = client.get(
        f"{settings.API_V1_STR}/videos/",
        headers=superuser_token_headers,
        params={"q": "beta", "limit": 10, "offset": 0},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["limit"] == 10
    assert payload["offset"] == 0
    assert payload["data"][0]["bvid"] == second_bvid
    assert payload["data"][0]["title"] == "Beta Buffer"
    assert payload["data"][0]["cover_url"].startswith(
        _playback_url_prefix(second_cover_asset.id)
    )

    detail_response = client.get(
        f"{settings.API_V1_STR}/videos/{first_bvid}",
        headers=superuser_token_headers,
    )
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["bvid"] == first_bvid
    assert detail_payload["description"] == "First video"
    assert detail_payload["cover_url"].startswith(
        _playback_url_prefix(first_cover_asset.id)
    )
    assert detail_payload["stat"] == {"view": 10}
    assert detail_payload["tags"] == ["alpha", "archive"]


def test_delete_video_removes_children_and_storage_objects(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    base_rpid = _base_rpid(bvid)

    ingest_job = IngestJob(
        input_text=bvid,
        normalized_bvid=bvid,
        requested_by="operator@example.com",
        status="completed",
        phase="ingest completed",
        options={},
        progress={},
        created_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        started_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
        finished_at=datetime(2025, 1, 2, tzinfo=timezone.utc),
    )
    video = Video(
        bvid=bvid,
        title="Delete Me",
        description="remove all related rows",
        duration_seconds=180,
        owner_name="Uploader",
        stat={"view": 123},
        last_crawled_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
    )
    db.add(ingest_job)
    db.add(video)
    db.commit()
    db.refresh(ingest_job)

    cover_asset = MediaAsset(
        bvid=bvid,
        cid=None,
        job_id=ingest_job.id,
        asset_type="cover",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"images/covers/bvid={bvid}/cid=unknown/asset_id=cover/cover.jpg",
        filename="cover.jpg",
        content_type="image/jpeg",
    )
    proxy_asset = MediaAsset(
        bvid=bvid,
        cid=100,
        job_id=ingest_job.id,
        asset_type="proxy_mp4",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/proxy/bvid={bvid}/cid=100/asset_id=proxy/proxy.mp4",
        filename="proxy.mp4",
        content_type="video/mp4",
    )
    comment_asset = MediaAsset(
        bvid=bvid,
        cid=None,
        job_id=ingest_job.id,
        asset_type="comment_image",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"images/comments/bvid={bvid}/cid=unknown/asset_id=comment/comment.jpg",
        filename="comment.jpg",
        content_type="image/jpeg",
    )
    db.add(cover_asset)
    db.add(proxy_asset)
    db.add(comment_asset)
    db.commit()
    db.refresh(video)
    db.refresh(cover_asset)
    db.refresh(proxy_asset)
    db.refresh(comment_asset)
    video.cover_asset_id = cover_asset.id
    db.add(video)
    db.add(
        VideoPage(
            bvid=bvid,
            aid=video.aid,
            cid=100,
            page_no=1,
            part_title="P1",
            duration_seconds=180,
        )
    )
    db.add(
        VideoStatSnapshot(
            bvid=bvid,
            view_count=123,
            like_count=10,
            crawled_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
        )
    )
    db.add(
        VideoSubtitle(
            bvid=bvid,
            cid=100,
            lang="zh-CN",
            source="player_v2",
            content='{"body":[{"from":0.0,"to":1.0,"content":"你好"}]}',
            asset_id=proxy_asset.id,
            crawled_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
        )
    )
    comment = VideoComment(
        rpid=base_rpid,
        bvid=bvid,
        oid=987654321,
        mid=42,
        uname="Commenter",
        message="delete me too",
        like_count=3,
        reply_count=0,
        ctime=datetime(2025, 1, 4, tzinfo=timezone.utc),
        raw={"rpid": base_rpid},
    )
    db.add(comment)
    db.add(
        VideoDanmaku(
            bvid=bvid,
            cid=100,
            danmaku_id=1001,
            time_offset_seconds=12.5,
            content="hello",
            source="snapshot_xml",
            crawled_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
        )
    )
    db.commit()
    db.refresh(comment)

    db.add(
        VideoCommentImage(
            comment_id=comment.id,
            rpid=comment.rpid,
            bvid=bvid,
            ordinal=0,
            source_url=None,
            width=640,
            height=360,
            asset_id=comment_asset.id,
            storage_status="ready",
            raw={"img_src": "//i0.hdslb.com/bfs/reply/example-delete.jpg"},
            crawled_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
        )
    )
    db.commit()

    storage_client = RecordingDeleteStorageClient()
    app.dependency_overrides[get_object_storage_client] = lambda: storage_client
    try:
        response = client.delete(
            f"{settings.API_V1_STR}/videos/{bvid}",
            headers=superuser_token_headers,
        )
    finally:
        app.dependency_overrides.pop(get_object_storage_client, None)

    assert response.status_code == 200
    assert response.json() == {"message": "Video deleted successfully"}

    db.expire_all()
    assert db.get(Video, bvid) is None
    assert db.exec(select(VideoPage).where(VideoPage.bvid == bvid)).all() == []
    assert (
        db.exec(select(VideoStatSnapshot).where(VideoStatSnapshot.bvid == bvid)).all()
        == []
    )
    assert db.exec(select(MediaAsset).where(MediaAsset.bvid == bvid)).all() == []
    assert db.exec(select(VideoSubtitle).where(VideoSubtitle.bvid == bvid)).all() == []
    assert db.exec(select(VideoComment).where(VideoComment.bvid == bvid)).all() == []
    assert (
        db.exec(select(VideoCommentImage).where(VideoCommentImage.bvid == bvid)).all()
        == []
    )
    assert db.exec(select(VideoDanmaku).where(VideoDanmaku.bvid == bvid)).all() == []
    assert db.get(IngestJob, ingest_job.id) is not None

    audit_event = db.exec(
        select(AuditEvent).where(
            AuditEvent.action == "video.deleted",
            AuditEvent.resource_type == "video",
            AuditEvent.resource_id == bvid,
        )
    ).first()
    assert audit_event is not None
    assert audit_event.payload["deleted_object_count"] == 3

    assert set(storage_client.deleted) == {
        ("bili-media-dev", f"images/covers/bvid={bvid}/cid=unknown/asset_id=cover/cover.jpg"),
        ("bili-media-dev", f"media/proxy/bvid={bvid}/cid=100/asset_id=proxy/proxy.mp4"),
        (
            "bili-media-dev",
            f"images/comments/bvid={bvid}/cid=unknown/asset_id=comment/comment.jpg",
        ),
    }


def test_delete_video_keeps_storage_objects_referenced_by_other_assets(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    deleted_bvid = random_bvid()
    kept_bvid = random_bvid()
    shared_key = "media/proxy/shared/proxy.mp4"
    unique_key = f"media/proxy/bvid={deleted_bvid}/cid=100/asset_id=unique/proxy.mp4"

    db.add(Video(bvid=deleted_bvid, title="Delete shared reference"))
    db.add(Video(bvid=kept_bvid, title="Keep shared reference"))
    db.commit()

    deleted_shared_asset = MediaAsset(
        bvid=deleted_bvid,
        cid=100,
        asset_type="proxy_mp4",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=shared_key,
        filename="proxy.mp4",
        content_type="video/mp4",
    )
    kept_shared_asset = MediaAsset(
        bvid=kept_bvid,
        cid=100,
        asset_type="proxy_mp4",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=shared_key,
        filename="proxy.mp4",
        content_type="video/mp4",
    )
    unique_asset = MediaAsset(
        bvid=deleted_bvid,
        cid=100,
        asset_type="thumbnail",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=unique_key,
        filename="thumbnail.jpg",
        content_type="image/jpeg",
    )
    db.add(deleted_shared_asset)
    db.add(kept_shared_asset)
    db.add(unique_asset)
    db.commit()
    db.refresh(kept_shared_asset)

    storage_client = RecordingDeleteStorageClient()
    app.dependency_overrides[get_object_storage_client] = lambda: storage_client
    try:
        response = client.delete(
            f"{settings.API_V1_STR}/videos/{deleted_bvid}",
            headers=superuser_token_headers,
        )
    finally:
        app.dependency_overrides.pop(get_object_storage_client, None)

    assert response.status_code == 200
    assert db.get(Video, deleted_bvid) is None
    assert db.get(Video, kept_bvid) is not None
    assert db.get(MediaAsset, kept_shared_asset.id) is not None
    assert ("bili-media-dev", shared_key) not in storage_client.deleted
    assert storage_client.deleted == [("bili-media-dev", unique_key)]


def test_delete_video_requires_superuser(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    db.add(Video(bvid=bvid, title="Restricted delete"))
    db.commit()

    response = client.delete(
        f"{settings.API_V1_STR}/videos/{bvid}",
        headers=normal_user_token_headers,
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "The user doesn't have enough privileges"
    assert db.get(Video, bvid) is not None


def test_read_video_subtitles_supports_filters(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    db.add(
        Video(
            bvid=bvid,
            title=bvid,
        )
    )
    db.commit()

    db.add(
        VideoSubtitle(
            bvid=bvid,
            cid=101,
            lang="zh-CN",
            source="player_v2",
            content='{"body":[{"from":0.0,"to":1.0,"content":"你好"}]}',
            crawled_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
        )
    )
    db.add(
        VideoSubtitle(
            bvid=bvid,
            cid=202,
            lang="en",
            source="player_v2",
            content='{"body":[{"from":0.0,"to":1.0,"content":"hello"}]}',
            crawled_at=datetime(2025, 1, 4, tzinfo=timezone.utc),
        )
    )
    db.commit()

    subtitles_crawled_at = datetime(2025, 1, 5, tzinfo=timezone.utc)
    subtitles_job = _store_ingest_job(
        db=db,
        bvid=bvid,
        created_at=subtitles_crawled_at,
        progress={
            "metadata": {"last_crawled_at": subtitles_crawled_at.isoformat()},
            "auxiliary": {
                "subtitles": {
                    "count": 2,
                    "cid_count": 2,
                    "languages": ["en", "zh-CN"],
                }
            },
        },
    )

    response = client.get(
        f"{settings.API_V1_STR}/videos/{bvid}/subtitles",
        headers=superuser_token_headers,
        params={"lang": "en"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["bvid"] == bvid
    assert payload["count"] == 1
    assert payload["completeness"] == {
        "partial": None,
        "stored_count": 2,
        "cid_count": 2,
        "languages": ["en", "zh-CN"],
        "source_job": {
            "job_id": str(subtitles_job.id),
            "status": "completed",
            "phase": "ingest completed",
            "crawled_at": _iso_z(subtitles_crawled_at),
        },
    }
    assert payload["subtitles"][0]["lang"] == "en"
    assert payload["subtitles"][0]["cid"] == 202


def test_create_video_subtitle_transcription_tasks_queues_source_assets(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
) -> None:
    bvid = random_bvid()
    db.add(Video(bvid=bvid, title=bvid))
    db.commit()

    source_asset = MediaAsset(
        bvid=bvid,
        cid=202,
        asset_type="source_archive",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/source/bvid={bvid}/cid=202/source.mp4",
        filename="source.mp4",
        content_type="video/mp4",
    )
    db.add(source_asset)
    db.commit()

    response = client.post(
        f"{settings.API_V1_STR}/videos/{bvid}/subtitles/transcriptions",
        headers=superuser_token_headers,
        json={"cid": 202},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["bvid"] == bvid
    assert len(payload["assets"]) == 1
    queued_asset = payload["assets"][0]
    assert queued_asset["asset_type"] == "subtitle"
    assert queued_asset["variant"] == "openai-stt"
    assert queued_asset["status"] == "pending"
    assert queued_asset["cid"] == 202

    stored_asset = db.get(MediaAsset, queued_asset["asset_id"])
    assert stored_asset is not None
    assert stored_asset.metadata_json["transcription_source_asset_id"] == str(
        source_asset.id
    )
    assert stored_asset.s3_key is not None

    audit_event = db.exec(
        select(AuditEvent).where(
            AuditEvent.action == "subtitle_transcription.backfill_enqueued",
            AuditEvent.resource_id == str(stored_asset.id),
        )
    ).first()
    assert audit_event is not None

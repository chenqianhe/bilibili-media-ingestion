from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.deps import get_object_storage_client
from app.core.config import settings
from app.ingest_models import MediaAsset, Video
from app.main import app
from app.uploader.base import ObjectStorageDownloadError, ObjectStorageResult
from tests.utils.utils import random_bvid


class RecordingDownloadStorageClient:
    def __init__(self, *, root_dir: Path) -> None:
        self.root_dir = root_dir

    def seed_object(self, *, bucket: str, key: str, content: bytes) -> None:
        remote_path = self.root_dir / bucket / key
        remote_path.parent.mkdir(parents=True, exist_ok=True)
        remote_path.write_bytes(content)

    def download_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
    ) -> ObjectStorageResult:
        remote_path = self.root_dir / bucket / key
        local_path.parent.mkdir(parents=True, exist_ok=True)
        payload = remote_path.read_bytes()
        local_path.write_bytes(payload)
        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=len(payload),
            etag="test-etag",
            content_type=None,
        )

    def multipart_upload_file(self, **kwargs: object) -> ObjectStorageResult:
        raise NotImplementedError

    def delete_object(self, **kwargs: object) -> None:
        raise NotImplementedError


class FailingDownloadStorageClient(RecordingDownloadStorageClient):
    def download_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
    ) -> ObjectStorageResult:
        raise ObjectStorageDownloadError(f"Object storage download failed for {bucket}/{key}")


def test_playback_url_streams_binary_assets_with_range_support(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    tmp_path: Path,
) -> None:
    bvid = random_bvid()
    db.add(Video(bvid=bvid, title=bvid))
    db.commit()

    asset = MediaAsset(
        bvid=bvid,
        cid=123,
        asset_type="proxy_mp4",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/proxy/bvid={bvid}/cid=123/asset_id=test/proxy.mp4",
        filename="proxy.mp4",
        content_type="video/mp4",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    storage_client = RecordingDownloadStorageClient(root_dir=tmp_path / "remote")
    payload = b"proxy-playback-binary"
    storage_client.seed_object(
        bucket=asset.s3_bucket or "",
        key=asset.s3_key or "",
        content=payload,
    )

    app.dependency_overrides[get_object_storage_client] = lambda: storage_client
    try:
        playback_url_response = client.post(
            f"{settings.API_V1_STR}/media/assets/{asset.id}/playback-url",
            headers=superuser_token_headers,
            json={"expires_in": 900},
        )
        assert playback_url_response.status_code == 200

        parsed = urlparse(playback_url_response.json()["url"])
        playback_response = client.get(
            f"{parsed.path}?{parsed.query}",
            headers={"Range": "bytes=0-4"},
        )
        assert playback_response.status_code == 206
        assert playback_response.content == payload[:5]
        assert playback_response.headers["content-range"] == f"bytes 0-4/{len(payload)}"
        assert playback_response.headers["content-type"].startswith("video/mp4")
    finally:
        app.dependency_overrides.pop(get_object_storage_client, None)


def test_playback_url_returns_bad_gateway_when_storage_object_is_missing(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    tmp_path: Path,
) -> None:
    bvid = random_bvid()
    db.add(Video(bvid=bvid, title=bvid))
    db.commit()

    asset = MediaAsset(
        bvid=bvid,
        cid=123,
        asset_type="proxy_mp4",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/proxy/bvid={bvid}/cid=123/asset_id=test/proxy.mp4",
        filename="proxy.mp4",
        content_type="video/mp4",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    storage_client = FailingDownloadStorageClient(root_dir=tmp_path / "remote")
    app.dependency_overrides[get_object_storage_client] = lambda: storage_client
    try:
        playback_url_response = client.post(
            f"{settings.API_V1_STR}/media/assets/{asset.id}/playback-url",
            headers=superuser_token_headers,
            json={"expires_in": 900},
        )
        assert playback_url_response.status_code == 200

        parsed = urlparse(playback_url_response.json()["url"])
        playback_response = client.get(f"{parsed.path}?{parsed.query}")
        assert playback_response.status_code == 502
        assert "Media object could not be downloaded" in playback_response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_object_storage_client, None)


def test_playback_url_rewrites_hls_manifests_and_serves_segments(
    client: TestClient,
    superuser_token_headers: dict[str, str],
    db: Session,
    tmp_path: Path,
) -> None:
    bvid = random_bvid()
    db.add(Video(bvid=bvid, title=bvid))
    db.commit()

    segment_asset = MediaAsset(
        bvid=bvid,
        cid=456,
        asset_type="hls_segment",
        variant="segment:segment_00000",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/hls/bvid={bvid}/cid=456/asset_id=segment/segment_00000.ts",
        filename="segment_00000.ts",
        content_type="video/mp2t",
        metadata_json={"hls_role": "media_segment", "hls_relative_path": "segment_00000.ts"},
    )
    db.add(segment_asset)
    db.commit()
    db.refresh(segment_asset)

    playlist_asset = MediaAsset(
        bvid=bvid,
        cid=456,
        asset_type="hls_segment",
        variant="playlist:stream",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/hls/bvid={bvid}/cid=456/asset_id=playlist/stream.m3u8",
        filename="stream.m3u8",
        content_type="application/vnd.apple.mpegurl",
        metadata_json={
            "hls_role": "media_playlist",
            "hls_relative_path": "stream.m3u8",
            "hls_references": [
                {
                    "uri": "segment_00000.ts",
                    "asset_id": str(segment_asset.id),
                    "relative_path": "segment_00000.ts",
                }
            ],
        },
    )
    db.add(playlist_asset)
    db.commit()
    db.refresh(playlist_asset)

    master_asset = MediaAsset(
        bvid=bvid,
        cid=456,
        asset_type="hls_master",
        variant="master",
        status="ready",
        s3_bucket="bili-media-dev",
        s3_key=f"media/hls/bvid={bvid}/cid=456/asset_id=master/master.m3u8",
        filename="master.m3u8",
        content_type="application/vnd.apple.mpegurl",
        metadata_json={
            "hls_role": "master_playlist",
            "hls_relative_path": "master.m3u8",
            "hls_references": [
                {
                    "uri": "stream.m3u8",
                    "asset_id": str(playlist_asset.id),
                    "relative_path": "stream.m3u8",
                }
            ],
        },
    )
    db.add(master_asset)
    db.commit()
    db.refresh(master_asset)

    storage_client = RecordingDownloadStorageClient(root_dir=tmp_path / "remote")
    storage_client.seed_object(
        bucket=master_asset.s3_bucket or "",
        key=master_asset.s3_key or "",
        content=b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1200000\nstream.m3u8\n",
    )
    storage_client.seed_object(
        bucket=playlist_asset.s3_bucket or "",
        key=playlist_asset.s3_key or "",
        content=b"#EXTM3U\n#EXTINF:6.0,\nsegment_00000.ts\n#EXT-X-ENDLIST\n",
    )
    segment_payload = b"hls-segment-payload"
    storage_client.seed_object(
        bucket=segment_asset.s3_bucket or "",
        key=segment_asset.s3_key or "",
        content=segment_payload,
    )

    app.dependency_overrides[get_object_storage_client] = lambda: storage_client
    try:
        playback_url_response = client.post(
            f"{settings.API_V1_STR}/media/assets/{master_asset.id}/playback-url",
            headers=superuser_token_headers,
            json={"expires_in": 900},
        )
        assert playback_url_response.status_code == 200
        parsed_master = urlparse(playback_url_response.json()["url"])

        master_response = client.get(f"{parsed_master.path}?{parsed_master.query}")
        assert master_response.status_code == 200
        assert "stream.m3u8" not in master_response.text
        assert f"/media/assets/{playlist_asset.id}/playback?token=" in master_response.text

        playlist_url = next(
            line
            for line in master_response.text.splitlines()
            if line and not line.startswith("#")
        )
        parsed_playlist = urlparse(playlist_url)
        playlist_response = client.get(f"{parsed_playlist.path}?{parsed_playlist.query}")
        assert playlist_response.status_code == 200
        assert "segment_00000.ts" not in playlist_response.text
        assert f"/media/assets/{segment_asset.id}/playback?token=" in playlist_response.text

        segment_url = next(
            line
            for line in playlist_response.text.splitlines()
            if line and not line.startswith("#")
        )
        parsed_segment = urlparse(segment_url)
        segment_response = client.get(f"{parsed_segment.path}?{parsed_segment.query}")
        assert segment_response.status_code == 200
        assert segment_response.content == segment_payload
        assert segment_response.headers["content-type"].startswith("video/mp2t")
    finally:
        app.dependency_overrides.pop(get_object_storage_client, None)

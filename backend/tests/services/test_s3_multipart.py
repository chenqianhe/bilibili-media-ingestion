from __future__ import annotations

from pathlib import Path

import pytest

from app.uploader.base import ObjectStorageVerificationError
from app.uploader.s3_multipart import S3MultipartObjectStorageClient


class FakeStreamingBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False

    def iter_chunks(self, chunk_size: int) -> list[bytes]:
        return [
            self.payload[index : index + chunk_size]
            for index in range(0, len(self.payload), chunk_size)
        ]

    def close(self) -> None:
        self.closed = True


class FakeS3Client:
    def __init__(self, *, payload: bytes, reported_size: int | None = None) -> None:
        self.payload = payload
        self.reported_size = reported_size if reported_size is not None else len(payload)
        self.body = FakeStreamingBody(payload)
        self.head_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.head_calls.append((Bucket, Key))
        return {
            "ContentLength": self.reported_size,
            "ETag": '"fake-etag"',
            "ContentType": "application/octet-stream",
        }

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, object]:
        self.get_calls.append((Bucket, Key))
        return {"Body": self.body}


def build_client(monkeypatch: pytest.MonkeyPatch, fake_s3: FakeS3Client) -> S3MultipartObjectStorageClient:
    client = S3MultipartObjectStorageClient(
        endpoint_url="http://example.invalid",
        access_key="test-access",
        secret_key="test-secret",
        chunk_size_bytes=4,
    )
    monkeypatch.setattr(client, "_create_client", lambda: fake_s3)
    return client


def test_download_file_streams_via_get_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = b"streamed-payload"
    fake_s3 = FakeS3Client(payload=payload)
    client = build_client(monkeypatch, fake_s3)

    local_path = tmp_path / "downloaded.bin"
    result = client.download_file(
        bucket="test-bucket",
        key="path/to/object.bin",
        local_path=local_path,
    )

    assert result.size_bytes == len(payload)
    assert result.etag == "fake-etag"
    assert result.content_type == "application/octet-stream"
    assert local_path.read_bytes() == payload
    assert fake_s3.head_calls == [("test-bucket", "path/to/object.bin")]
    assert fake_s3.get_calls == [("test-bucket", "path/to/object.bin")]
    assert fake_s3.body.closed is True


def test_download_file_rejects_size_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_s3 = FakeS3Client(payload=b"payload", reported_size=999)
    client = build_client(monkeypatch, fake_s3)

    with pytest.raises(ObjectStorageVerificationError):
        client.download_file(
            bucket="test-bucket",
            key="path/to/object.bin",
            local_path=tmp_path / "downloaded.bin",
        )

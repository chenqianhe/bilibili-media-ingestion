from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel


class ObjectStorageError(Exception):
    error_code = "storage_upload_failed"


class ObjectStorageConfigurationError(ObjectStorageError):
    error_code = "storage_not_configured"


class ObjectStorageUploadError(ObjectStorageError):
    error_code = "storage_upload_failed"


class ObjectStorageDownloadError(ObjectStorageError):
    error_code = "storage_download_failed"


class ObjectStorageDeleteError(ObjectStorageError):
    error_code = "storage_cleanup_failed"


class ObjectStorageVerificationError(ObjectStorageError):
    error_code = "storage_upload_verification_failed"


class ObjectStorageResult(BaseModel):
    bucket: str
    key: str
    size_bytes: int
    etag: str | None = None
    content_type: str | None = None


class ObjectStorageClient(Protocol):
    def multipart_upload_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectStorageResult:
        ...

    def download_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
    ) -> ObjectStorageResult:
        ...

    def delete_object(
        self,
        *,
        bucket: str,
        key: str,
    ) -> None:
        ...

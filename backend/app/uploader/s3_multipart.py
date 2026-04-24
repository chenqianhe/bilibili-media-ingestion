from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from app.core.config import settings
from app.uploader.base import (
    ObjectStorageClient,
    ObjectStorageConfigurationError,
    ObjectStorageDeleteError,
    ObjectStorageDownloadError,
    ObjectStorageResult,
    ObjectStorageUploadError,
    ObjectStorageVerificationError,
)


def _normalize_etag(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().strip('"') or None


class S3MultipartObjectStorageClient(ObjectStorageClient):
    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
        force_path_style: bool | None = None,
        chunk_size_bytes: int | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url or settings.S3_ENDPOINT_URL
        self._access_key = access_key or settings.S3_ACCESS_KEY
        self._secret_key = secret_key or settings.S3_SECRET_KEY
        self._region = region or settings.S3_REGION or "us-east-1"
        self._force_path_style = (
            settings.S3_FORCE_PATH_STYLE
            if force_path_style is None
            else force_path_style
        )
        self._chunk_size_bytes = (
            chunk_size_bytes or settings.S3_MULTIPART_CHUNK_SIZE_BYTES
        )

        if not self._access_key or not self._secret_key:
            raise ObjectStorageConfigurationError(
                "S3 credentials are not configured for the upload worker"
            )
        if self._chunk_size_bytes <= 0:
            raise ObjectStorageConfigurationError(
                "S3 multipart chunk size must be greater than zero"
            )

    def multipart_upload_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> ObjectStorageResult:
        if not local_path.is_file():
            raise ObjectStorageUploadError(f"Local upload source does not exist: {local_path}")

        client = self._create_client()
        file_size = local_path.stat().st_size
        upload_id: str | None = None
        completed = False
        try:
            create_kwargs: dict[str, Any] = {
                "Bucket": bucket,
                "Key": key,
            }
            if content_type:
                create_kwargs["ContentType"] = content_type
            if metadata:
                create_kwargs["Metadata"] = metadata

            created = client.create_multipart_upload(**create_kwargs)
            upload_id = cast(str | None, created.get("UploadId"))
            if not upload_id:
                raise ObjectStorageUploadError(
                    "Object storage did not return an upload id"
                )

            parts: list[dict[str, Any]] = []
            with local_path.open("rb") as handle:
                part_number = 1
                while chunk := handle.read(self._chunk_size_bytes):
                    response = client.upload_part(
                        Bucket=bucket,
                        Key=key,
                        UploadId=upload_id,
                        PartNumber=part_number,
                        Body=chunk,
                    )
                    etag = _normalize_etag(cast(str | None, response.get("ETag")))
                    if etag is None:
                        raise ObjectStorageUploadError(
                            f"Object storage did not return an ETag for part {part_number}"
                        )
                    parts.append(
                        {
                            "ETag": etag,
                            "PartNumber": part_number,
                        }
                    )
                    part_number += 1

            completed_response = client.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            completed = True
            remote_etag = _normalize_etag(
                cast(str | None, completed_response.get("ETag"))
            )

            head = client.head_object(Bucket=bucket, Key=key)
            verified_size = cast(int | None, head.get("ContentLength"))
            if verified_size != file_size:
                client.delete_object(Bucket=bucket, Key=key)
                raise ObjectStorageVerificationError(
                    f"Object storage size verification failed for {bucket}/{key}"
                )

            head_etag = _normalize_etag(cast(str | None, head.get("ETag")))
            if remote_etag and head_etag and remote_etag != head_etag:
                client.delete_object(Bucket=bucket, Key=key)
                raise ObjectStorageVerificationError(
                    f"Object storage ETag verification failed for {bucket}/{key}"
                )

            verified_content_type = cast(str | None, head.get("ContentType"))
            return ObjectStorageResult(
                bucket=bucket,
                key=key,
                size_bytes=file_size,
                etag=head_etag or remote_etag,
                content_type=verified_content_type or content_type,
            )
        except ObjectStorageVerificationError:
            raise
        except Exception as exc:
            if upload_id and not completed:
                try:
                    client.abort_multipart_upload(
                        Bucket=bucket,
                        Key=key,
                        UploadId=upload_id,
                    )
                except Exception:
                    pass
            if isinstance(exc, ObjectStorageUploadError):
                raise
            raise ObjectStorageUploadError(
                f"Object storage multipart upload failed for {bucket}/{key}: {exc}"
            ) from exc

    def download_file(
        self,
        *,
        bucket: str,
        key: str,
        local_path: Path,
    ) -> ObjectStorageResult:
        client = self._create_client()
        local_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            head = client.head_object(Bucket=bucket, Key=key)
            response = client.get_object(Bucket=bucket, Key=key)
            body = cast(Any, response["Body"])
            with local_path.open("wb") as handle:
                for chunk in body.iter_chunks(chunk_size=self._chunk_size_bytes):
                    if chunk:
                        handle.write(chunk)
            body.close()
        except Exception as exc:
            raise ObjectStorageDownloadError(
                f"Object storage download failed for {bucket}/{key}: {exc}"
            ) from exc

        if not local_path.is_file():
            raise ObjectStorageDownloadError(
                f"Object storage download did not create a local file for {bucket}/{key}"
            )

        expected_size = cast(int | None, head.get("ContentLength"))
        actual_size = local_path.stat().st_size
        if expected_size is not None and actual_size != expected_size:
            raise ObjectStorageVerificationError(
                f"Object storage download size verification failed for {bucket}/{key}"
            )

        return ObjectStorageResult(
            bucket=bucket,
            key=key,
            size_bytes=actual_size,
            etag=_normalize_etag(cast(str | None, head.get("ETag"))),
            content_type=cast(str | None, head.get("ContentType")),
        )

    def delete_object(
        self,
        *,
        bucket: str,
        key: str,
    ) -> None:
        client = self._create_client()
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise ObjectStorageDeleteError(
                f"Object storage delete failed for {bucket}/{key}: {exc}"
            ) from exc

    def _create_client(self) -> Any:
        try:
            from botocore.client import Config
            from botocore.session import get_session
        except ModuleNotFoundError as exc:
            raise ObjectStorageConfigurationError(
                "botocore is required to run the upload worker"
            ) from exc

        session = get_session()
        return session.create_client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            config=Config(
                s3={"addressing_style": "path" if self._force_path_style else "auto"}
            ),
        )

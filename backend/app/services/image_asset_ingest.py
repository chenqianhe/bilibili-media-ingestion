from __future__ import annotations

import hashlib
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.crawler.bilibili_web import BilibiliWebClient
from app.ingest_models import IngestJob, MediaAsset
from app.services.storage_keys import build_asset_storage_key
from app.services.text_sanitization import strip_nul_bytes, strip_nul_text
from app.uploader.base import ObjectStorageClient

_IMAGE_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_IMAGE_URL_KEYS = {
    "avatar_url",
    "cover_url",
    "face",
    "img_src",
    "img_url",
    "pic",
    "source_url",
    "sub_url",
    "subtitle_url",
    "url",
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalize_content_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip() or None


def _filename_from_source_url(
    source_url: str,
    *,
    content_type: str | None,
    fallback_stem: str,
) -> str:
    normalized_content_type = _normalize_content_type(content_type)
    filename: str | None = None
    try:
        raw_name = Path(httpx.URL(source_url).path).name
    except ValueError:
        raw_name = ""

    if raw_name:
        candidate = raw_name.split("@", 1)[0].strip()
        if candidate:
            filename = candidate

    if filename is not None:
        return filename

    extension = _IMAGE_CONTENT_TYPE_EXTENSIONS.get(normalized_content_type or "")
    if extension is None and normalized_content_type is not None:
        extension = mimetypes.guess_extension(normalized_content_type)
    return f"{fallback_stem}{extension or '.bin'}"


def strip_url_fields(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized_key = strip_nul_text(key) if isinstance(key, str) else key
            normalized_key = (
                sanitized_key.lower()
                if isinstance(sanitized_key, str)
                else str(sanitized_key).lower()
            )
            if normalized_key in _IMAGE_URL_KEYS:
                continue
            sanitized[sanitized_key] = strip_url_fields(item)
        return sanitized
    if isinstance(value, list):
        return [strip_url_fields(item) for item in value]
    if isinstance(value, tuple):
        return [strip_url_fields(item) for item in value]
    if isinstance(value, str):
        return strip_nul_text(value)
    return value


def _find_existing_asset_by_source_hash(
    session: Session,
    *,
    asset_type: str,
    source_url_hash: str,
) -> MediaAsset | None:
    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.asset_type == asset_type,
            MediaAsset.original_url_hash == source_url_hash,
            MediaAsset.status.in_(("uploaded", "ready")),
        )
        .order_by(MediaAsset.ready_at.desc(), MediaAsset.created_at.desc())
        .limit(1)
    )
    return session.exec(statement).first()


def _find_existing_asset_by_sha256(
    session: Session,
    *,
    asset_type: str,
    sha256: str,
) -> MediaAsset | None:
    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.asset_type == asset_type,
            MediaAsset.sha256 == sha256,
            MediaAsset.status.in_(("uploaded", "ready")),
        )
        .order_by(MediaAsset.ready_at.desc(), MediaAsset.created_at.desc())
        .limit(1)
    )
    return session.exec(statement).first()


def _build_reused_asset(
    *,
    job: IngestJob,
    bvid: str,
    cid: int | None,
    asset_type: str,
    variant: str | None,
    source_url_hash: str,
    existing_asset: MediaAsset,
    metadata_json: dict[str, Any] | None,
    width: int | None,
    height: int | None,
) -> MediaAsset:
    reused_at = _now_utc()
    payload = dict(strip_nul_bytes(strip_url_fields(metadata_json or {})))
    payload["reused_from_asset_id"] = str(existing_asset.id)
    payload["uploaded_at"] = reused_at.isoformat()
    source_sha256 = existing_asset.sha256
    if not source_sha256:
        inherited_source_sha256 = existing_asset.metadata_json.get("source_sha256")
        if isinstance(inherited_source_sha256, str) and inherited_source_sha256.strip():
            source_sha256 = inherited_source_sha256.strip()
    if source_sha256:
        payload["source_sha256"] = source_sha256

    return MediaAsset(
        bvid=bvid,
        cid=cid,
        job_id=job.id,
        asset_type=asset_type,
        variant=variant,
        status="ready",
        s3_bucket=existing_asset.s3_bucket,
        s3_key=existing_asset.s3_key,
        s3_region=existing_asset.s3_region,
        storage_class=existing_asset.storage_class,
        original_url_hash=source_url_hash,
        filename=existing_asset.filename,
        content_type=existing_asset.content_type,
        container_format=existing_asset.container_format,
        width=existing_asset.width or width,
        height=existing_asset.height or height,
        size_bytes=existing_asset.size_bytes,
        # Reused rows point at an existing canonical asset; keep the original
        # digest in metadata_json instead of competing for the unique ready/uploaded
        # sha256 slot.
        sha256=None,
        etag=existing_asset.etag,
        metadata_json=payload,
        ready_at=reused_at,
    )


def store_remote_image_asset(
    session: Session,
    *,
    job: IngestJob,
    bvid: str,
    asset_type: str,
    source_url: str,
    web_client: BilibiliWebClient,
    storage_client: ObjectStorageClient,
    temp_dir: Path,
    referer: str | None = None,
    cid: int | None = None,
    variant: str | None = None,
    source_type: str,
    fallback_stem: str,
    width: int | None = None,
    height: int | None = None,
    metadata_json: dict[str, Any] | None = None,
    upload_metadata: dict[str, str] | None = None,
) -> MediaAsset:
    bucket = settings.S3_BUCKET
    if not bucket:
        raise ValueError("S3 bucket is not configured for image uploads")

    source_url_hash = _hash_text(source_url)
    existing_asset = _find_existing_asset_by_source_hash(
        session,
        asset_type=asset_type,
        source_url_hash=source_url_hash,
    )
    if existing_asset is not None:
        if existing_asset.bvid == bvid:
            return existing_asset
        reused_asset = _build_reused_asset(
            job=job,
            bvid=bvid,
            cid=cid,
            asset_type=asset_type,
            variant=variant,
            source_url_hash=source_url_hash,
            existing_asset=existing_asset,
            metadata_json=metadata_json,
            width=width,
            height=height,
        )
        session.add(reused_asset)
        return reused_asset

    image_bytes, content_type = web_client.request_bytes(
        source_url,
        context=f"{source_type} {source_url}",
        referer=referer,
    )
    filename = _filename_from_source_url(
        source_url,
        content_type=content_type,
        fallback_stem=fallback_stem,
    )
    temp_dir.mkdir(parents=True, exist_ok=True)
    local_path = temp_dir / filename
    local_path.write_bytes(image_bytes)

    sha256 = _sha256_bytes(image_bytes)
    existing_asset = _find_existing_asset_by_sha256(
        session,
        asset_type=asset_type,
        sha256=sha256,
    )
    if existing_asset is not None:
        if existing_asset.bvid == bvid:
            return existing_asset
        reused_asset = _build_reused_asset(
            job=job,
            bvid=bvid,
            cid=cid,
            asset_type=asset_type,
            variant=variant,
            source_url_hash=source_url_hash,
            existing_asset=existing_asset,
            metadata_json=metadata_json,
            width=width,
            height=height,
        )
        session.add(reused_asset)
        return reused_asset

    uploaded_at = _now_utc()
    payload = dict(strip_nul_bytes(strip_url_fields(metadata_json or {})))
    payload["uploaded_at"] = uploaded_at.isoformat()

    asset = MediaAsset(
        bvid=bvid,
        cid=cid,
        job_id=job.id,
        asset_type=asset_type,
        variant=variant,
        status="ready",
        s3_bucket=bucket,
        s3_region=settings.S3_REGION,
        original_url_hash=source_url_hash,
        filename=filename,
        content_type=_normalize_content_type(content_type),
        container_format=Path(filename).suffix.lower().lstrip(".") or None,
        width=width,
        height=height,
        size_bytes=len(image_bytes),
        sha256=sha256,
        metadata_json=payload,
        ready_at=uploaded_at,
    )
    asset.s3_key = build_asset_storage_key(
        asset_type=asset_type,
        bvid=bvid,
        cid=cid,
        asset_id=asset.id,
        filename=asset.filename,
    )

    result = storage_client.multipart_upload_file(
        bucket=bucket,
        key=asset.s3_key or "",
        local_path=local_path,
        content_type=asset.content_type,
        metadata={
            "source_type": source_type,
            "source_url_hash": source_url_hash,
            **(upload_metadata or {}),
        },
    )
    asset.s3_bucket = result.bucket
    asset.s3_key = result.key
    asset.size_bytes = result.size_bytes
    asset.etag = result.etag
    asset.content_type = result.content_type or asset.content_type
    session.add(asset)
    return asset

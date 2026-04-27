import shutil
import tempfile
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from sqlmodel import Session

from app.api.deps import CurrentUser, ObjectStorageClientDep, SessionDep
from app.core.config import settings
from app.ingest_models import (
    MediaAsset,
    MediaAssetDetailPublic,
    MediaAssetDownloadDescriptor,
    SignedUrlRequest,
    SignedUrlResponse,
    Video,
)
from app.services.audit import record_audit_event
from app.services.signed_urls import (
    build_media_download_url,
    build_media_playback_url,
    verify_media_download_token,
    verify_media_playback_token,
)
from app.uploader.base import ObjectStorageDownloadError

router = APIRouter(prefix="/media", tags=["media"])


def _assert_asset_available(session: Session, asset: MediaAsset) -> None:
    if asset.status not in {"uploaded", "ready"}:
        raise HTTPException(status_code=409, detail="Asset is not ready for access")
    video = session.get(Video, asset.bvid)
    if asset.status == "takedown" or (video and video.takedown_status == "takedown"):
        raise HTTPException(status_code=410, detail="Asset has been taken down")


def _require_asset(*, session: Session, asset_id: uuid.UUID) -> MediaAsset:
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return asset


def _to_media_asset_detail_public(asset: MediaAsset) -> MediaAssetDetailPublic:
    return MediaAssetDetailPublic(
        asset_id=asset.id,
        bvid=asset.bvid,
        cid=asset.cid,
        asset_type=asset.asset_type,
        variant=asset.variant,
        status=asset.status,
        filename=asset.filename,
        content_type=asset.content_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        container_format=asset.container_format,
        video_codec=asset.video_codec,
        audio_codec=asset.audio_codec,
        width=asset.width,
        height=asset.height,
        duration_seconds=asset.duration_seconds,
        created_at=asset.created_at,
        ready_at=asset.ready_at,
        s3_bucket=asset.s3_bucket,
        s3_key=asset.s3_key,
    )


def _is_hls_playlist_asset(asset: MediaAsset) -> bool:
    hls_role = asset.metadata_json.get("hls_role")
    return asset.asset_type == "hls_master" or hls_role in {
        "master_playlist",
        "media_playlist",
    }


def _download_asset_to_temp(
    *, storage_client: ObjectStorageClientDep, asset: MediaAsset
) -> tuple[Path, Path]:
    if not asset.s3_bucket or not asset.s3_key:
        raise HTTPException(
            status_code=409,
            detail="Media asset is missing object storage location data",
        )

    base_temp_dir = Path(settings.INGEST_TMP_DIR)
    base_temp_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(
        tempfile.mkdtemp(
            prefix="media-playback-",
            dir=str(base_temp_dir),
        )
    )
    filename = asset.filename or f"{asset.id}{Path(asset.s3_key).suffix}"
    local_path = temp_dir / filename
    try:
        storage_client.download_file(
            bucket=asset.s3_bucket,
            key=asset.s3_key,
            local_path=local_path,
        )
    except ObjectStorageDownloadError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(
            status_code=502,
            detail=f"Media object could not be downloaded: {exc}",
        ) from exc
    return temp_dir, local_path


def _parse_range_header(
    *, range_header: str | None, file_size: int
) -> tuple[int, int] | None:
    if not range_header:
        return None
    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Unsupported range unit")

    range_value = range_header[len("bytes=") :].strip()
    if "," in range_value:
        raise HTTPException(status_code=416, detail="Multiple byte ranges are unsupported")

    start_raw, _, end_raw = range_value.partition("-")
    if not start_raw and not end_raw:
        raise HTTPException(status_code=416, detail="Invalid byte range")

    try:
        if not start_raw:
            length = int(end_raw)
            if length <= 0:
                raise ValueError
            start = max(file_size - length, 0)
            end = file_size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else file_size - 1
    except ValueError as exc:
        raise HTTPException(status_code=416, detail="Invalid byte range") from exc

    if start < 0 or start >= file_size or end < start:
        raise HTTPException(
            status_code=416,
            detail="Requested range is not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    return start, min(end, file_size - 1)


def _iter_file(
    *, local_path: Path, temp_dir: Path, start: int = 0, end: int | None = None
) -> Iterator[bytes]:
    try:
        with local_path.open("rb") as handle:
            handle.seek(start)
            remaining = None if end is None else end - start + 1
            while True:
                read_size = 1024 * 1024
                if remaining is not None:
                    if remaining <= 0:
                        break
                    read_size = min(read_size, remaining)
                chunk = handle.read(read_size)
                if not chunk:
                    break
                yield chunk
                if remaining is not None:
                    remaining -= len(chunk)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _remaining_seconds(expires_at: datetime) -> int:
    remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return max(1, remaining)


def _rewrite_hls_manifest(*, asset: MediaAsset, manifest_text: str, expires_at: datetime) -> str:
    raw_references = asset.metadata_json.get("hls_references")
    if not isinstance(raw_references, list):
        return manifest_text

    remaining_seconds = _remaining_seconds(expires_at)
    replacement_by_uri: dict[str, str] = {}
    for reference in raw_references:
        if not isinstance(reference, dict):
            continue
        uri = reference.get("uri")
        asset_id = reference.get("asset_id")
        if not isinstance(uri, str) or not isinstance(asset_id, str):
            continue
        try:
            child_asset_id = uuid.UUID(asset_id)
        except ValueError:
            continue
        replacement_by_uri[uri] = build_media_playback_url(
            asset_id=child_asset_id,
            expires_in=remaining_seconds,
        )

    rewritten_lines: list[str] = []
    for line in manifest_text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            rewritten_lines.append(replacement_by_uri.get(stripped, line))
        else:
            rewritten_lines.append(line)
    return "\n".join(rewritten_lines) + ("\n" if manifest_text.endswith("\n") else "")


@router.get("/assets/{asset_id}", response_model=MediaAssetDetailPublic)
def read_media_asset(
    *, session: SessionDep, current_user: CurrentUser, asset_id: uuid.UUID
) -> Any:
    del current_user
    asset = _require_asset(session=session, asset_id=asset_id)
    return _to_media_asset_detail_public(asset)


@router.post("/assets/{asset_id}/signed-url", response_model=SignedUrlResponse)
def create_media_signed_url(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    asset_id: uuid.UUID,
    payload: SignedUrlRequest,
) -> Any:
    asset = _require_asset(session=session, asset_id=asset_id)
    _assert_asset_available(session, asset)
    url = build_media_download_url(asset_id=asset.id, expires_in=payload.expires_in)
    record_audit_event(
        session=session,
        actor=current_user.email,
        action="media_asset.signed_url_created",
        resource_type="media_asset",
        resource_id=str(asset.id),
        message="Created a media asset access URL",
        payload={"expires_in": payload.expires_in},
    )
    session.commit()
    return SignedUrlResponse(url=url, expires_in=payload.expires_in)


@router.post("/assets/{asset_id}/playback-url", response_model=SignedUrlResponse)
def create_media_playback_url_response(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    asset_id: uuid.UUID,
    payload: SignedUrlRequest,
) -> Any:
    asset = _require_asset(session=session, asset_id=asset_id)
    _assert_asset_available(session, asset)
    url = build_media_playback_url(asset_id=asset.id, expires_in=payload.expires_in)
    record_audit_event(
        session=session,
        actor=current_user.email,
        action="media_asset.playback_url_created",
        resource_type="media_asset",
        resource_id=str(asset.id),
        message="Created a media playback URL",
        payload={"expires_in": payload.expires_in},
    )
    session.commit()
    return SignedUrlResponse(url=url, expires_in=payload.expires_in)


@router.get("/assets/{asset_id}/download", response_model=MediaAssetDownloadDescriptor)
def read_media_download_descriptor(
    *, session: SessionDep, asset_id: uuid.UUID, token: str = Query(min_length=1)
) -> Any:
    asset = _require_asset(session=session, asset_id=asset_id)
    _assert_asset_available(session, asset)
    try:
        expires_at = verify_media_download_token(asset_id=asset_id, token=token)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return MediaAssetDownloadDescriptor(
        asset_id=asset.id,
        bvid=asset.bvid,
        s3_bucket=asset.s3_bucket,
        s3_key=asset.s3_key,
        filename=asset.filename,
        content_type=asset.content_type,
        expires_at=expires_at,
    )


@router.get("/assets/{asset_id}/playback")
def proxy_media_asset(
    *,
    session: SessionDep,
    storage_client: ObjectStorageClientDep,
    request: Request,
    asset_id: uuid.UUID,
    token: str = Query(min_length=1),
) -> Response:
    asset = _require_asset(session=session, asset_id=asset_id)
    _assert_asset_available(session, asset)
    try:
        expires_at = verify_media_playback_token(asset_id=asset_id, token=token)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    temp_dir, local_path = _download_asset_to_temp(
        storage_client=storage_client,
        asset=asset,
    )
    if _is_hls_playlist_asset(asset):
        try:
            manifest_text = local_path.read_text(encoding="utf-8")
            rewritten_manifest = _rewrite_hls_manifest(
                asset=asset,
                manifest_text=manifest_text,
                expires_at=expires_at,
            )
            return Response(
                content=rewritten_manifest,
                media_type=asset.content_type or "application/vnd.apple.mpegurl",
                headers={"Cache-Control": "private, max-age=0"},
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    file_size = local_path.stat().st_size
    byte_range = _parse_range_header(
        range_header=request.headers.get("range"),
        file_size=file_size,
    )
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=0",
    }
    status_code = 200
    start = 0
    end = file_size - 1
    if byte_range is not None:
        start, end = byte_range
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(end - start + 1)
    else:
        headers["Content-Length"] = str(file_size)

    return StreamingResponse(
        _iter_file(local_path=local_path, temp_dir=temp_dir, start=start, end=end),
        media_type=asset.content_type or "application/octet-stream",
        status_code=status_code,
        headers=headers,
    )

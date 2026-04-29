from __future__ import annotations

import hashlib
import mimetypes
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import settings
from app.ingest_models import IngestJob, MediaAsset
from app.processor.base import (
    MediaProbeResult,
    MediaProcessingError,
    MediaProcessor,
    MediaResultError,
)
from app.services.audit import record_audit_event
from app.services.storage_keys import build_asset_storage_key
from app.uploader.base import ObjectStorageClient, ObjectStorageError

_SOURCE_ASSET_TYPES = (
    "source_archive",
    "source_video_stream",
    "source_audio_stream",
)
_SPECIAL_CONTENT_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
}


@dataclass
class PreparedSourceAsset:
    asset: MediaAsset
    local_path: Path
    probe: MediaProbeResult


@dataclass
class DerivativeBundle:
    cid: int | None
    reference_asset: PreparedSourceAsset
    video_source: PreparedSourceAsset
    audio_source: PreparedSourceAsset | None = None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _merge_progress(job: IngestJob, *, payload: dict[str, object]) -> None:
    progress = dict(job.progress)
    progress.update(payload)
    job.progress = progress


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _content_type_for(path: Path) -> str | None:
    special_content_type = _SPECIAL_CONTENT_TYPES.get(path.suffix.lower())
    if special_content_type is not None:
        return special_content_type
    content_type, _ = mimetypes.guess_type(path.name)
    return content_type


def _processing_workspace_dir(job_id: uuid.UUID) -> Path:
    return Path(settings.INGEST_TMP_DIR) / "jobs" / str(job_id) / "processing"


def _source_assets_for_job(session: Session, *, job_id: uuid.UUID) -> list[MediaAsset]:
    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.job_id == job_id,
            MediaAsset.asset_type.in_(_SOURCE_ASSET_TYPES),
            MediaAsset.status.in_(("uploaded", "ready")),
        )
        .order_by(MediaAsset.created_at.asc())
    )
    return list(session.exec(statement).all())


def _find_existing_ready_asset(
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


def _cleanup_workspace(path: Path | None) -> None:
    if path is None:
        return
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _thumbnail_offset(duration_seconds: float | None) -> float:
    if duration_seconds is None or duration_seconds <= 0:
        return 1.0
    return min(max(duration_seconds * 0.1, 1.0), 30.0)


def _safe_stem(filename: str | None) -> str:
    if filename:
        candidate = Path(filename).stem.strip()
        if candidate:
            return candidate
    return "source"


def _variant_for_hls_file(relative_path: Path) -> str:
    kind = "playlist" if relative_path.suffix.lower() == ".m3u8" else "segment"
    return f"{kind}:{relative_path.stem}"[:128]


def _apply_probe_metadata(
    *,
    asset: MediaAsset,
    probe: MediaProbeResult,
    probed_at: datetime,
) -> None:
    asset.container_format = probe.container_format or asset.container_format
    asset.video_codec = probe.video_codec or asset.video_codec
    asset.audio_codec = probe.audio_codec or asset.audio_codec
    asset.width = probe.width if probe.width is not None else asset.width
    asset.height = probe.height if probe.height is not None else asset.height
    asset.fps = probe.fps if probe.fps is not None else asset.fps
    asset.bitrate = probe.bitrate if probe.bitrate is not None else asset.bitrate
    asset.duration_seconds = (
        probe.duration_seconds
        if probe.duration_seconds is not None
        else asset.duration_seconds
    )
    metadata_json = dict(asset.metadata_json)
    metadata_json["probed_at"] = probed_at.isoformat()
    metadata_json["ffprobe"] = probe.raw
    asset.metadata_json = metadata_json


def _mark_asset_as_reused(
    *,
    asset: MediaAsset,
    existing_asset: MediaAsset,
    ready_at: datetime,
    derived_sha256: str,
) -> None:
    metadata_json = dict(asset.metadata_json)
    metadata_json["uploaded_at"] = ready_at.isoformat()
    metadata_json["uploaded_bucket"] = existing_asset.s3_bucket
    metadata_json["uploaded_key"] = existing_asset.s3_key
    metadata_json["verified_size_bytes"] = existing_asset.size_bytes
    metadata_json["verified_etag"] = existing_asset.etag
    metadata_json["reused_from_asset_id"] = str(existing_asset.id)
    metadata_json["derived_sha256"] = derived_sha256

    asset.status = "ready"
    asset.s3_bucket = existing_asset.s3_bucket
    asset.s3_key = existing_asset.s3_key
    asset.s3_region = existing_asset.s3_region
    asset.storage_class = existing_asset.storage_class
    asset.size_bytes = existing_asset.size_bytes
    asset.etag = existing_asset.etag
    asset.content_type = existing_asset.content_type or asset.content_type
    asset.container_format = existing_asset.container_format or asset.container_format
    asset.video_codec = existing_asset.video_codec or asset.video_codec
    asset.audio_codec = existing_asset.audio_codec or asset.audio_codec
    asset.width = existing_asset.width or asset.width
    asset.height = existing_asset.height or asset.height
    asset.fps = existing_asset.fps or asset.fps
    asset.bitrate = existing_asset.bitrate or asset.bitrate
    asset.duration_seconds = existing_asset.duration_seconds or asset.duration_seconds
    asset.ready_at = ready_at
    asset.sha256 = None
    asset.metadata_json = metadata_json


def _build_derivative_bundles(
    assets: list[PreparedSourceAsset],
) -> list[DerivativeBundle]:
    by_cid: dict[int | None, list[PreparedSourceAsset]] = {}
    for prepared in assets:
        by_cid.setdefault(prepared.asset.cid, []).append(prepared)

    bundles: list[DerivativeBundle] = []
    for cid, grouped_assets in by_cid.items():
        source_archive = next(
            (asset for asset in grouped_assets if asset.asset.asset_type == "source_archive"),
            None,
        )
        if source_archive is not None:
            bundles.append(
                DerivativeBundle(
                    cid=cid,
                    reference_asset=source_archive,
                    video_source=source_archive,
                )
            )
            continue

        video_stream = next(
            (
                asset
                for asset in grouped_assets
                if asset.asset.asset_type == "source_video_stream"
            ),
            None,
        )
        if video_stream is None:
            continue

        audio_stream = next(
            (
                asset
                for asset in grouped_assets
                if asset.asset.asset_type == "source_audio_stream" and asset.probe.has_audio
            ),
            None,
        )
        bundles.append(
            DerivativeBundle(
                cid=cid,
                reference_asset=video_stream,
                video_source=video_stream,
                audio_source=audio_stream,
            )
        )

    return bundles


def _prepare_derivative_asset(
    *,
    job: IngestJob,
    reference_asset: MediaAsset,
    asset_type: str,
    variant: str,
    filename: str,
    probe: MediaProbeResult | None,
    sha256: str,
    created_at: datetime,
    source_assets: list[MediaAsset],
    extra_metadata: dict[str, object] | None = None,
) -> MediaAsset:
    metadata_json = {
        "generated_at": created_at.isoformat(),
        "generator": "ffmpeg",
        "source_asset_ids": [str(source_asset.id) for source_asset in source_assets],
        "source_asset_types": [source_asset.asset_type for source_asset in source_assets],
    }
    if extra_metadata:
        metadata_json.update(extra_metadata)

    asset = MediaAsset(
        bvid=reference_asset.bvid,
        cid=reference_asset.cid,
        job_id=job.id,
        asset_type=asset_type,
        variant=variant,
        status="pending",
        s3_bucket=reference_asset.s3_bucket,
        s3_region=reference_asset.s3_region,
        original_url_hash=reference_asset.original_url_hash,
        filename=filename,
        content_type=_content_type_for(Path(filename)),
        size_bytes=None,
        sha256=sha256,
        metadata_json=metadata_json,
    )
    asset.s3_key = build_asset_storage_key(
        asset_type=asset.asset_type,
        bvid=asset.bvid,
        cid=asset.cid,
        asset_id=asset.id,
        filename=asset.filename,
    )
    if probe is not None:
        _apply_probe_metadata(asset=asset, probe=probe, probed_at=created_at)
    return asset


def _persist_derivative_asset(
    *,
    session: Session,
    job: IngestJob,
    reference_asset: MediaAsset,
    asset_type: str,
    variant: str,
    filename: str,
    local_path: Path,
    processor: MediaProcessor,
    storage_client: ObjectStorageClient,
    uploaded_objects: list[tuple[str, str]],
    source_assets: list[MediaAsset],
    probe_with_processor: bool = True,
    allow_reuse: bool = True,
    extra_metadata: dict[str, object] | None = None,
) -> MediaAsset:
    created_at = _now_utc()
    probe = processor.probe(input_path=local_path) if probe_with_processor else None
    sha256 = _sha256_file(local_path)
    asset = _prepare_derivative_asset(
        job=job,
        reference_asset=reference_asset,
        asset_type=asset_type,
        variant=variant,
        filename=filename,
        probe=probe,
        sha256=sha256,
        created_at=created_at,
        source_assets=source_assets,
        extra_metadata=extra_metadata,
    )

    existing_asset = (
        _find_existing_ready_asset(
            session,
            asset_type=asset.asset_type,
            sha256=sha256,
        )
        if allow_reuse
        else None
    )
    if existing_asset is not None:
        _mark_asset_as_reused(
            asset=asset,
            existing_asset=existing_asset,
            ready_at=created_at,
            derived_sha256=sha256,
        )
        session.add(asset)
        return asset

    if not allow_reuse:
        metadata_json = dict(asset.metadata_json)
        metadata_json["derived_sha256"] = sha256
        asset.metadata_json = metadata_json
        asset.sha256 = None

    if not asset.s3_bucket or not asset.s3_key:
        raise ValueError(
            f"Media asset {asset.id} is missing object storage location data"
        )

    result = storage_client.multipart_upload_file(
        bucket=asset.s3_bucket,
        key=asset.s3_key,
        local_path=local_path,
        content_type=asset.content_type,
        metadata={
            "bvid": asset.bvid,
            "asset_type": asset.asset_type,
            "job_id": str(job.id),
            "asset_id": str(asset.id),
        },
    )
    uploaded_objects.append((result.bucket, result.key))

    asset.status = "ready"
    asset.size_bytes = result.size_bytes
    asset.etag = result.etag
    asset.content_type = result.content_type or asset.content_type
    asset.ready_at = created_at
    metadata_json = dict(asset.metadata_json)
    metadata_json["uploaded_at"] = asset.ready_at.isoformat()
    metadata_json["uploaded_bucket"] = result.bucket
    metadata_json["uploaded_key"] = result.key
    metadata_json["verified_size_bytes"] = result.size_bytes
    metadata_json["verified_etag"] = result.etag
    asset.metadata_json = metadata_json
    session.add(asset)
    return asset


def _read_hls_manifest_references(
    *,
    manifest_path: Path,
    hls_root_dir: Path,
    assets_by_relative_path: dict[str, MediaAsset],
) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    hls_root_resolved = hls_root_dir.resolve()
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if (
            not line
            or line.startswith("#")
            or "://" in line
            or line.startswith("data:")
        ):
            continue

        target_path = (manifest_path.parent / line).resolve()
        try:
            relative_path = target_path.relative_to(hls_root_resolved).as_posix()
        except ValueError:
            continue

        asset = assets_by_relative_path.get(relative_path)
        if asset is None:
            continue
        references.append(
            {
                "uri": line,
                "asset_id": str(asset.id),
                "relative_path": relative_path,
            }
        )

    return references


def _persist_hls_bundle(
    *,
    session: Session,
    job: IngestJob,
    reference_asset: MediaAsset,
    base_stem: str,
    hls_root_dir: Path,
    master_playlist_path: Path,
    processor: MediaProcessor,
    storage_client: ObjectStorageClient,
    uploaded_objects: list[tuple[str, str]],
    source_assets: list[MediaAsset],
    proxy_asset: MediaAsset | None = None,
) -> list[MediaAsset]:
    assets_by_relative_path: dict[str, MediaAsset] = {}
    persisted_assets: list[MediaAsset] = []
    hls_extra_metadata = (
        {"proxy_asset_id": str(proxy_asset.id)} if proxy_asset is not None else {}
    )

    files = [
        path
        for path in sorted(hls_root_dir.rglob("*"))
        if path.is_file()
    ]
    master_playlist_resolved = master_playlist_path.resolve()

    for local_path in files:
        if local_path.resolve() == master_playlist_resolved:
            continue
        if local_path.suffix.lower() == ".m3u8":
            continue

        relative_path = local_path.relative_to(hls_root_dir)
        asset = _persist_derivative_asset(
            session=session,
            job=job,
            reference_asset=reference_asset,
            asset_type="hls_segment",
            variant=_variant_for_hls_file(relative_path),
            filename=f"{base_stem}.hls.{relative_path.name}",
            local_path=local_path,
            processor=processor,
            storage_client=storage_client,
            uploaded_objects=uploaded_objects,
            source_assets=source_assets,
            allow_reuse=False,
            extra_metadata={
                **hls_extra_metadata,
                "hls_role": "media_segment",
                "hls_relative_path": relative_path.as_posix(),
            },
        )
        assets_by_relative_path[relative_path.as_posix()] = asset
        persisted_assets.append(asset)

    for local_path in files:
        if local_path.resolve() == master_playlist_resolved:
            continue
        if local_path.suffix.lower() != ".m3u8":
            continue

        relative_path = local_path.relative_to(hls_root_dir)
        asset = _persist_derivative_asset(
            session=session,
            job=job,
            reference_asset=reference_asset,
            asset_type="hls_segment",
            variant=_variant_for_hls_file(relative_path),
            filename=f"{base_stem}.hls.{relative_path.name}",
            local_path=local_path,
            processor=processor,
            storage_client=storage_client,
            uploaded_objects=uploaded_objects,
            source_assets=source_assets,
            probe_with_processor=False,
            allow_reuse=False,
            extra_metadata={
                **hls_extra_metadata,
                "hls_role": "media_playlist",
                "hls_relative_path": relative_path.as_posix(),
                "hls_references": _read_hls_manifest_references(
                    manifest_path=local_path,
                    hls_root_dir=hls_root_dir,
                    assets_by_relative_path=assets_by_relative_path,
                ),
            },
        )
        assets_by_relative_path[relative_path.as_posix()] = asset
        persisted_assets.append(asset)

    master_asset = _persist_derivative_asset(
        session=session,
        job=job,
        reference_asset=reference_asset,
        asset_type="hls_master",
        variant="master",
        filename=f"{base_stem}.master.m3u8",
        local_path=master_playlist_path,
        processor=processor,
        storage_client=storage_client,
        uploaded_objects=uploaded_objects,
        source_assets=source_assets,
        probe_with_processor=False,
        allow_reuse=False,
        extra_metadata={
            **hls_extra_metadata,
            "hls_role": "master_playlist",
            "hls_relative_path": master_playlist_path.relative_to(hls_root_dir).as_posix(),
            "hls_references": _read_hls_manifest_references(
                manifest_path=master_playlist_path,
                hls_root_dir=hls_root_dir,
                assets_by_relative_path=assets_by_relative_path,
            ),
        },
    )
    persisted_assets.append(master_asset)
    return persisted_assets


def _generate_bundle_derivatives(
    *,
    session: Session,
    job: IngestJob,
    bundle: DerivativeBundle,
    output_dir: Path,
    processor: MediaProcessor,
    storage_client: ObjectStorageClient,
    uploaded_objects: list[tuple[str, str]],
) -> list[MediaAsset]:
    if not bundle.video_source.probe.has_video:
        raise MediaResultError(
            f"Source asset {bundle.video_source.asset.id} does not contain a video stream"
        )

    derivative_assets: list[MediaAsset] = []
    base_stem = _safe_stem(bundle.reference_asset.asset.filename)
    source_assets = [bundle.video_source.asset]
    if bundle.audio_source is not None:
        source_assets.append(bundle.audio_source.asset)
    include_audio_from_video_input = (
        bundle.audio_source is None and bundle.video_source.probe.has_audio
    )

    if bool(job.options.get("create_normalized_mp4", True)):
        normalized_path = output_dir / f"{bundle.reference_asset.asset.id}-normalized.mp4"
        processor.create_normalized_mp4(
            video_input_path=bundle.video_source.local_path,
            audio_input_path=(
                bundle.audio_source.local_path if bundle.audio_source is not None else None
            ),
            output_path=normalized_path,
            include_audio_from_video_input=include_audio_from_video_input,
        )
        derivative_assets.append(
            _persist_derivative_asset(
                session=session,
                job=job,
                reference_asset=bundle.reference_asset.asset,
                asset_type="normalized_mp4",
                variant="default",
                filename=f"{base_stem}.normalized.mp4",
                local_path=normalized_path,
                processor=processor,
                storage_client=storage_client,
                uploaded_objects=uploaded_objects,
                source_assets=source_assets,
            )
        )

    if bool(job.options.get("create_hls")):
        proxy_path = output_dir / f"{bundle.reference_asset.asset.id}-proxy.mp4"
        processor.create_proxy_mp4(
            video_input_path=bundle.video_source.local_path,
            audio_input_path=(
                bundle.audio_source.local_path if bundle.audio_source is not None else None
            ),
            output_path=proxy_path,
            include_audio_from_video_input=include_audio_from_video_input,
        )
        proxy_asset = _persist_derivative_asset(
            session=session,
            job=job,
            reference_asset=bundle.reference_asset.asset,
            asset_type="proxy_mp4",
            variant="playback",
            filename=f"{base_stem}.proxy.mp4",
            local_path=proxy_path,
            processor=processor,
            storage_client=storage_client,
            uploaded_objects=uploaded_objects,
            source_assets=source_assets,
        )
        derivative_assets.append(proxy_asset)

        hls_root_dir = output_dir / f"{bundle.reference_asset.asset.id}-hls"
        master_playlist_path = processor.create_hls_package(
            video_input_path=bundle.video_source.local_path,
            audio_input_path=(
                bundle.audio_source.local_path if bundle.audio_source is not None else None
            ),
            output_dir=hls_root_dir,
            include_audio_from_video_input=include_audio_from_video_input,
        )
        derivative_assets.extend(
            _persist_hls_bundle(
                session=session,
                job=job,
                reference_asset=bundle.reference_asset.asset,
                base_stem=base_stem,
                hls_root_dir=hls_root_dir,
                master_playlist_path=master_playlist_path,
                processor=processor,
                storage_client=storage_client,
                uploaded_objects=uploaded_objects,
                source_assets=source_assets,
                proxy_asset=proxy_asset,
            )
        )

    thumbnail_path = output_dir / f"{bundle.reference_asset.asset.id}-thumbnail.jpg"
    processor.create_thumbnail(
        video_input_path=bundle.video_source.local_path,
        output_path=thumbnail_path,
        offset_seconds=_thumbnail_offset(bundle.video_source.probe.duration_seconds),
    )
    derivative_assets.append(
        _persist_derivative_asset(
            session=session,
            job=job,
            reference_asset=bundle.reference_asset.asset,
            asset_type="thumbnail",
            variant="poster",
            filename=f"{base_stem}.thumbnail.jpg",
            local_path=thumbnail_path,
            processor=processor,
            storage_client=storage_client,
            uploaded_objects=uploaded_objects,
            source_assets=source_assets,
        )
    )
    return derivative_assets


def _start_media_processing(
    session: Session,
    *,
    job: IngestJob,
    started_at: datetime,
) -> None:
    job.status = "processing_media"
    job.phase = "processing uploaded source media"
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    _merge_progress(
        job,
        payload={
            "current_step": "media_processing",
            "last_transition_at": started_at.isoformat(),
            "next_step": "media_processing_worker",
        },
    )
    session.add(job)


def _complete_media_processing(
    session: Session,
    *,
    job: IngestJob,
    source_asset_ids: list[uuid.UUID],
    derivative_asset_ids: list[uuid.UUID],
    completed_at: datetime,
) -> None:
    job.status = "completed"
    job.phase = "media processed; derivative assets ready"
    job.finished_at = completed_at
    _merge_progress(
        job,
        payload={
            "current_step": "media_processed",
            "last_transition_at": completed_at.isoformat(),
            "next_step": "job_complete",
            "processing": {
                "source_asset_ids": [str(asset_id) for asset_id in source_asset_ids],
                "source_asset_count": len(source_asset_ids),
                "derivative_asset_ids": [
                    str(asset_id) for asset_id in derivative_asset_ids
                ],
                "derivative_asset_count": len(derivative_asset_ids),
                "completed_at": completed_at.isoformat(),
            },
        },
    )
    session.add(job)


def _fail_media_processing(
    session: Session,
    *,
    job: IngestJob,
    error_code: str,
    message: str,
    failed_at: datetime,
) -> None:
    job.status = "failed"
    job.phase = "media processing failed"
    job.error_code = error_code
    job.error_message = message
    job.finished_at = failed_at
    job.retry_count += 1
    _merge_progress(
        job,
        payload={
            "current_step": "media_processing_failed",
            "last_transition_at": failed_at.isoformat(),
        },
    )
    session.add(job)


def _cleanup_uploaded_objects(
    storage_client: ObjectStorageClient,
    uploaded_objects: list[tuple[str, str]],
) -> None:
    for bucket, key in reversed(uploaded_objects):
        try:
            storage_client.delete_object(bucket=bucket, key=key)
        except Exception:
            continue


def process_media_processing_job(
    *,
    session: Session,
    job_id: uuid.UUID,
    storage_client: ObjectStorageClient,
    processor: MediaProcessor,
) -> IngestJob:
    job = session.get(IngestJob, job_id)
    if job is None:
        raise ValueError(f"Ingest job {job_id} not found")
    if job.status != "source_uploaded":
        raise ValueError(
            f"Ingest job {job_id} is not ready for media processing: {job.status}"
        )

    assets = _source_assets_for_job(session, job_id=job.id)
    if not assets:
        raise ValueError(f"Ingest job {job_id} has no uploaded source assets")

    started_at = _now_utc()
    _start_media_processing(session, job=job, started_at=started_at)
    record_audit_event(
        session=session,
        actor=job.requested_by,
        action="ingest_job.media_processing_started",
        resource_type="ingest_job",
        resource_id=str(job.id),
        message="Started media processing for uploaded source assets",
        payload={
            "asset_ids": [str(asset.id) for asset in assets],
            "asset_count": len(assets),
        },
    )
    session.commit()
    session.refresh(job)

    workspace_dir = _processing_workspace_dir(job.id)
    uploaded_objects: list[tuple[str, str]] = []
    try:
        source_dir = workspace_dir / "source"
        output_dir = workspace_dir / "output"
        source_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        prepared_assets: list[PreparedSourceAsset] = []
        for asset in assets:
            if not asset.s3_bucket or not asset.s3_key:
                raise ValueError(
                    f"Media asset {asset.id} is missing object storage location data"
                )

            local_path = source_dir / f"{asset.id}-{asset.filename or 'source.bin'}"
            download_result = storage_client.download_file(
                bucket=asset.s3_bucket,
                key=asset.s3_key,
                local_path=local_path,
            )
            probe = processor.probe(input_path=local_path)
            asset.size_bytes = download_result.size_bytes
            asset.content_type = download_result.content_type or asset.content_type
            _apply_probe_metadata(asset=asset, probe=probe, probed_at=_now_utc())
            session.add(asset)
            prepared_assets.append(
                PreparedSourceAsset(asset=asset, local_path=local_path, probe=probe)
            )

        bundles = _build_derivative_bundles(prepared_assets)
        if not bundles:
            raise MediaResultError(
                f"Ingest job {job.id} has no processable source video assets"
            )

        derivative_assets: list[MediaAsset] = []
        for bundle in bundles:
            derivative_assets.extend(
                _generate_bundle_derivatives(
                    session=session,
                    job=job,
                    bundle=bundle,
                    output_dir=output_dir,
                    processor=processor,
                    storage_client=storage_client,
                    uploaded_objects=uploaded_objects,
                )
            )

        completed_at = _now_utc()
        _complete_media_processing(
            session,
            job=job,
            source_asset_ids=[asset.asset.id for asset in prepared_assets],
            derivative_asset_ids=[asset.id for asset in derivative_assets],
            completed_at=completed_at,
        )
        record_audit_event(
            session=session,
            actor=job.requested_by,
            action="ingest_job.media_processing_completed",
            resource_type="ingest_job",
            resource_id=str(job.id),
            message="Completed media processing for uploaded source assets",
            payload={
                "source_asset_ids": [str(asset.asset.id) for asset in prepared_assets],
                "derivative_asset_ids": [
                    str(asset.id) for asset in derivative_assets
                ],
            },
        )
        session.commit()
        _cleanup_workspace(workspace_dir)
    except Exception as exc:
        session.rollback()
        _cleanup_uploaded_objects(storage_client, uploaded_objects)
        _cleanup_workspace(workspace_dir)
        failed_job = session.get(IngestJob, job_id)
        if failed_job is None:
            raise
        failed_at = _now_utc()
        _fail_media_processing(
            session,
            job=failed_job,
            error_code=(
                exc.error_code
                if isinstance(exc, (MediaProcessingError, ObjectStorageError))
                else "media_processing_failed"
            ),
            message=str(exc),
            failed_at=failed_at,
        )
        record_audit_event(
            session=session,
            actor=failed_job.requested_by,
            action="ingest_job.media_processing_failed",
            resource_type="ingest_job",
            resource_id=str(failed_job.id),
            message="Media processing for uploaded source assets failed",
            payload={"error": str(exc)},
        )
        session.commit()
        job = failed_job

    session.refresh(job)
    return job

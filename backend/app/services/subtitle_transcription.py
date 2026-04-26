from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, delete, select

from app.core.config import settings
from app.ingest_models import IngestJob, MediaAsset, Video, VideoSubtitle
from app.services.audit import record_audit_event
from app.services.storage_keys import build_asset_storage_key
from app.transcription.base import (
    SubtitleAudioPreparer,
    SubtitleTranscriber,
    SubtitleTranscriptionError,
    SubtitleTranscriptionResultError,
    SubtitleTranscriptionSegment,
)
from app.uploader.base import ObjectStorageClient, ObjectStorageError

_CLAIM_CANDIDATE_LIMIT = 200
_SOURCE_ASSET_TYPES = (
    "source_archive",
    "source_video_stream",
    "source_audio_stream",
)
_SUBTITLE_ASSET_VARIANT = "openai-stt"
_SUBTITLE_SOURCE = "openai_stt"
_PROMPT_CONTEXT_CHAR_LIMIT = 500


@dataclass(slots=True)
class SubtitleTaskCandidate:
    source_asset: MediaAsset
    source_asset_ids: list[uuid.UUID]


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _merge_asset_metadata(asset: MediaAsset, *, payload: dict[str, object]) -> None:
    metadata_json = dict(asset.metadata_json)
    metadata_json.update(payload)
    asset.metadata_json = metadata_json


def _safe_stem(filename: str | None) -> str:
    if filename:
        candidate = Path(filename).stem.strip()
        if candidate:
            return candidate
    return "subtitle"


def _subtitle_workspace_dir(asset_id: uuid.UUID) -> Path:
    return Path(settings.INGEST_TMP_DIR) / "subtitle-tasks" / str(asset_id)


def _normalize_subtitle_text(value: str) -> str:
    normalized_lines = [part.strip() for part in value.splitlines() if part.strip()]
    if not normalized_lines:
        return value.strip()
    return " ".join(normalized_lines)


def _format_srt_timestamp(value: float) -> str:
    clamped = max(0.0, value)
    total_milliseconds = int(round(clamped * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def _segments_to_srt(segments: list[SubtitleTranscriptionSegment]) -> str:
    parts: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = _normalize_subtitle_text(segment.text)
        if not text:
            continue
        parts.append(
            "\n".join(
                [
                    str(index),
                    (
                        f"{_format_srt_timestamp(segment.start_seconds)} --> "
                        f"{_format_srt_timestamp(segment.end_seconds)}"
                    ),
                    text,
                ]
            )
        )
    return "\n\n".join(parts)


def _source_asset_ids_for_task(asset: MediaAsset) -> list[str]:
    raw_value = asset.metadata_json.get("transcription_source_asset_ids")
    if not isinstance(raw_value, list):
        return []
    return [str(value) for value in raw_value if isinstance(value, str) and value.strip()]


def _last_transition_at(asset: MediaAsset) -> datetime:
    raw_value = asset.metadata_json.get("last_transition_at")
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
    return asset.created_at.astimezone(timezone.utc)


def _is_stale_asset(
    asset: MediaAsset,
    *,
    stale_after_seconds: float,
    reference_time: datetime | None = None,
) -> bool:
    effective_reference_time = reference_time or _now_utc()
    if stale_after_seconds <= 0:
        return True
    return _last_transition_at(asset) <= effective_reference_time - timedelta(
        seconds=stale_after_seconds
    )


def _mark_asset_reclaimed(
    asset: MediaAsset,
    *,
    stale_after_seconds: float,
    reclaimed_at: datetime,
) -> None:
    metadata_json = dict(asset.metadata_json)
    reclaim = metadata_json.get("reclaim")
    if not isinstance(reclaim, dict):
        reclaim = {}
    previous_count = reclaim.get("count")
    reclaim["count"] = int(previous_count) + 1 if isinstance(previous_count, int) else 1
    reclaim["previous_status"] = asset.status
    reclaim["reclaimed_at"] = reclaimed_at.isoformat()
    reclaim["stale_after_seconds"] = stale_after_seconds
    reclaim["stale_since"] = _last_transition_at(asset).isoformat()
    metadata_json["reclaim"] = reclaim
    metadata_json["last_transition_at"] = reclaimed_at.isoformat()
    asset.status = "pending"
    asset.metadata_json = metadata_json


def _group_source_assets_for_subtitles(
    source_assets: list[MediaAsset],
) -> list[SubtitleTaskCandidate]:
    grouped_assets: dict[tuple[str, int | None], list[MediaAsset]] = {}
    for asset in source_assets:
        if asset.asset_type not in _SOURCE_ASSET_TYPES:
            continue
        if asset.status not in {"uploaded", "ready"}:
            continue
        grouped_assets.setdefault((asset.bvid, asset.cid), []).append(asset)

    # Preserve the source asset scan order so global backfills can prefer the
    # newest uploaded source per (bvid, cid) without extra sorting.
    candidates: list[SubtitleTaskCandidate] = []
    for assets in grouped_assets.values():
        source_archive = next(
            (asset for asset in assets if asset.asset_type == "source_archive"),
            None,
        )
        if source_archive is not None:
            candidates.append(
                SubtitleTaskCandidate(
                    source_asset=source_archive,
                    source_asset_ids=[asset.id for asset in assets],
                )
            )
            continue

        audio_asset = next(
            (asset for asset in assets if asset.asset_type == "source_audio_stream"),
            None,
        )
        if audio_asset is not None:
            candidates.append(
                SubtitleTaskCandidate(
                    source_asset=audio_asset,
                    source_asset_ids=[asset.id for asset in assets],
                )
            )
            continue

        video_asset = next(
            (asset for asset in assets if asset.asset_type == "source_video_stream"),
            None,
        )
        if video_asset is not None:
            candidates.append(
                SubtitleTaskCandidate(
                    source_asset=video_asset,
                    source_asset_ids=[asset.id for asset in assets],
                )
            )

    return candidates


def _find_existing_subtitle_asset(
    session: Session,
    *,
    bvid: str,
    cid: int | None,
) -> MediaAsset | None:
    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.bvid == bvid,
            MediaAsset.cid == cid,
            MediaAsset.asset_type == "subtitle",
            MediaAsset.variant == _SUBTITLE_ASSET_VARIANT,
            MediaAsset.status.in_(("pending", "processing", "ready")),
        )
        .order_by(MediaAsset.ready_at.desc(), MediaAsset.created_at.desc())
        .limit(1)
    )
    return session.exec(statement).first()


def _build_subtitle_asset(
    *,
    job_id: uuid.UUID | None,
    candidate: SubtitleTaskCandidate,
    created_at: datetime,
    replaces_subtitle_asset_id: uuid.UUID | None = None,
) -> MediaAsset:
    source_asset = candidate.source_asset
    filename = f"{_safe_stem(source_asset.filename)}.openai-stt.json"
    metadata_json: dict[str, object] = {
        "generator": _SUBTITLE_SOURCE,
        "queued_at": created_at.isoformat(),
        "last_transition_at": created_at.isoformat(),
        "transcription_model": settings.OPENAI_TRANSCRIPTION_MODEL,
        "transcription_language": settings.OPENAI_TRANSCRIPTION_LANGUAGE,
        "transcription_temperature": settings.OPENAI_TRANSCRIPTION_TEMPERATURE,
        "audio_format": settings.SUBTITLE_TRANSCRIPTION_AUDIO_FORMAT,
        "audio_bitrate": settings.SUBTITLE_TRANSCRIPTION_AUDIO_BITRATE,
        "audio_sample_rate": settings.SUBTITLE_TRANSCRIPTION_AUDIO_SAMPLE_RATE,
        "audio_split_strategy": "whole_file_then_equal_split",
        "transcription_source_asset_id": str(source_asset.id),
        "transcription_source_asset_type": source_asset.asset_type,
        "transcription_source_asset_ids": [
            str(asset_id) for asset_id in candidate.source_asset_ids
        ],
    }
    if replaces_subtitle_asset_id is not None:
        metadata_json["replaces_subtitle_asset_id"] = str(replaces_subtitle_asset_id)
    asset = MediaAsset(
        bvid=source_asset.bvid,
        cid=source_asset.cid,
        job_id=job_id,
        asset_type="subtitle",
        variant=_SUBTITLE_ASSET_VARIANT,
        status="pending",
        s3_bucket=source_asset.s3_bucket,
        s3_region=source_asset.s3_region,
        original_url_hash=source_asset.original_url_hash,
        filename=filename,
        content_type="application/json",
        metadata_json=metadata_json,
    )
    asset.s3_key = build_asset_storage_key(
        asset_type=asset.asset_type,
        bvid=asset.bvid,
        cid=asset.cid,
        asset_id=asset.id,
        filename=asset.filename,
    )
    return asset


def enqueue_subtitle_transcription_tasks(
    session: Session,
    *,
    job: IngestJob,
    source_assets: list[MediaAsset],
    replace_existing_ready: bool = False,
) -> list[MediaAsset]:
    if not bool(job.options.get("transcribe_subtitles")):
        return []

    created_at = _now_utc()
    queued_assets: list[MediaAsset] = []
    for candidate in _group_source_assets_for_subtitles(source_assets):
        existing_asset = _find_existing_subtitle_asset(
            session,
            bvid=candidate.source_asset.bvid,
            cid=candidate.source_asset.cid,
        )
        if existing_asset is not None:
            if existing_asset.status in {"pending", "processing"}:
                continue
            if existing_asset.status == "ready" and not replace_existing_ready:
                continue
        asset = _build_subtitle_asset(
            job_id=job.id,
            candidate=candidate,
            created_at=created_at,
            replaces_subtitle_asset_id=(
                existing_asset.id
                if existing_asset is not None and existing_asset.status == "ready"
                else None
            ),
        )
        session.add(asset)
        queued_assets.append(asset)
    return queued_assets


def _subtitle_backfill_source_assets(
    session: Session,
    *,
    bvid: str | None = None,
    cid: int | None = None,
) -> list[MediaAsset]:
    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.asset_type.in_(_SOURCE_ASSET_TYPES),
            MediaAsset.status.in_(("uploaded", "ready")),
            MediaAsset.deleted_at.is_(None),
        )
        .order_by(MediaAsset.created_at.desc(), MediaAsset.id.desc())
    )
    if bvid is not None:
        statement = statement.where(MediaAsset.bvid == bvid)
    if cid is not None:
        statement = statement.where(MediaAsset.cid == cid)
    return list(session.exec(statement).all())


def _job_actor(
    session: Session,
    *,
    job_id: uuid.UUID | None,
) -> str | None:
    if job_id is None:
        return None
    job = session.get(IngestJob, job_id)
    if job is None:
        return None
    return job.requested_by


def backfill_subtitle_transcription_tasks(
    session: Session,
    *,
    bvid: str | None = None,
    cid: int | None = None,
    limit: int | None = None,
    replace_existing_ready: bool = False,
) -> list[MediaAsset]:
    if limit is not None and limit <= 0:
        return []

    source_assets = _subtitle_backfill_source_assets(
        session,
        bvid=bvid,
        cid=cid,
    )
    created_at = _now_utc()
    queued_assets: list[MediaAsset] = []
    for candidate in _group_source_assets_for_subtitles(source_assets):
        if limit is not None and len(queued_assets) >= limit:
            break

        existing_asset = _find_existing_subtitle_asset(
            session,
            bvid=candidate.source_asset.bvid,
            cid=candidate.source_asset.cid,
        )
        if existing_asset is not None:
            if existing_asset.status in {"pending", "processing"}:
                continue
            if existing_asset.status == "ready" and not replace_existing_ready:
                continue

        asset = _build_subtitle_asset(
            job_id=candidate.source_asset.job_id,
            candidate=candidate,
            created_at=created_at,
            replaces_subtitle_asset_id=(
                existing_asset.id
                if existing_asset is not None and existing_asset.status == "ready"
                else None
            ),
        )
        session.add(asset)
        queued_assets.append(asset)
        record_audit_event(
            session=session,
            actor=_job_actor(session, job_id=asset.job_id),
            action="subtitle_transcription.backfill_enqueued",
            resource_type="media_asset",
            resource_id=str(asset.id),
            message="Enqueued subtitle transcription task for existing source media",
            payload={
                "subtitle_asset_id": str(asset.id),
                "source_asset_id": str(candidate.source_asset.id),
                "source_asset_ids": [str(asset_id) for asset_id in candidate.source_asset_ids],
                **({"job_id": str(asset.job_id)} if asset.job_id is not None else {}),
                **(
                    {"replaces_subtitle_asset_id": str(existing_asset.id)}
                    if existing_asset is not None and existing_asset.status == "ready"
                    else {}
                ),
            },
        )
    return queued_assets


def claim_next_subtitle_transcription_task(
    session: Session,
    *,
    stale_after_seconds: float | None = None,
) -> MediaAsset | None:
    effective_stale_after_seconds = (
        stale_after_seconds or settings.SUBTITLE_WORKER_STALE_AFTER_SECONDS
    )
    reference_time = _now_utc()
    statement = (
        select(MediaAsset)
        .where(
            MediaAsset.asset_type == "subtitle",
            MediaAsset.variant == _SUBTITLE_ASSET_VARIANT,
            MediaAsset.status.in_(("pending", "processing")),
        )
        .order_by(MediaAsset.created_at.asc(), MediaAsset.id.asc())
        .limit(_CLAIM_CANDIDATE_LIMIT)
        .with_for_update(skip_locked=True)
    )
    candidates = list(session.exec(statement).all())
    for asset in candidates:
        if asset.status == "pending":
            return asset
        if asset.status == "processing" and _is_stale_asset(
            asset,
            stale_after_seconds=effective_stale_after_seconds,
            reference_time=reference_time,
        ):
            _mark_asset_reclaimed(
                asset,
                stale_after_seconds=effective_stale_after_seconds,
                reclaimed_at=reference_time,
            )
            session.add(asset)
            return asset
    return None


def _load_source_asset(session: Session, *, subtitle_asset: MediaAsset) -> MediaAsset:
    raw_source_asset_id = subtitle_asset.metadata_json.get("transcription_source_asset_id")
    if not isinstance(raw_source_asset_id, str) or not raw_source_asset_id.strip():
        raise ValueError(
            f"Subtitle asset {subtitle_asset.id} is missing transcription source metadata"
        )
    source_asset_id = uuid.UUID(raw_source_asset_id)
    source_asset = session.get(MediaAsset, source_asset_id)
    if source_asset is None:
        raise ValueError(
            f"Subtitle asset {subtitle_asset.id} references missing source asset {source_asset_id}"
        )
    return source_asset


def _start_subtitle_transcription(
    session: Session,
    *,
    asset: MediaAsset,
    started_at: datetime,
) -> None:
    asset.status = "processing"
    asset.ready_at = None
    _merge_asset_metadata(
        asset,
        payload={
            "started_at": started_at.isoformat(),
            "last_transition_at": started_at.isoformat(),
            "error_code": None,
            "error_message": None,
        },
    )
    session.add(asset)


def _complete_subtitle_transcription(
    session: Session,
    *,
    asset: MediaAsset,
    completed_at: datetime,
    storage_result: object,
    model: str,
    language: str | None,
    segment_count: int,
    chunk_count: int,
) -> None:
    from app.uploader.base import ObjectStorageResult

    if not isinstance(storage_result, ObjectStorageResult):
        raise TypeError("storage_result must be an ObjectStorageResult")

    asset.status = "ready"
    asset.size_bytes = storage_result.size_bytes
    asset.etag = storage_result.etag
    asset.content_type = storage_result.content_type or asset.content_type
    asset.ready_at = completed_at
    _merge_asset_metadata(
        asset,
        payload={
            "completed_at": completed_at.isoformat(),
            "last_transition_at": completed_at.isoformat(),
            "uploaded_at": completed_at.isoformat(),
            "uploaded_bucket": storage_result.bucket,
            "uploaded_key": storage_result.key,
            "verified_size_bytes": storage_result.size_bytes,
            "verified_etag": storage_result.etag,
            "transcription_model": model,
            "subtitle_language": language,
            "subtitle_segment_count": segment_count,
            "subtitle_chunk_count": chunk_count,
            "transcription_temperature": settings.OPENAI_TRANSCRIPTION_TEMPERATURE,
            "audio_format": settings.SUBTITLE_TRANSCRIPTION_AUDIO_FORMAT,
            "audio_bitrate": settings.SUBTITLE_TRANSCRIPTION_AUDIO_BITRATE,
            "audio_sample_rate": settings.SUBTITLE_TRANSCRIPTION_AUDIO_SAMPLE_RATE,
            "audio_split_strategy": "whole_file_then_equal_split",
            "error_code": None,
            "error_message": None,
        },
    )
    session.add(asset)


def _fail_subtitle_transcription(
    session: Session,
    *,
    asset: MediaAsset,
    failed_at: datetime,
    error_code: str,
    message: str,
) -> None:
    asset.status = "failed"
    asset.ready_at = None
    _merge_asset_metadata(
        asset,
        payload={
            "failed_at": failed_at.isoformat(),
            "last_transition_at": failed_at.isoformat(),
            "error_code": error_code,
            "error_message": message,
        },
    )
    session.add(asset)


def _build_chunk_prompt(
    *,
    video: Video | None,
    previous_text: str,
) -> str | None:
    prompt_parts: list[str] = []
    if video is not None and video.title.strip():
        prompt_parts.append(video.title.strip())
    if previous_text.strip():
        prompt_parts.append(previous_text.strip()[-_PROMPT_CONTEXT_CHAR_LIMIT:])
    if not prompt_parts:
        return None
    return "\n".join(prompt_parts)


def _shift_segments(
    segments: list[SubtitleTranscriptionSegment],
    *,
    start_offset_seconds: float,
) -> list[SubtitleTranscriptionSegment]:
    return [
        SubtitleTranscriptionSegment(
            start_seconds=max(0.0, start_offset_seconds + segment.start_seconds),
            end_seconds=max(
                0.0,
                start_offset_seconds + max(segment.end_seconds, segment.start_seconds),
            ),
            text=segment.text,
            speaker=segment.speaker,
        )
        for segment in segments
    ]


def _transcription_summary_for_db(
    *,
    model: str,
    language: str | None,
    text: str,
    segments: list[SubtitleTranscriptionSegment],
    chunk_count: int,
) -> dict[str, object]:
    return {
        "model": model,
        "language": language,
        "text": text,
        "chunk_count": chunk_count,
        "segments": [
            {
                "start": segment.start_seconds,
                "end": segment.end_seconds,
                "text": segment.text,
                **({"speaker": segment.speaker} if segment.speaker else {}),
            }
            for segment in segments
        ],
    }


def _replace_openai_subtitle_track(
    session: Session,
    *,
    asset: MediaAsset,
    language: str | None,
    content: str,
    raw: dict[str, object],
    completed_at: datetime,
) -> None:
    session.exec(
        delete(VideoSubtitle).where(
            VideoSubtitle.bvid == asset.bvid,
            VideoSubtitle.cid == asset.cid,
            VideoSubtitle.source == _SUBTITLE_SOURCE,
        )
    )
    session.add(
        VideoSubtitle(
            bvid=asset.bvid,
            cid=asset.cid,
            lang=language,
            source=_SUBTITLE_SOURCE,
            content=content,
            raw=raw,
            asset_id=asset.id,
            crawled_at=completed_at,
        )
    )


def _asset_actor(session: Session, *, asset: MediaAsset) -> str | None:
    return _job_actor(session, job_id=asset.job_id)


def process_subtitle_transcription_task(
    *,
    session: Session,
    asset_id: uuid.UUID,
    storage_client: ObjectStorageClient,
    audio_preparer: SubtitleAudioPreparer,
    transcriber: SubtitleTranscriber,
) -> MediaAsset:
    asset = session.get(MediaAsset, asset_id)
    if asset is None:
        raise ValueError(f"Subtitle asset {asset_id} not found")
    if asset.asset_type != "subtitle" or asset.variant != _SUBTITLE_ASSET_VARIANT:
        raise ValueError(f"Media asset {asset_id} is not an OpenAI subtitle task")
    if asset.status != "pending":
        raise ValueError(f"Subtitle asset {asset_id} is not ready for processing: {asset.status}")

    source_asset = _load_source_asset(session, subtitle_asset=asset)
    if not source_asset.s3_bucket or not source_asset.s3_key:
        raise ValueError(
            f"Subtitle source asset {source_asset.id} is missing object storage metadata"
        )

    started_at = _now_utc()
    actor = _asset_actor(session, asset=asset)
    _start_subtitle_transcription(session, asset=asset, started_at=started_at)
    record_audit_event(
        session=session,
        actor=actor,
        action="subtitle_transcription.started",
        resource_type="media_asset",
        resource_id=str(asset.id),
        message="Started subtitle transcription task",
        payload={
            "subtitle_asset_id": str(asset.id),
            "source_asset_id": str(source_asset.id),
            "model": transcriber.model,
        },
    )
    session.commit()
    session.refresh(asset)

    workspace_dir = _subtitle_workspace_dir(asset.id)
    try:
        source_dir = workspace_dir / "source"
        audio_dir = workspace_dir / "audio"
        output_dir = workspace_dir / "output"
        source_dir.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

        source_input_path = source_dir / (source_asset.filename or f"{source_asset.id}.bin")
        storage_client.download_file(
            bucket=source_asset.s3_bucket,
            key=source_asset.s3_key,
            local_path=source_input_path,
        )

        audio_chunks = audio_preparer.prepare_chunks(
            input_path=source_input_path,
            output_dir=audio_dir,
        )
        video = session.get(Video, asset.bvid)

        accumulated_segments: list[SubtitleTranscriptionSegment] = []
        chunk_payloads: list[dict[str, object]] = []
        previous_text = ""
        detected_language: str | None = None

        for chunk in audio_chunks:
            prompt = (
                _build_chunk_prompt(video=video, previous_text=previous_text)
                if transcriber.supports_prompt
                else None
            )
            result = transcriber.transcribe(
                input_path=chunk.path,
                language=settings.OPENAI_TRANSCRIPTION_LANGUAGE,
                prompt=prompt,
            )
            shifted_segments = _shift_segments(
                result.segments,
                start_offset_seconds=chunk.start_seconds,
            )
            if not shifted_segments:
                raise SubtitleTranscriptionResultError(
                    f"Subtitle transcription model '{transcriber.model}' did not return timed segments"
                )
            accumulated_segments.extend(shifted_segments)
            previous_text = result.text.strip()[-_PROMPT_CONTEXT_CHAR_LIMIT:]
            detected_language = detected_language or result.language
            chunk_payloads.append(
                {
                    "chunk_file": chunk.path.name,
                    "start_seconds": chunk.start_seconds,
                    "duration_seconds": chunk.duration_seconds,
                    "text": result.text,
                    "language": result.language,
                    "usage": result.usage,
                    "raw": result.raw,
                }
            )

        srt_content = _segments_to_srt(accumulated_segments)
        combined_text = "\n".join(
            _normalize_subtitle_text(segment.text)
            for segment in accumulated_segments
            if _normalize_subtitle_text(segment.text)
        )

        payload = {
            "model": transcriber.model,
            "language": detected_language,
            "temperature": settings.OPENAI_TRANSCRIPTION_TEMPERATURE,
            "text": combined_text,
            "srt": srt_content,
            "audio": {
                "format": settings.SUBTITLE_TRANSCRIPTION_AUDIO_FORMAT,
                "bitrate": settings.SUBTITLE_TRANSCRIPTION_AUDIO_BITRATE,
                "sample_rate": settings.SUBTITLE_TRANSCRIPTION_AUDIO_SAMPLE_RATE,
                "split_strategy": "whole_file_then_equal_split",
            },
            "segments": [
                {
                    "start": segment.start_seconds,
                    "end": segment.end_seconds,
                    "text": segment.text,
                    **({"speaker": segment.speaker} if segment.speaker else {}),
                }
                for segment in accumulated_segments
            ],
            "chunks": chunk_payloads,
            "source_asset_id": str(source_asset.id),
            "source_asset_ids": _source_asset_ids_for_task(asset),
        }
        subtitle_json_path = output_dir / (asset.filename or "subtitle.json")
        subtitle_json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if not asset.s3_bucket or not asset.s3_key:
            raise ValueError(f"Subtitle asset {asset.id} is missing object storage metadata")

        storage_result = storage_client.multipart_upload_file(
            bucket=asset.s3_bucket,
            key=asset.s3_key,
            local_path=subtitle_json_path,
            content_type=asset.content_type,
            metadata={
                "bvid": asset.bvid,
                "asset_type": asset.asset_type,
                "asset_id": str(asset.id),
                "generator": _SUBTITLE_SOURCE,
            },
        )

        completed_at = _now_utc()
        _replace_openai_subtitle_track(
            session,
            asset=asset,
            language=detected_language,
            content=srt_content,
            raw=_transcription_summary_for_db(
                model=transcriber.model,
                language=detected_language,
                text=combined_text,
                segments=accumulated_segments,
                chunk_count=len(audio_chunks),
            ),
            completed_at=completed_at,
        )
        _complete_subtitle_transcription(
            session,
            asset=asset,
            completed_at=completed_at,
            storage_result=storage_result,
            model=transcriber.model,
            language=detected_language,
            segment_count=len(accumulated_segments),
            chunk_count=len(audio_chunks),
        )
        record_audit_event(
            session=session,
            actor=actor,
            action="subtitle_transcription.completed",
            resource_type="media_asset",
            resource_id=str(asset.id),
            message="Completed subtitle transcription task",
            payload={
                "subtitle_asset_id": str(asset.id),
                "source_asset_id": str(source_asset.id),
                "language": detected_language,
                "segment_count": len(accumulated_segments),
                "chunk_count": len(audio_chunks),
            },
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        failed_asset = session.get(MediaAsset, asset_id)
        if failed_asset is None:
            raise
        failed_at = _now_utc()
        _fail_subtitle_transcription(
            session,
            asset=failed_asset,
            failed_at=failed_at,
            error_code=(
                exc.error_code
                if isinstance(exc, (SubtitleTranscriptionError, ObjectStorageError))
                else "subtitle_transcription_failed"
            ),
            message=str(exc),
        )
        record_audit_event(
            session=session,
            actor=actor,
            action="subtitle_transcription.failed",
            resource_type="media_asset",
            resource_id=str(failed_asset.id),
            message="Subtitle transcription task failed",
            payload={
                "subtitle_asset_id": str(failed_asset.id),
                "source_asset_id": str(source_asset.id),
                "error": str(exc),
            },
        )
        session.commit()
        asset = failed_asset
    finally:
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir, ignore_errors=True)

    session.refresh(asset)
    return asset


def process_next_subtitle_transcription_task(
    *,
    session: Session,
    storage_client: ObjectStorageClient,
    audio_preparer: SubtitleAudioPreparer,
    transcriber: SubtitleTranscriber,
    stale_after_seconds: float | None = None,
) -> MediaAsset | None:
    asset = claim_next_subtitle_transcription_task(
        session,
        stale_after_seconds=stale_after_seconds,
    )
    if asset is None:
        return None

    return process_subtitle_transcription_task(
        session=session,
        asset_id=asset.id,
        storage_client=storage_client,
        audio_preparer=audio_preparer,
        transcriber=transcriber,
    )

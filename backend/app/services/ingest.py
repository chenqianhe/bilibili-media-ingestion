import hashlib
import json

from fastapi import HTTPException
from sqlmodel import Session, select

from app.ingest_models import (
    IngestJob,
    IngestVideoRequest,
    Video,
)
from app.models import User
from app.services.audit import record_audit_event
from app.services.bilibili import extract_bvid


def build_idempotency_key(
    *, bvid: str, requested_by: str | None, options: dict[str, object]
) -> str:
    payload = {
        "bvid": bvid,
        "requested_by": requested_by,
        "options": options,
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def submit_ingest_job(
    *,
    session: Session,
    current_user: User,
    payload: IngestVideoRequest,
) -> IngestJob:
    normalized_bvid = extract_bvid(payload.input)
    if normalized_bvid is None:
        raise HTTPException(status_code=400, detail="Could not extract a valid BVID")

    options = payload.options.model_dump(mode="json")
    idempotency_key: str | None = None
    if not payload.options.force_refresh:
        idempotency_key = build_idempotency_key(
            bvid=normalized_bvid,
            requested_by=current_user.email,
            options=options,
        )
        statement = select(IngestJob).where(IngestJob.idempotency_key == idempotency_key)
        existing_job = session.exec(statement).first()
        if existing_job is not None:
            return existing_job

    is_download_requested = payload.options.download_video

    job_status = "pending"
    phase = "queued for metadata ingestion"
    error_code: str | None = None
    error_message: str | None = None

    video = session.get(Video, normalized_bvid)
    if video is None:
        video = Video(
            bvid=normalized_bvid,
            title=normalized_bvid,
            raw={"source": "ingest_submission"},
        )
    else:
        raw = dict(video.raw)
        raw["last_ingest_input"] = payload.input
        video.raw = raw
    session.add(video)

    ingest_job = IngestJob(
        input_text=payload.input,
        normalized_bvid=normalized_bvid,
        requested_by=current_user.email,
        status=job_status,
        phase=phase,
        options=options,
        progress={},
        idempotency_key=idempotency_key,
        error_code=error_code,
        error_message=error_message,
    )
    session.add(ingest_job)
    record_audit_event(
        session=session,
        actor=current_user.email,
        action="ingest_job.created",
        resource_type="ingest_job",
        resource_id=str(ingest_job.id),
        message="Submitted video ingest job",
        payload={
            "bvid": normalized_bvid,
            "download_video": is_download_requested,
            "status": job_status,
        },
    )
    session.commit()
    session.refresh(ingest_job)
    return ingest_job

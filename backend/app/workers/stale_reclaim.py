from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from app.ingest_models import IngestJob

_CLAIM_CANDIDATE_LIMIT = 200


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def select_candidate_jobs(
    session: Session,
    *,
    statuses: Sequence[str],
    require_normalized_bvid: bool = False,
    normalized_bvid: str | None = None,
    limit: int = _CLAIM_CANDIDATE_LIMIT,
) -> list[IngestJob]:
    statement = select(IngestJob).where(
        IngestJob.status.in_(tuple(statuses)),
        IngestJob.finished_at.is_(None),
    )
    if require_normalized_bvid:
        statement = statement.where(IngestJob.normalized_bvid.is_not(None))
    if normalized_bvid is not None:
        statement = statement.where(IngestJob.normalized_bvid == normalized_bvid)

    statement = (
        statement.order_by(IngestJob.priority.asc(), IngestJob.created_at.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(session.exec(statement).all())


def last_transition_at(job: IngestJob) -> datetime:
    raw_value = job.progress.get("last_transition_at")
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            parsed = datetime.fromisoformat(raw_value)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)

    if job.started_at is not None:
        return job.started_at.astimezone(timezone.utc)
    return job.created_at.astimezone(timezone.utc)


def is_stale_job(
    job: IngestJob,
    *,
    stale_after_seconds: float,
    reference_time: datetime | None = None,
) -> bool:
    effective_reference_time = reference_time or now_utc()
    if stale_after_seconds <= 0:
        return True
    return last_transition_at(job) <= effective_reference_time - timedelta(
        seconds=stale_after_seconds
    )


def _coerce_reclaim_count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return 0


def mark_job_reclaimed(
    job: IngestJob,
    *,
    stage: str,
    stale_after_seconds: float,
    reclaimed_at: datetime,
    queued_status: str | None = None,
) -> None:
    progress = dict(job.progress)
    reclaim = progress.get("reclaim")
    if not isinstance(reclaim, dict):
        reclaim = {}

    previous_status = job.status
    reclaim["count"] = _coerce_reclaim_count(reclaim.get("count")) + 1
    reclaim["stage"] = stage
    reclaim["previous_status"] = previous_status
    reclaim["queued_status"] = queued_status or previous_status
    reclaim["reclaimed_at"] = reclaimed_at.isoformat()
    reclaim["stale_after_seconds"] = stale_after_seconds
    reclaim["stale_since"] = last_transition_at(job).isoformat()
    progress["reclaim"] = reclaim
    job.progress = progress
    if queued_status is not None:
        job.status = queued_status
    job.finished_at = None

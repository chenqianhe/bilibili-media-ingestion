import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import func, select

from app.api.deps import CurrentUser, SessionDep
from app.ingest_models import (
    IngestJob,
    IngestJobDetail,
    IngestJobPublic,
    IngestJobsPublic,
    IngestJobSummaryPublic,
    IngestVideoRequest,
    JobErrorPublic,
)
from app.services.ingest import submit_ingest_job

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _to_job_public(job: IngestJob) -> IngestJobPublic:
    return IngestJobPublic(
        job_id=job.id,
        bvid=job.normalized_bvid,
        status=job.status,
        phase=job.phase,
    )


def _to_job_error_public(job: IngestJob) -> JobErrorPublic | None:
    if not job.error_code and not job.error_message:
        return None
    return JobErrorPublic(
        code=job.error_code,
        message=job.error_message,
    )


def _to_job_summary_public(job: IngestJob) -> IngestJobSummaryPublic:
    return IngestJobSummaryPublic(
        job_id=job.id,
        bvid=job.normalized_bvid,
        status=job.status,
        phase=job.phase,
        requested_by=job.requested_by,
        error=_to_job_error_public(job),
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post("/videos", response_model=IngestJobPublic, status_code=status.HTTP_202_ACCEPTED)
def create_ingest_job(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    payload: IngestVideoRequest,
) -> Any:
    ingest_job = submit_ingest_job(
        session=session,
        current_user=current_user,
        payload=payload,
    )
    return _to_job_public(ingest_job)


@router.get("/jobs", response_model=IngestJobsPublic)
def read_ingest_jobs(
    *,
    session: SessionDep,
    current_user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status", min_length=1),
    bvid: str | None = Query(default=None, min_length=1, max_length=32),
    requested_by: str | None = Query(default=None, min_length=1, max_length=255),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    filters: list[object] = []
    if status_filter is not None:
        filters.append(IngestJob.status == status_filter)
    if bvid is not None:
        filters.append(IngestJob.normalized_bvid == bvid)
    if current_user.is_superuser:
        if requested_by is not None:
            filters.append(IngestJob.requested_by == requested_by)
    else:
        filters.append(IngestJob.requested_by == current_user.email)

    total_count = (
        session.exec(
            select(func.count())
            .select_from(IngestJob)
            .where(*filters)
        ).one()
        or 0
    )
    jobs = list(
        session.exec(
            select(IngestJob)
            .where(*filters)
            .order_by(IngestJob.created_at.desc(), IngestJob.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return IngestJobsPublic(
        data=[_to_job_summary_public(job) for job in jobs],
        count=int(total_count),
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", response_model=IngestJobDetail)
def read_ingest_job(
    *, session: SessionDep, current_user: CurrentUser, job_id: uuid.UUID
) -> Any:
    ingest_job = session.get(IngestJob, job_id)
    if ingest_job is None:
        raise HTTPException(status_code=404, detail="Ingest job not found")
    if not current_user.is_superuser and ingest_job.requested_by != current_user.email:
        raise HTTPException(status_code=403, detail="Not enough permissions")
    return IngestJobDetail(
        job_id=ingest_job.id,
        bvid=ingest_job.normalized_bvid,
        status=ingest_job.status,
        phase=ingest_job.phase,
        requested_by=ingest_job.requested_by,
        options=ingest_job.options,
        progress=ingest_job.progress,
        error=_to_job_error_public(ingest_job),
        created_at=ingest_job.created_at,
        started_at=ingest_job.started_at,
        finished_at=ingest_job.finished_at,
    )

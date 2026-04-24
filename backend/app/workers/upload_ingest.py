from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable

from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.ingest_models import IngestJob
from app.services.upload_ingest import process_upload_ingest_job
from app.uploader.base import ObjectStorageClient
from app.uploader.s3_multipart import S3MultipartObjectStorageClient
from app.workers.stale_reclaim import (
    is_stale_job,
    mark_job_reclaimed,
    now_utc,
    select_candidate_jobs,
)

logger = logging.getLogger(__name__)
_UPLOAD_CANDIDATE_STATUSES = ("source_downloaded", "uploading_source")


def claim_next_upload_ingest_job(
    session: Session,
    *,
    stale_after_seconds: float | None = None,
) -> IngestJob | None:
    effective_stale_after_seconds = (
        stale_after_seconds or settings.UPLOAD_WORKER_STALE_AFTER_SECONDS
    )
    reference_time = now_utc()
    candidates = select_candidate_jobs(
        session,
        statuses=_UPLOAD_CANDIDATE_STATUSES,
        require_normalized_bvid=True,
    )
    for job in candidates:
        if job.status == "source_downloaded":
            return job
        if job.status == "uploading_source" and is_stale_job(
            job,
            stale_after_seconds=effective_stale_after_seconds,
            reference_time=reference_time,
        ):
            mark_job_reclaimed(
                job,
                stage="upload",
                stale_after_seconds=effective_stale_after_seconds,
                reclaimed_at=reference_time,
                queued_status="source_downloaded",
            )
            return job
    return None


def process_next_upload_ingest_job(
    *,
    session: Session,
    storage_client: ObjectStorageClient,
    stale_after_seconds: float | None = None,
) -> IngestJob | None:
    job = claim_next_upload_ingest_job(
        session,
        stale_after_seconds=stale_after_seconds,
    )
    if job is None:
        return None

    return process_upload_ingest_job(
        session=session,
        job_id=job.id,
        storage_client=storage_client,
    )


class UploadIngestWorker:
    def __init__(
        self,
        *,
        storage_client: ObjectStorageClient,
        session_factory: Callable[[], Session] | None = None,
        poll_interval_seconds: float | None = None,
        stale_after_seconds: float | None = None,
    ) -> None:
        self._storage_client = storage_client
        self._session_factory = session_factory or (lambda: Session(engine))
        self._poll_interval_seconds = (
            poll_interval_seconds or settings.UPLOAD_WORKER_POLL_INTERVAL_SECONDS
        )
        self._stale_after_seconds = (
            stale_after_seconds or settings.UPLOAD_WORKER_STALE_AFTER_SECONDS
        )

    def run_once(self) -> IngestJob | None:
        with self._session_factory() as session:
            return process_next_upload_ingest_job(
                session=session,
                storage_client=self._storage_client,
                stale_after_seconds=self._stale_after_seconds,
            )

    def run_forever(self, *, max_jobs: int | None = None) -> int:
        processed_count = 0
        while max_jobs is None or processed_count < max_jobs:
            job = self.run_once()
            if job is None:
                time.sleep(self._poll_interval_seconds)
                continue

            processed_count += 1
            logger.info(
                "Processed upload job %s with final status %s",
                job.id,
                job.status,
            )

        return processed_count


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the source upload worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one source upload job, then exit",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Process up to N jobs before exiting",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=settings.UPLOAD_WORKER_POLL_INTERVAL_SECONDS,
        help="Seconds to sleep when no upload jobs are available",
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        default=settings.UPLOAD_WORKER_STALE_AFTER_SECONDS,
        help="Seconds after which an in-progress upload job can be reclaimed",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Worker log level",
    )
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))

    storage_client = S3MultipartObjectStorageClient()
    worker = UploadIngestWorker(
        storage_client=storage_client,
        poll_interval_seconds=args.poll_interval,
        stale_after_seconds=args.stale_after,
    )
    try:
        if args.once:
            job = worker.run_once()
            if job is None:
                logger.info("No pending source upload jobs found")
                return 0

            logger.info(
                "Processed upload job %s with final status %s",
                job.id,
                job.status,
            )
            return 0

        worker.run_forever(max_jobs=args.max_jobs)
        return 0
    except KeyboardInterrupt:
        logger.info("Upload worker interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

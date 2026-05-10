from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable

from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.ingest_models import IngestJob
from app.processor.base import MediaProcessor
from app.processor.ffmpeg import FFmpegMediaProcessor
from app.services.bilibili import extract_bvid
from app.services.media_processing import process_media_processing_job
from app.uploader.base import ObjectStorageClient
from app.uploader.s3_multipart import S3MultipartObjectStorageClient
from app.workers.resilience import sleep_after_database_error
from app.workers.stale_reclaim import (
    is_stale_job,
    mark_job_reclaimed,
    now_utc,
    select_candidate_jobs,
)

logger = logging.getLogger(__name__)
_PROCESSING_CANDIDATE_STATUSES = ("source_uploaded", "processing_media")


def claim_next_media_processing_job(
    session: Session,
    *,
    stale_after_seconds: float | None = None,
    bvid: str | None = None,
) -> IngestJob | None:
    effective_stale_after_seconds = (
        stale_after_seconds or settings.PROCESSING_WORKER_STALE_AFTER_SECONDS
    )
    reference_time = now_utc()
    candidates = select_candidate_jobs(
        session,
        statuses=_PROCESSING_CANDIDATE_STATUSES,
        require_normalized_bvid=True,
        normalized_bvid=bvid,
    )
    for job in candidates:
        if job.status == "source_uploaded":
            return job
        if job.status == "processing_media" and is_stale_job(
            job,
            stale_after_seconds=effective_stale_after_seconds,
            reference_time=reference_time,
        ):
            mark_job_reclaimed(
                job,
                stage="processing",
                stale_after_seconds=effective_stale_after_seconds,
                reclaimed_at=reference_time,
                queued_status="source_uploaded",
            )
            return job
    return None


def process_next_media_processing_job(
    *,
    session: Session,
    storage_client: ObjectStorageClient,
    processor: MediaProcessor,
    stale_after_seconds: float | None = None,
    bvid: str | None = None,
) -> IngestJob | None:
    job = claim_next_media_processing_job(
        session,
        stale_after_seconds=stale_after_seconds,
        bvid=bvid,
    )
    if job is None:
        return None

    return process_media_processing_job(
        session=session,
        job_id=job.id,
        storage_client=storage_client,
        processor=processor,
    )


class MediaProcessingWorker:
    def __init__(
        self,
        *,
        storage_client: ObjectStorageClient,
        processor: MediaProcessor,
        session_factory: Callable[[], Session] | None = None,
        poll_interval_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        bvid: str | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._storage_client = storage_client
        self._processor = processor
        self._session_factory = session_factory or (lambda: Session(engine))
        self._poll_interval_seconds = (
            poll_interval_seconds or settings.PROCESSING_WORKER_POLL_INTERVAL_SECONDS
        )
        self._stale_after_seconds = (
            stale_after_seconds or settings.PROCESSING_WORKER_STALE_AFTER_SECONDS
        )
        self._bvid = bvid
        self._sleep = sleep or time.sleep

    def run_once(self) -> IngestJob | None:
        with self._session_factory() as session:
            return process_next_media_processing_job(
                session=session,
                storage_client=self._storage_client,
                processor=self._processor,
                stale_after_seconds=self._stale_after_seconds,
                bvid=self._bvid,
            )

    def run_forever(self, *, max_jobs: int | None = None) -> int:
        processed_count = 0
        while max_jobs is None or processed_count < max_jobs:
            try:
                job = self.run_once()
            except OperationalError:
                sleep_after_database_error(
                    logger=logger,
                    worker_name="Media processing",
                    retry_seconds=self._poll_interval_seconds,
                    sleep=self._sleep,
                )
                continue
            if job is None:
                self._sleep(self._poll_interval_seconds)
                continue

            processed_count += 1
            logger.info(
                "Processed media job %s with final status %s",
                job.id,
                job.status,
            )

        return processed_count


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the media processing worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one media processing job, then exit",
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
        default=settings.PROCESSING_WORKER_POLL_INTERVAL_SECONDS,
        help="Seconds to sleep when no processing jobs are available",
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        default=settings.PROCESSING_WORKER_STALE_AFTER_SECONDS,
        help="Seconds after which an in-progress media job can be reclaimed",
    )
    parser.add_argument(
        "--bvid",
        default=None,
        help="Only claim media processing jobs for this BVID or Bilibili URL",
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
    bvid = extract_bvid(args.bvid) if args.bvid else None
    if args.bvid and bvid is None:
        parser.error("--bvid must be a valid BVID or Bilibili video URL")

    storage_client = S3MultipartObjectStorageClient()
    processor = FFmpegMediaProcessor()
    worker = MediaProcessingWorker(
        storage_client=storage_client,
        processor=processor,
        poll_interval_seconds=args.poll_interval,
        stale_after_seconds=args.stale_after,
        bvid=bvid,
    )
    try:
        if args.once:
            job = worker.run_once()
            if job is None:
                logger.info("No pending media processing jobs found")
                return 0

            logger.info(
                "Processed media job %s with final status %s",
                job.id,
                job.status,
            )
            return 0

        worker.run_forever(max_jobs=args.max_jobs)
        return 0
    except KeyboardInterrupt:
        logger.info("Media processing worker interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

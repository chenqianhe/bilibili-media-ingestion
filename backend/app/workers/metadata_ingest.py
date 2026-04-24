from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable

from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.crawler.bilibili_auxiliary import BilibiliAuxiliaryProvider
from app.crawler.bilibili_auxiliary_http import BilibiliHttpAuxiliaryProvider
from app.crawler.bilibili_metadata import BilibiliMetadataProvider
from app.crawler.bilibili_metadata_http import BilibiliHttpMetadataProvider
from app.crawler.bilibili_web import BilibiliWebClient
from app.ingest_models import IngestJob
from app.services.bilibili_access import build_bilibili_access_runtime
from app.services.metadata_ingest import process_metadata_ingest_job
from app.uploader.base import ObjectStorageClient, ObjectStorageConfigurationError
from app.uploader.s3_multipart import S3MultipartObjectStorageClient
from app.workers.stale_reclaim import (
    is_stale_job,
    mark_job_reclaimed,
    now_utc,
    select_candidate_jobs,
)

logger = logging.getLogger(__name__)

_WORKABLE_METADATA_STATUSES = ("pending",)
_METADATA_CANDIDATE_STATUSES = _WORKABLE_METADATA_STATUSES + ("metadata_fetching",)


def claim_next_metadata_ingest_job(
    session: Session,
    *,
    stale_after_seconds: float | None = None,
) -> IngestJob | None:
    effective_stale_after_seconds = (
        stale_after_seconds or settings.METADATA_WORKER_STALE_AFTER_SECONDS
    )
    reference_time = now_utc()
    candidates = select_candidate_jobs(
        session,
        statuses=_METADATA_CANDIDATE_STATUSES,
        require_normalized_bvid=True,
    )
    for job in candidates:
        if job.status in _WORKABLE_METADATA_STATUSES and job.started_at is None:
            return job
        if job.status == "metadata_fetching" and is_stale_job(
            job,
            stale_after_seconds=effective_stale_after_seconds,
            reference_time=reference_time,
        ):
            mark_job_reclaimed(
                job,
                stage="metadata",
                stale_after_seconds=effective_stale_after_seconds,
                reclaimed_at=reference_time,
            )
            return job
    return None


def process_next_metadata_ingest_job(
    *,
    session: Session,
    provider: BilibiliMetadataProvider,
    auxiliary_provider: BilibiliAuxiliaryProvider | None = None,
    comment_image_web_client: BilibiliWebClient | None = None,
    comment_image_storage_client: ObjectStorageClient | None = None,
    stale_after_seconds: float | None = None,
) -> IngestJob | None:
    job = claim_next_metadata_ingest_job(
        session,
        stale_after_seconds=stale_after_seconds,
    )
    if job is None:
        return None

    return process_metadata_ingest_job(
        session=session,
        job_id=job.id,
        provider=provider,
        auxiliary_provider=auxiliary_provider,
        comment_image_web_client=comment_image_web_client,
        comment_image_storage_client=comment_image_storage_client,
    )


class MetadataIngestWorker:
    def __init__(
        self,
        *,
        comment_image_storage_client: ObjectStorageClient | None = None,
        session_factory: Callable[[], Session] | None = None,
        poll_interval_seconds: float | None = None,
        inter_job_delay_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._comment_image_storage_client = comment_image_storage_client
        self._session_factory = session_factory or (lambda: Session(engine))
        self._poll_interval_seconds = (
            poll_interval_seconds or settings.METADATA_WORKER_POLL_INTERVAL_SECONDS
        )
        self._inter_job_delay_seconds = max(
            0.0,
            (
                inter_job_delay_seconds
                if inter_job_delay_seconds is not None
                else settings.METADATA_WORKER_INTER_JOB_DELAY_SECONDS
            ),
        )
        self._stale_after_seconds = (
            stale_after_seconds or settings.METADATA_WORKER_STALE_AFTER_SECONDS
        )
        self._sleep = sleep or time.sleep

    def run_once(self) -> IngestJob | None:
        with self._session_factory() as session:
            job = claim_next_metadata_ingest_job(
                session,
                stale_after_seconds=self._stale_after_seconds,
            )
            if job is None:
                return None

            runtime = build_bilibili_access_runtime(session)
            provider = BilibiliHttpMetadataProvider(cookie_header=runtime.cookie_header)
            auxiliary_provider = BilibiliHttpAuxiliaryProvider(
                cookie_header=runtime.cookie_header
            )
            comment_image_web_client = (
                BilibiliWebClient(cookie_header=runtime.cookie_header)
                if self._comment_image_storage_client is not None
                else None
            )
            try:
                return process_metadata_ingest_job(
                    session=session,
                    job_id=job.id,
                    provider=provider,
                    auxiliary_provider=auxiliary_provider,
                    comment_image_web_client=comment_image_web_client,
                    comment_image_storage_client=self._comment_image_storage_client,
                )
            finally:
                provider.close()
                auxiliary_provider.close()
                if comment_image_web_client is not None:
                    comment_image_web_client.close()

    def run_forever(self, *, max_jobs: int | None = None) -> int:
        processed_count = 0
        while max_jobs is None or processed_count < max_jobs:
            job = self.run_once()
            if job is None:
                self._sleep(self._poll_interval_seconds)
                continue

            processed_count += 1
            logger.info(
                "Processed metadata job %s with final status %s",
                job.id,
                job.status,
            )
            if (
                self._inter_job_delay_seconds > 0
                and (max_jobs is None or processed_count < max_jobs)
            ):
                logger.debug(
                    "Sleeping %.3fs before claiming the next metadata job",
                    self._inter_job_delay_seconds,
                )
                self._sleep(self._inter_job_delay_seconds)

        return processed_count


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the metadata ingestion worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one metadata ingest job, then exit",
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
        default=settings.METADATA_WORKER_POLL_INTERVAL_SECONDS,
        help="Seconds to sleep when no metadata jobs are available",
    )
    parser.add_argument(
        "--inter-job-delay",
        type=float,
        default=settings.METADATA_WORKER_INTER_JOB_DELAY_SECONDS,
        help="Seconds to sleep after finishing one metadata job before claiming the next",
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        default=settings.METADATA_WORKER_STALE_AFTER_SECONDS,
        help="Seconds after which an in-progress metadata job can be reclaimed",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Worker log level",
    )
    return parser


def _build_optional_comment_image_storage_client() -> ObjectStorageClient | None:
    if (
        not settings.S3_BUCKET
        or not settings.S3_ACCESS_KEY
        or not settings.S3_SECRET_KEY
    ):
        logger.info(
            "Image asset uploads are disabled because object storage is not configured"
        )
        return None

    try:
        return S3MultipartObjectStorageClient()
    except ObjectStorageConfigurationError as exc:
        logger.warning("Image asset uploads are disabled: %s", exc)
        return None


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))

    comment_image_storage_client = _build_optional_comment_image_storage_client()
    worker = MetadataIngestWorker(
        comment_image_storage_client=comment_image_storage_client,
        poll_interval_seconds=args.poll_interval,
        inter_job_delay_seconds=args.inter_job_delay,
        stale_after_seconds=args.stale_after,
    )
    try:
        if args.once:
            job = worker.run_once()
            if job is None:
                logger.info("No pending metadata jobs found")
                return 0

            logger.info(
                "Processed metadata job %s with final status %s",
                job.id,
                job.status,
            )
            return 0

        worker.run_forever(max_jobs=args.max_jobs)
        return 0
    except KeyboardInterrupt:
        logger.info("Metadata worker interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

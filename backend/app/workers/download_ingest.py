from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable

from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.downloader.base import DownloaderAdapter
from app.downloader.yt_dlp_adapter import YtDlpDownloaderAdapter
from app.ingest_models import IngestJob
from app.services.bilibili_access import build_bilibili_access_runtime
from app.services.download_ingest import process_download_ingest_job
from app.workers.resilience import sleep_after_database_error
from app.workers.stale_reclaim import (
    is_stale_job,
    mark_job_reclaimed,
    now_utc,
    select_candidate_jobs,
)

logger = logging.getLogger(__name__)
_DOWNLOAD_CANDIDATE_STATUSES = ("metadata_ready", "downloading")


def _download_requested(job: IngestJob) -> bool:
    return bool(job.options.get("download_video"))


def claim_next_download_ingest_job(
    session: Session,
    *,
    stale_after_seconds: float | None = None,
) -> IngestJob | None:
    effective_stale_after_seconds = (
        stale_after_seconds or settings.DOWNLOAD_WORKER_STALE_AFTER_SECONDS
    )
    reference_time = now_utc()
    candidates = select_candidate_jobs(
        session,
        statuses=_DOWNLOAD_CANDIDATE_STATUSES,
        require_normalized_bvid=True,
    )
    for job in candidates:
        if not _download_requested(job):
            continue
        if job.status == "metadata_ready":
            return job
        if job.status == "downloading" and is_stale_job(
            job,
            stale_after_seconds=effective_stale_after_seconds,
            reference_time=reference_time,
        ):
            mark_job_reclaimed(
                job,
                stage="download",
                stale_after_seconds=effective_stale_after_seconds,
                reclaimed_at=reference_time,
                queued_status="metadata_ready",
            )
            return job
    return None


def process_next_download_ingest_job(
    *,
    session: Session,
    adapter: DownloaderAdapter,
    stale_after_seconds: float | None = None,
) -> IngestJob | None:
    job = claim_next_download_ingest_job(
        session,
        stale_after_seconds=stale_after_seconds,
    )
    if job is None:
        return None

    return process_download_ingest_job(
        session=session,
        job_id=job.id,
        adapter=adapter,
    )


class DownloadIngestWorker:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] | None = None,
        poll_interval_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._session_factory = session_factory or (lambda: Session(engine))
        self._poll_interval_seconds = (
            poll_interval_seconds or settings.DOWNLOAD_WORKER_POLL_INTERVAL_SECONDS
        )
        self._stale_after_seconds = (
            stale_after_seconds or settings.DOWNLOAD_WORKER_STALE_AFTER_SECONDS
        )
        self._sleep = sleep or time.sleep

    def run_once(self) -> IngestJob | None:
        with self._session_factory() as session:
            job = claim_next_download_ingest_job(
                session,
                stale_after_seconds=self._stale_after_seconds,
            )
            if job is None:
                return None

            runtime = build_bilibili_access_runtime(session)
            adapter = YtDlpDownloaderAdapter(
                cookie_header=runtime.cookie_header,
                cookies_text=runtime.download_cookies_text,
                user_agent=runtime.download_user_agent,
            )
            return process_download_ingest_job(
                session=session,
                job_id=job.id,
                adapter=adapter,
            )

    def run_forever(self, *, max_jobs: int | None = None) -> int:
        processed_count = 0
        while max_jobs is None or processed_count < max_jobs:
            try:
                job = self.run_once()
            except OperationalError:
                sleep_after_database_error(
                    logger=logger,
                    worker_name="Download",
                    retry_seconds=self._poll_interval_seconds,
                    sleep=self._sleep,
                )
                continue
            if job is None:
                self._sleep(self._poll_interval_seconds)
                continue

            processed_count += 1
            logger.info(
                "Processed download job %s with final status %s",
                job.id,
                job.status,
            )

        return processed_count


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the source download worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one source download job, then exit",
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
        default=settings.DOWNLOAD_WORKER_POLL_INTERVAL_SECONDS,
        help="Seconds to sleep when no download jobs are available",
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        default=settings.DOWNLOAD_WORKER_STALE_AFTER_SECONDS,
        help="Seconds after which an in-progress download job can be reclaimed",
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

    worker = DownloadIngestWorker(
        poll_interval_seconds=args.poll_interval,
        stale_after_seconds=args.stale_after,
    )
    try:
        if args.once:
            job = worker.run_once()
            if job is None:
                logger.info("No pending source download jobs found")
                return 0

            logger.info(
                "Processed download job %s with final status %s",
                job.id,
                job.status,
            )
            return 0

        worker.run_forever(max_jobs=args.max_jobs)
        return 0
    except KeyboardInterrupt:
        logger.info("Download worker interrupted")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

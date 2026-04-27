from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Callable

from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.core.config import settings
from app.core.db import engine
from app.ingest_models import MediaAsset
from app.services.subtitle_transcription import process_next_subtitle_transcription_task
from app.transcription.base import SubtitleAudioPreparer, SubtitleTranscriber
from app.transcription.ffmpeg_audio import FFmpegSubtitleAudioPreparer
from app.transcription.openai_stt import OpenAISubtitleTranscriber
from app.uploader.base import ObjectStorageClient
from app.uploader.s3_multipart import S3MultipartObjectStorageClient
from app.workers.resilience import sleep_after_database_error

logger = logging.getLogger(__name__)


def _log_processed_subtitle_task(asset: MediaAsset) -> None:
    if asset.status != "failed":
        logger.info(
            "Processed subtitle task %s with final status %s",
            asset.id,
            asset.status,
        )
        return

    metadata_json = asset.metadata_json or {}
    logger.error(
        "Processed subtitle task %s with final status failed (error_code=%s, error_message=%s)",
        asset.id,
        metadata_json.get("error_code") or "unknown",
        metadata_json.get("error_message") or "not recorded",
    )


class SubtitleTranscriptionWorker:
    def __init__(
        self,
        *,
        storage_client: ObjectStorageClient,
        audio_preparer: SubtitleAudioPreparer,
        transcriber: SubtitleTranscriber,
        session_factory: Callable[[], Session] | None = None,
        poll_interval_seconds: float | None = None,
        stale_after_seconds: float | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._storage_client = storage_client
        self._audio_preparer = audio_preparer
        self._transcriber = transcriber
        self._session_factory = session_factory or (lambda: Session(engine))
        self._poll_interval_seconds = (
            poll_interval_seconds or settings.SUBTITLE_WORKER_POLL_INTERVAL_SECONDS
        )
        self._stale_after_seconds = (
            stale_after_seconds or settings.SUBTITLE_WORKER_STALE_AFTER_SECONDS
        )
        self._sleep = sleep or time.sleep

    def run_once(self) -> MediaAsset | None:
        with self._session_factory() as session:
            return process_next_subtitle_transcription_task(
                session=session,
                storage_client=self._storage_client,
                audio_preparer=self._audio_preparer,
                transcriber=self._transcriber,
                stale_after_seconds=self._stale_after_seconds,
            )

    def run_forever(self, *, max_jobs: int | None = None) -> int:
        processed_count = 0
        while max_jobs is None or processed_count < max_jobs:
            try:
                asset = self.run_once()
            except OperationalError:
                sleep_after_database_error(
                    logger=logger,
                    worker_name="Subtitle transcription",
                    retry_seconds=self._poll_interval_seconds,
                    sleep=self._sleep,
                )
                continue
            if asset is None:
                self._sleep(self._poll_interval_seconds)
                continue

            processed_count += 1
            _log_processed_subtitle_task(asset)

        return processed_count


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the subtitle transcription worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one subtitle task, then exit",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Process up to N subtitle tasks before exiting",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=settings.SUBTITLE_WORKER_POLL_INTERVAL_SECONDS,
        help="Seconds to sleep when no subtitle tasks are available",
    )
    parser.add_argument(
        "--stale-after",
        type=float,
        default=settings.SUBTITLE_WORKER_STALE_AFTER_SECONDS,
        help="Seconds after which an in-progress subtitle task can be reclaimed",
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
    audio_preparer = FFmpegSubtitleAudioPreparer()
    transcriber = OpenAISubtitleTranscriber()
    worker = SubtitleTranscriptionWorker(
        storage_client=storage_client,
        audio_preparer=audio_preparer,
        transcriber=transcriber,
        poll_interval_seconds=args.poll_interval,
        stale_after_seconds=args.stale_after,
    )
    try:
        if args.once:
            asset = worker.run_once()
            if asset is None:
                logger.info("No pending subtitle transcription tasks found")
                return 0

            _log_processed_subtitle_task(asset)
            return 0

        worker.run_forever(max_jobs=args.max_jobs)
        return 0
    except KeyboardInterrupt:
        logger.info("Subtitle transcription worker interrupted")
        return 0
    finally:
        transcriber.close()


if __name__ == "__main__":
    raise SystemExit(main())

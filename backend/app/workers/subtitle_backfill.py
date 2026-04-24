from __future__ import annotations

import argparse
import logging

from sqlmodel import Session

from app.core.db import engine
from app.services.subtitle_transcription import backfill_subtitle_transcription_tasks

logger = logging.getLogger(__name__)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill subtitle transcription tasks for existing source media"
    )
    parser.add_argument(
        "--bvid",
        default=None,
        help="Only enqueue subtitle tasks for the specified BVID",
    )
    parser.add_argument(
        "--cid",
        type=int,
        default=None,
        help="Only enqueue subtitle tasks for the specified CID",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Enqueue up to N missing subtitle tasks before exiting",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Command log level",
    )
    return parser


def _log_queued_assets(queued_assets: list[dict[str, object]]) -> None:
    for asset in queued_assets:
        logger.info(
            "Queued subtitle task %s for bvid=%s cid=%s source_asset_id=%s",
            asset["id"],
            asset["bvid"],
            asset["cid"],
            asset["source_asset_id"],
        )


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    queued_asset_summaries: list[dict[str, object]] = []

    try:
        with Session(engine) as session:
            queued_assets = backfill_subtitle_transcription_tasks(
                session,
                bvid=args.bvid,
                cid=args.cid,
                limit=args.limit,
            )
            queued_asset_summaries = [
                {
                    "id": str(asset.id),
                    "bvid": asset.bvid,
                    "cid": asset.cid,
                    "source_asset_id": asset.metadata_json.get("transcription_source_asset_id"),
                }
                for asset in queued_assets
            ]
            session.commit()
    except KeyboardInterrupt:
        logger.info("Subtitle backfill interrupted")
        return 0

    if not queued_asset_summaries:
        logger.info("No missing subtitle transcription tasks found")
        return 0

    logger.info("Enqueued %s subtitle transcription task(s)", len(queued_asset_summaries))
    _log_queued_assets(queued_asset_summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import logging
from collections.abc import Callable


def sleep_after_database_error(
    *,
    logger: logging.Logger,
    worker_name: str,
    retry_seconds: float,
    sleep: Callable[[float], None],
) -> None:
    logger.exception(
        "%s worker lost its database connection; retrying in %.3fs",
        worker_name,
        retry_seconds,
    )
    sleep(retry_seconds)

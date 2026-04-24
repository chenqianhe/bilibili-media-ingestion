from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.workers.metadata_ingest import MetadataIngestWorker


def test_metadata_worker_waits_between_consecutive_jobs() -> None:
    sleeps: list[float] = []
    jobs = iter(
        [
            SimpleNamespace(id=uuid.uuid4(), status="metadata_ready"),
            SimpleNamespace(id=uuid.uuid4(), status="metadata_ready"),
        ]
    )
    worker = MetadataIngestWorker(
        inter_job_delay_seconds=1.25,
        sleep=sleeps.append,
    )
    worker.run_once = lambda: next(jobs)  # type: ignore[method-assign]

    processed_count = worker.run_forever(max_jobs=2)

    assert processed_count == 2
    assert sleeps == [1.25]


def test_metadata_worker_uses_poll_interval_when_no_job_is_available() -> None:
    sleeps: list[float] = []
    jobs = iter(
        [
            None,
            SimpleNamespace(id=uuid.uuid4(), status="metadata_ready"),
        ]
    )
    worker = MetadataIngestWorker(
        poll_interval_seconds=2.5,
        inter_job_delay_seconds=1.25,
        sleep=sleeps.append,
    )
    worker.run_once = lambda: next(jobs)  # type: ignore[method-assign]

    processed_count = worker.run_forever(max_jobs=1)

    assert processed_count == 1
    assert sleeps == [2.5]

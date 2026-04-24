from __future__ import annotations

import uuid
from types import SimpleNamespace

from app.workers.subtitle_transcription import SubtitleTranscriptionWorker


def test_subtitle_worker_uses_poll_interval_when_no_task_is_available() -> None:
    sleeps: list[float] = []
    assets = iter(
        [
            None,
            SimpleNamespace(id=uuid.uuid4(), status="ready"),
        ]
    )
    worker = SubtitleTranscriptionWorker(
        storage_client=object(),
        audio_preparer=object(),
        transcriber=object(),
        poll_interval_seconds=2.0,
        sleep=sleeps.append,
    )
    worker.run_once = lambda: next(assets)  # type: ignore[method-assign]

    processed_count = worker.run_forever(max_jobs=1)

    assert processed_count == 1
    assert sleeps == [2.0]


def test_subtitle_worker_processes_multiple_tasks_without_sleeping_between_them() -> None:
    sleeps: list[float] = []
    assets = iter(
        [
            SimpleNamespace(id=uuid.uuid4(), status="ready"),
            SimpleNamespace(id=uuid.uuid4(), status="ready"),
        ]
    )
    worker = SubtitleTranscriptionWorker(
        storage_client=object(),
        audio_preparer=object(),
        transcriber=object(),
        poll_interval_seconds=2.0,
        sleep=sleeps.append,
    )
    worker.run_once = lambda: next(assets)  # type: ignore[method-assign]

    processed_count = worker.run_forever(max_jobs=2)

    assert processed_count == 2
    assert sleeps == []

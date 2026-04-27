from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest

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


def test_subtitle_worker_logs_error_details_for_failed_task(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleeps: list[float] = []
    asset = SimpleNamespace(
        id=uuid.uuid4(),
        status="failed",
        metadata_json={
            "error_code": "subtitle_transcription_execution_failed",
            "error_message": "OpenAI transcription request failed",
        },
    )
    worker = SubtitleTranscriptionWorker(
        storage_client=object(),
        audio_preparer=object(),
        transcriber=object(),
        poll_interval_seconds=2.0,
        sleep=sleeps.append,
    )
    worker.run_once = lambda: asset  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR, logger="app.workers.subtitle_transcription"):
        processed_count = worker.run_forever(max_jobs=1)

    assert processed_count == 1
    assert sleeps == []
    assert "final status failed" in caplog.text
    assert "error_code=subtitle_transcription_execution_failed" in caplog.text
    assert "error_message=OpenAI transcription request failed" in caplog.text

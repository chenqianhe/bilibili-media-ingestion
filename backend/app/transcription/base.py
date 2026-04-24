from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class SubtitleTranscriptionError(Exception):
    error_code = "subtitle_transcription_failed"


class SubtitleTranscriptionConfigurationError(SubtitleTranscriptionError):
    error_code = "subtitle_transcription_not_configured"


class SubtitleAudioPreparationError(SubtitleTranscriptionError):
    error_code = "subtitle_audio_preparation_failed"


class SubtitleTranscriptionExecutionError(SubtitleTranscriptionError):
    error_code = "subtitle_transcription_execution_failed"


class SubtitleTranscriptionResultError(SubtitleTranscriptionError):
    error_code = "subtitle_transcription_result_invalid"


@dataclass(slots=True)
class PreparedAudioChunk:
    path: Path
    start_seconds: float
    duration_seconds: float | None = None


@dataclass(slots=True)
class SubtitleTranscriptionSegment:
    start_seconds: float
    end_seconds: float
    text: str
    speaker: str | None = None


@dataclass(slots=True)
class SubtitleTranscriptionResult:
    text: str
    segments: list[SubtitleTranscriptionSegment] = field(default_factory=list)
    language: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


class SubtitleAudioPreparer(Protocol):
    def prepare_chunks(
        self,
        *,
        input_path: Path,
        output_dir: Path,
    ) -> list[PreparedAudioChunk]:
        ...


class SubtitleTranscriber(Protocol):
    @property
    def model(self) -> str:
        ...

    @property
    def supports_prompt(self) -> bool:
        ...

    def transcribe(
        self,
        *,
        input_path: Path,
        language: str | None = None,
        prompt: str | None = None,
    ) -> SubtitleTranscriptionResult:
        ...

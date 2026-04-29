from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field


class MediaProcessingError(Exception):
    error_code = "media_processing_failed"


class MediaToolUnavailableError(MediaProcessingError):
    error_code = "media_processing_unavailable"


class MediaProbeExecutionError(MediaProcessingError):
    error_code = "media_probe_failed"


class MediaTranscodeError(MediaProcessingError):
    error_code = "media_transcode_failed"


class MediaThumbnailError(MediaProcessingError):
    error_code = "media_thumbnail_failed"


class MediaResultError(MediaProcessingError):
    error_code = "media_processing_result_invalid"


class MediaProbeResult(BaseModel):
    container_format: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    bitrate: int | None = None
    duration_seconds: float | None = None
    has_video: bool = False
    has_audio: bool = False
    stream_count: int = 0
    raw: dict[str, Any] = Field(default_factory=dict)


class MediaProcessor(Protocol):
    def probe(self, *, input_path: Path) -> MediaProbeResult:
        ...

    def create_proxy_mp4(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_path: Path,
        include_audio_from_video_input: bool = True,
    ) -> None:
        ...

    def create_normalized_mp4(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_path: Path,
        include_audio_from_video_input: bool = True,
    ) -> None:
        ...

    def create_hls_package(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_dir: Path,
        include_audio_from_video_input: bool = True,
        segment_duration_seconds: int = 6,
    ) -> Path:
        ...

    def create_thumbnail(
        self,
        *,
        video_input_path: Path,
        output_path: Path,
        offset_seconds: float | None = None,
    ) -> None:
        ...

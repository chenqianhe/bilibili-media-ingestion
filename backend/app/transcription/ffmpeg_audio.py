from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path

from app.core.config import settings
from app.transcription.base import (
    PreparedAudioChunk,
    SubtitleAudioPreparationError,
)


class FFmpegSubtitleAudioPreparer:
    def __init__(
        self,
        *,
        ffmpeg_binary: str | None = None,
        ffprobe_binary: str | None = None,
        audio_format: str | None = None,
        audio_bitrate: str | None = None,
        compression_level: int | None = None,
        sample_rate: int | None = None,
        max_upload_bytes: int | None = None,
    ) -> None:
        self._ffmpeg_binary = ffmpeg_binary or settings.FFMPEG_BINARY
        self._ffprobe_binary = ffprobe_binary or settings.FFPROBE_BINARY
        self._audio_format = (
            audio_format or settings.SUBTITLE_TRANSCRIPTION_AUDIO_FORMAT
        )
        self._audio_bitrate = (
            audio_bitrate or settings.SUBTITLE_TRANSCRIPTION_AUDIO_BITRATE
        )
        self._compression_level = (
            compression_level
            if compression_level is not None
            else settings.SUBTITLE_TRANSCRIPTION_AUDIO_COMPRESSION_LEVEL
        )
        self._sample_rate = (
            sample_rate or settings.SUBTITLE_TRANSCRIPTION_AUDIO_SAMPLE_RATE
        )
        self._max_upload_bytes = (
            max_upload_bytes or settings.SUBTITLE_TRANSCRIPTION_MAX_UPLOAD_BYTES
        )

    def prepare_chunks(
        self,
        *,
        input_path: Path,
        output_dir: Path,
    ) -> list[PreparedAudioChunk]:
        self._ensure_binary_available(self._ffmpeg_binary, label="ffmpeg")
        self._ensure_binary_available(self._ffprobe_binary, label="ffprobe")
        output_dir.mkdir(parents=True, exist_ok=True)
        audio_path = output_dir / f"audio.{self._audio_format}"
        self._transcode_audio(
            input_path=input_path,
            output_path=audio_path,
        )

        total_duration_seconds = self._probe_duration_seconds(audio_path)
        audio_size_bytes = audio_path.stat().st_size
        if audio_size_bytes <= self._max_upload_bytes:
            return [
                PreparedAudioChunk(
                    path=audio_path,
                    start_seconds=0.0,
                    duration_seconds=total_duration_seconds,
                )
            ]

        required_chunk_count = max(
            2, math.ceil(audio_size_bytes / self._max_upload_bytes)
        )
        max_chunk_count = required_chunk_count + 8
        for chunk_count in range(2, max_chunk_count + 1):
            split_output_dir = output_dir / f"split-{chunk_count:03d}"
            if split_output_dir.exists():
                shutil.rmtree(split_output_dir)
            chunks = self._split_transcoded_audio_evenly(
                input_path=audio_path,
                output_dir=split_output_dir,
                chunk_count=chunk_count,
                total_duration_seconds=total_duration_seconds,
            )
            if all(
                chunk.path.stat().st_size <= self._max_upload_bytes for chunk in chunks
            ):
                return chunks

        raise SubtitleAudioPreparationError(
            "unable to split prepared subtitle audio below the OpenAI upload limit "
            f"of {self._max_upload_bytes} bytes using equal-duration chunks"
        )

    def _transcode_audio(
        self,
        *,
        input_path: Path,
        output_path: Path,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._ffmpeg_binary,
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(self._sample_rate),
            *self._codec_arguments(),
            str(output_path),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "unknown error"
            )
            raise SubtitleAudioPreparationError(
                f"ffmpeg failed to prepare subtitle audio: {detail}"
            )

    def _probe_duration_seconds(
        self,
        input_path: Path,
    ) -> float:
        command = [
            self._ffprobe_binary,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(input_path),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "unknown error"
            )
            raise SubtitleAudioPreparationError(
                f"ffprobe failed to inspect subtitle audio: {detail}"
            )
        raw_duration = completed.stdout.strip()
        try:
            duration_seconds = float(raw_duration)
        except ValueError as exc:
            raise SubtitleAudioPreparationError(
                f"ffprobe returned invalid subtitle audio duration: {raw_duration or 'empty'}"
            ) from exc
        if duration_seconds <= 0:
            raise SubtitleAudioPreparationError(
                f"ffprobe returned a non-positive subtitle audio duration: {duration_seconds}"
            )
        return duration_seconds

    def _split_transcoded_audio_evenly(
        self,
        *,
        input_path: Path,
        output_dir: Path,
        chunk_count: int,
        total_duration_seconds: float,
    ) -> list[PreparedAudioChunk]:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_pattern = output_dir / f"chunk-%03d.{self._audio_format}"
        segment_boundaries = [
            round(total_duration_seconds * index / chunk_count, 6)
            for index in range(1, chunk_count)
        ]
        command = [
            self._ffmpeg_binary,
            "-y",
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_format",
            self._segment_format(),
            "-segment_times",
            ",".join(str(boundary) for boundary in segment_boundaries),
            "-reset_timestamps",
            "1",
            str(output_pattern),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()
                or completed.stdout.strip()
                or "unknown error"
            )
            raise SubtitleAudioPreparationError(
                f"ffmpeg failed to split prepared subtitle audio: {detail}"
            )

        paths = [
            path
            for path in sorted(output_dir.glob(f"chunk-*.{self._audio_format}"))
            if path.is_file()
        ]
        if len(paths) != chunk_count:
            raise SubtitleAudioPreparationError(
                "ffmpeg produced an unexpected number of subtitle audio chunks: "
                f"expected {chunk_count}, got {len(paths)}"
            )
        starts = [0.0, *segment_boundaries]
        ends = [*segment_boundaries, total_duration_seconds]
        return [
            PreparedAudioChunk(
                path=path,
                start_seconds=starts[index],
                duration_seconds=max(0.0, ends[index] - starts[index]),
            )
            for index, path in enumerate(paths)
        ]

    def _ensure_binary_available(self, binary: str, *, label: str) -> None:
        if shutil.which(binary) is None:
            raise SubtitleAudioPreparationError(
                f"{label} binary '{binary}' is not available"
            )

    def _codec_arguments(self) -> list[str]:
        if self._audio_format == "flac":
            return [
                "-c:a",
                "flac",
                "-compression_level",
                str(self._compression_level),
            ]
        if self._audio_format == "m4a":
            return [
                "-c:a",
                "aac",
                "-b:a",
                self._audio_bitrate,
            ]
        raise SubtitleAudioPreparationError(
            f"Unsupported subtitle audio format: {self._audio_format}"
        )

    def _segment_format(self) -> str:
        if self._audio_format == "flac":
            return "flac"
        if self._audio_format == "m4a":
            return "ipod"
        raise SubtitleAudioPreparationError(
            f"Unsupported subtitle audio format: {self._audio_format}"
        )

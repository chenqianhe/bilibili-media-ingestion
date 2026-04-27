from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.processor.base import (
    MediaProbeExecutionError,
    MediaProbeResult,
    MediaResultError,
    MediaThumbnailError,
    MediaToolUnavailableError,
    MediaTranscodeError,
)

_MP4_CONTAINER_ALIASES = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}
_SUPPORTED_VIDEO_ACCELERATORS = {"cpu", "none", "videotoolbox", "nvenc"}


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _parse_ratio(value: Any) -> float | None:
    if value in (None, "", "0/0"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    if "/" not in value:
        return _coerce_float(value)
    numerator, denominator = value.split("/", maxsplit=1)
    try:
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        return float(numerator) / denominator_value
    except ValueError:
        return None


def _normalize_container_format(
    *,
    format_name: str | None,
    input_path: Path,
) -> str | None:
    candidates = [segment.strip() for segment in (format_name or "").split(",") if segment.strip()]
    extension = input_path.suffix.lower().lstrip(".")
    if extension:
        if extension in candidates:
            return extension
        if extension in {"mp4", "m4a"} and _MP4_CONTAINER_ALIASES.intersection(candidates):
            return extension
        if extension in {"jpg", "jpeg"} and "image2" in candidates:
            return "jpeg"
    if _MP4_CONTAINER_ALIASES.intersection(candidates):
        return "mp4"
    if "image2" in candidates and extension in {"jpg", "jpeg"}:
        return "jpeg"
    return candidates[0] if candidates else extension or None


class FFmpegMediaProcessor:
    def __init__(
        self,
        *,
        ffmpeg_binary: str | None = None,
        ffprobe_binary: str | None = None,
        video_accelerator: str | None = None,
    ) -> None:
        self._ffmpeg_binary = ffmpeg_binary or settings.FFMPEG_BINARY
        self._ffprobe_binary = ffprobe_binary or settings.FFPROBE_BINARY
        self._video_accelerator = self._normalize_video_accelerator(
            video_accelerator or settings.FFMPEG_VIDEO_ACCELERATOR
        )

    def probe(self, *, input_path: Path) -> MediaProbeResult:
        self._ensure_binary_available(self._ffprobe_binary, label="ffprobe")

        command = [
            self._ffprobe_binary,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(input_path),
        ]
        completed = self._run(command, error_cls=MediaProbeExecutionError)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise MediaResultError("ffprobe returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise MediaResultError("ffprobe returned a non-object payload")

        format_payload = payload.get("format")
        if not isinstance(format_payload, dict):
            format_payload = {}
        streams = payload.get("streams")
        if not isinstance(streams, list):
            streams = []

        video_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "video"
            ),
            None,
        )
        audio_stream = next(
            (
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "audio"
            ),
            None,
        )

        return MediaProbeResult(
            container_format=_normalize_container_format(
                format_name=(
                    format_payload.get("format_name")
                    if isinstance(format_payload.get("format_name"), str)
                    else None
                ),
                input_path=input_path,
            ),
            video_codec=(
                video_stream.get("codec_name")
                if isinstance(video_stream, dict)
                and isinstance(video_stream.get("codec_name"), str)
                else None
            ),
            audio_codec=(
                audio_stream.get("codec_name")
                if isinstance(audio_stream, dict)
                and isinstance(audio_stream.get("codec_name"), str)
                else None
            ),
            width=(
                _coerce_int(video_stream.get("width"))
                if isinstance(video_stream, dict)
                else None
            ),
            height=(
                _coerce_int(video_stream.get("height"))
                if isinstance(video_stream, dict)
                else None
            ),
            fps=(
                _parse_ratio(video_stream.get("avg_frame_rate"))
                or _parse_ratio(video_stream.get("r_frame_rate"))
                if isinstance(video_stream, dict)
                else None
            ),
            bitrate=_coerce_int(format_payload.get("bit_rate"))
            or (
                _coerce_int(video_stream.get("bit_rate"))
                if isinstance(video_stream, dict)
                else None
            )
            or (
                _coerce_int(audio_stream.get("bit_rate"))
                if isinstance(audio_stream, dict)
                else None
            ),
            duration_seconds=_coerce_float(format_payload.get("duration"))
            or (
                _coerce_float(video_stream.get("duration"))
                if isinstance(video_stream, dict)
                else None
            )
            or (
                _coerce_float(audio_stream.get("duration"))
                if isinstance(audio_stream, dict)
                else None
            ),
            has_video=isinstance(video_stream, dict),
            has_audio=isinstance(audio_stream, dict),
            stream_count=sum(1 for stream in streams if isinstance(stream, dict)),
            raw=payload,
        )

    def create_proxy_mp4(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_path: Path,
        include_audio_from_video_input: bool = True,
    ) -> None:
        self._ensure_binary_available(self._ffmpeg_binary, label="ffmpeg")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._build_mp4_command(
            video_input_path=video_input_path,
            audio_input_path=audio_input_path,
            output_path=output_path,
            include_audio_from_video_input=include_audio_from_video_input,
            video_filter=(
                "scale=1280:720:force_original_aspect_ratio=decrease:"
                "force_divisible_by=2"
            ),
            preset="veryfast",
            video_bitrate="2500k",
            audio_bitrate="128k",
            crf="23",
            maxrate="2500k",
            bufsize="5000k",
        )

        self._run(command, error_cls=MediaTranscodeError)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise MediaResultError("ffmpeg did not create a proxy MP4 output")

    def create_normalized_mp4(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_path: Path,
        include_audio_from_video_input: bool = True,
    ) -> None:
        self._ensure_binary_available(self._ffmpeg_binary, label="ffmpeg")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = self._build_mp4_command(
            video_input_path=video_input_path,
            audio_input_path=audio_input_path,
            output_path=output_path,
            include_audio_from_video_input=include_audio_from_video_input,
            video_filter="scale=trunc(iw/2)*2:trunc(ih/2)*2",
            preset="fast",
            video_bitrate="5000k",
            audio_bitrate="192k",
            crf="23",
        )

        self._run(command, error_cls=MediaTranscodeError)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise MediaResultError("ffmpeg did not create a normalized MP4 output")

    def create_hls_package(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_dir: Path,
        include_audio_from_video_input: bool = True,
        segment_duration_seconds: int = 6,
    ) -> Path:
        self._ensure_binary_available(self._ffmpeg_binary, label="ffmpeg")

        output_dir.mkdir(parents=True, exist_ok=True)
        media_playlist_path = output_dir / "stream.m3u8"
        master_playlist_path = output_dir / "master.m3u8"
        segment_pattern = output_dir / "segment_%05d.ts"

        command = [
            self._ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_input_path),
        ]
        if audio_input_path is not None:
            command.extend(["-i", str(audio_input_path)])
        command.extend(["-map", "0:v:0"])
        has_audio_output = audio_input_path is not None or include_audio_from_video_input
        if audio_input_path is not None:
            command.extend(["-map", "1:a:0"])
        elif include_audio_from_video_input:
            command.extend(["-map", "0:a:0?"])
        else:
            command.append("-an")

        if audio_input_path is None and video_input_path.suffix.lower() == ".mp4":
            command.extend(["-c:v", "copy"])
            if has_audio_output:
                command.extend(["-c:a", "copy"])
        else:
            command.extend(
                [
                    "-vf",
                    (
                        "scale=1280:720:force_original_aspect_ratio=decrease:"
                        "force_divisible_by=2"
                    ),
                    "-c:v",
                    *self._video_encoding_args(
                        preset="veryfast",
                        video_bitrate="2500k",
                        crf="23",
                        maxrate="2500k",
                        bufsize="5000k",
                    ),
                    "-pix_fmt",
                    "yuv420p",
                ]
            )
            if has_audio_output:
                command.extend(["-c:a", "aac", "-b:a", "128k", "-shortest"])

        command.extend(
            [
                "-f",
                "hls",
                "-hls_time",
                str(segment_duration_seconds),
                "-hls_list_size",
                "0",
                "-hls_playlist_type",
                "vod",
                "-hls_flags",
                "independent_segments",
                "-hls_segment_filename",
                str(segment_pattern),
                str(media_playlist_path),
            ]
        )

        self._run(command, error_cls=MediaTranscodeError)
        if not media_playlist_path.is_file() or media_playlist_path.stat().st_size <= 0:
            raise MediaResultError("ffmpeg did not create an HLS media playlist")
        if not any(output_dir.glob("segment_*.ts")):
            raise MediaResultError("ffmpeg did not create HLS media segments")

        master_playlist_path.write_text(
            "\n".join(
                [
                    "#EXTM3U",
                    "#EXT-X-VERSION:3",
                    "#EXT-X-STREAM-INF:BANDWIDTH=2500000,AVERAGE-BANDWIDTH=1800000",
                    media_playlist_path.name,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return master_playlist_path

    def create_thumbnail(
        self,
        *,
        video_input_path: Path,
        output_path: Path,
        offset_seconds: float | None = None,
    ) -> None:
        self._ensure_binary_available(self._ffmpeg_binary, label="ffmpeg")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            self._ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
        ]
        if offset_seconds is not None and offset_seconds > 0:
            command.extend(["-ss", f"{offset_seconds:.3f}"])
        command.extend(
            [
                "-i",
                str(video_input_path),
                "-frames:v",
                "1",
                str(output_path),
            ]
        )

        self._run(command, error_cls=MediaThumbnailError)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise MediaResultError("ffmpeg did not create a thumbnail output")

    def _normalize_video_accelerator(self, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _SUPPORTED_VIDEO_ACCELERATORS:
            supported = ", ".join(sorted(_SUPPORTED_VIDEO_ACCELERATORS))
            raise ValueError(
                "Unsupported FFMPEG_VIDEO_ACCELERATOR "
                f"{value!r}; expected one of: {supported}"
            )
        return "cpu" if normalized == "none" else normalized

    def _video_encoding_args(
        self,
        *,
        preset: str,
        video_bitrate: str,
        crf: str,
        maxrate: str | None = None,
        bufsize: str | None = None,
    ) -> list[str]:
        if self._video_accelerator == "videotoolbox":
            return [
                "h264_videotoolbox",
                "-b:v",
                video_bitrate,
            ]

        if self._video_accelerator == "nvenc":
            args = [
                "h264_nvenc",
                "-preset",
                "fast",
                "-rc",
                "vbr",
                "-cq:v",
                crf,
                "-b:v",
                video_bitrate,
            ]
            if maxrate is not None:
                args.extend(["-maxrate", maxrate])
            if bufsize is not None:
                args.extend(["-bufsize", bufsize])
            return args

        args = [
            "libx264",
            "-preset",
            preset,
            "-crf",
            crf,
        ]
        if maxrate is not None:
            args.extend(["-maxrate", maxrate])
        if bufsize is not None:
            args.extend(["-bufsize", bufsize])
        return args

    def _build_mp4_command(
        self,
        *,
        video_input_path: Path,
        audio_input_path: Path | None,
        output_path: Path,
        include_audio_from_video_input: bool,
        video_filter: str,
        preset: str,
        video_bitrate: str,
        audio_bitrate: str,
        crf: str,
        maxrate: str | None = None,
        bufsize: str | None = None,
    ) -> list[str]:
        command = [
            self._ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_input_path),
        ]
        if audio_input_path is not None:
            command.extend(["-i", str(audio_input_path)])
        command.extend(["-map", "0:v:0"])
        if audio_input_path is not None:
            command.extend(["-map", "1:a:0"])
        elif include_audio_from_video_input:
            command.extend(["-map", "0:a:0?"])
        else:
            command.append("-an")
        command.extend(
            [
                "-vf",
                video_filter,
                "-c:v",
                *self._video_encoding_args(
                    preset=preset,
                    video_bitrate=video_bitrate,
                    crf=crf,
                    maxrate=maxrate,
                    bufsize=bufsize,
                ),
                "-pix_fmt",
                "yuv420p",
            ]
        )
        command.extend(["-movflags", "+faststart"])
        if audio_input_path is not None or include_audio_from_video_input:
            command.extend(["-c:a", "aac", "-b:a", audio_bitrate, "-shortest"])
        command.append(str(output_path))
        return command

    def _ensure_binary_available(self, binary: str, *, label: str) -> None:
        if shutil.which(binary) is None:
            raise MediaToolUnavailableError(
                f"Required media tool '{label}' is not available as '{binary}'"
            )

    def _run(
        self,
        command: list[str],
        *,
        error_cls: type[
            MediaProbeExecutionError | MediaThumbnailError | MediaTranscodeError
        ],
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise error_cls(f"ffmpeg tooling failed: {detail}")
        return completed

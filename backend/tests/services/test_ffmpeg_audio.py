from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import app.transcription.ffmpeg_audio as ffmpeg_audio_module
from app.transcription.base import SubtitleAudioPreparationError
from app.transcription.ffmpeg_audio import FFmpegSubtitleAudioPreparer


def test_prepare_chunks_transcodes_source_media_to_single_m4a(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    input_path = tmp_path / "source.mp4"
    input_path.write_bytes(b"video")

    monkeypatch.setattr(
        ffmpeg_audio_module.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}",
    )

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> SimpleNamespace:
        del capture_output, check, text
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stderr="", stdout="90.0\n")
        output_path = Path(command[-1])
        output_path.write_bytes(b"aac-audio")
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(ffmpeg_audio_module.subprocess, "run", fake_run)

    preparer = FFmpegSubtitleAudioPreparer(
        ffmpeg_binary="ffmpeg",
        ffprobe_binary="ffprobe",
        audio_format="m4a",
        sample_rate=16000,
        max_upload_bytes=1024,
    )

    chunks = preparer.prepare_chunks(
        input_path=input_path,
        output_dir=tmp_path / "audio",
    )

    assert len(chunks) == 1
    assert chunks[0].path.name == "audio.m4a"
    assert chunks[0].start_seconds == 0.0

    command = commands[0]
    assert command[0] == "ffmpeg"
    assert "-vn" in command
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-b:a") + 1] == "48k"
    assert command[-1].endswith("audio.m4a")


def test_prepare_chunks_rejects_audio_when_equal_splits_still_exceed_upload_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path = tmp_path / "source.mp4"
    input_path.write_bytes(b"video")

    monkeypatch.setattr(
        ffmpeg_audio_module.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}",
    )

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> SimpleNamespace:
        del capture_output, check, text
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stderr="", stdout="2.0\n")
        output_path = Path(command[-1])
        if "-segment_times" in command:
            segment_boundaries = command[command.index("-segment_times") + 1].split(",")
            chunk_count = len(segment_boundaries) + 1
            for index in range(chunk_count):
                (output_path.parent / f"chunk-{index:03d}.m4a").write_bytes(b"x" * 10)
        else:
            output_path.write_bytes(b"x" * 20)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(ffmpeg_audio_module.subprocess, "run", fake_run)

    preparer = FFmpegSubtitleAudioPreparer(
        ffmpeg_binary="ffmpeg",
        ffprobe_binary="ffprobe",
        audio_format="m4a",
        max_upload_bytes=4,
    )

    with pytest.raises(
        SubtitleAudioPreparationError,
        match="unable to split prepared subtitle audio below the OpenAI upload limit",
    ):
        preparer.prepare_chunks(
            input_path=input_path,
            output_dir=tmp_path / "audio",
        )


def test_prepare_chunks_resplits_oversized_audio_by_observed_file_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    input_path = tmp_path / "source.mp4"
    input_path.write_bytes(b"video")

    monkeypatch.setattr(
        ffmpeg_audio_module.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}",
    )

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        check: bool,
        text: bool,
    ) -> SimpleNamespace:
        del capture_output, check, text
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stderr="", stdout="120.0\n")
        output_path = Path(command[-1])
        if "-segment_times" not in command:
            output_path.write_bytes(b"x" * 100)
        elif command[command.index("-segment_times") + 1] == "60.0":
            (output_path.parent / "chunk-000.m4a").write_bytes(b"x" * 70)
            (output_path.parent / "chunk-001.m4a").write_bytes(b"x" * 30)
        else:
            (output_path.parent / "chunk-000.m4a").write_bytes(b"x" * 20)
            (output_path.parent / "chunk-001.m4a").write_bytes(b"x" * 20)
            (output_path.parent / "chunk-002.m4a").write_bytes(b"x" * 20)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(ffmpeg_audio_module.subprocess, "run", fake_run)

    preparer = FFmpegSubtitleAudioPreparer(
        ffmpeg_binary="ffmpeg",
        ffprobe_binary="ffprobe",
        audio_format="m4a",
        max_upload_bytes=60,
    )

    chunks = preparer.prepare_chunks(
        input_path=input_path,
        output_dir=tmp_path / "audio",
    )

    assert [round(chunk.start_seconds, 3) for chunk in chunks] == [0.0, 40.0, 80.0]
    assert [chunk.path.name for chunk in chunks] == [
        "chunk-000.m4a",
        "chunk-001.m4a",
        "chunk-002.m4a",
    ]
    split_commands = [command for command in commands if "-segment_times" in command]
    assert [command[command.index("-segment_times") + 1] for command in split_commands] == [
        "60.0",
        "40.0,80.0",
    ]

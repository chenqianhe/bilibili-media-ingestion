from __future__ import annotations

from pathlib import Path

import httpx

from app.transcription.openai_stt import OpenAISubtitleTranscriber


class RecordingClient:
    def __init__(self, *, response_json: dict[str, object]) -> None:
        self.response_json = response_json
        self.calls: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, str],
        files: dict[str, tuple[str, object, str]],
    ) -> httpx.Response:
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "data": data,
                "files": files,
            }
        )
        return httpx.Response(200, json=self.response_json)

    def close(self) -> None:
        return None


def test_diarize_transcription_sends_supported_request_fields(tmp_path: Path) -> None:
    input_path = tmp_path / "audio.flac"
    input_path.write_bytes(b"audio")
    client = RecordingClient(
        response_json={
            "text": "你好",
            "language": "zh",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.2,
                    "text": "你好",
                    "speaker": "speaker_0",
                }
            ],
            "usage": {"type": "duration", "seconds": 1},
        }
    )
    transcriber = OpenAISubtitleTranscriber(
        api_key="sk-test",
        client=client,
        model="gpt-4o-transcribe-diarize",
        temperature=0.2,
    )
    result = transcriber.transcribe(
        input_path=input_path,
        language="zh",
        prompt="should-be-ignored",
    )

    call = client.calls[0]
    form_fields = call["data"]
    assert form_fields["model"] == "gpt-4o-transcribe-diarize"
    assert form_fields["language"] == "zh"
    assert form_fields["temperature"] == "0.2"
    assert form_fields["response_format"] == "diarized_json"
    assert form_fields["chunking_strategy"] == "auto"
    assert "prompt" not in form_fields
    assert result.language == "zh"
    assert result.segments[0].speaker == "speaker_0"


def test_whisper_transcription_sends_prompt_and_segment_timestamps(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "audio.flac"
    input_path.write_bytes(b"audio")
    client = RecordingClient(
        response_json={
            "text": "第一句",
            "language": "zh",
            "segments": [
                {
                    "start": 0.0,
                    "end": 1.5,
                    "text": "第一句",
                }
            ],
        }
    )
    transcriber = OpenAISubtitleTranscriber(
        api_key="sk-test",
        client=client,
        model="whisper-1",
        temperature=0.0,
    )
    transcriber.transcribe(
        input_path=input_path,
        language="zh",
        prompt="科技访谈",
    )

    form_fields = client.calls[0]["data"]
    assert form_fields["prompt"] == "科技访谈"
    assert form_fields["response_format"] == "verbose_json"
    assert form_fields["timestamp_granularities[]"] == "segment"

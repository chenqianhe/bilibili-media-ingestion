from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.transcription.base import (
    SubtitleTranscriptionConfigurationError,
    SubtitleTranscriptionExecutionError,
    SubtitleTranscriptionResult,
    SubtitleTranscriptionResultError,
    SubtitleTranscriptionSegment,
)

_TIMED_SEGMENT_MODELS = {"whisper-1", "gpt-4o-transcribe-diarize"}
_PROMPT_CAPABLE_MODELS = {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"}


def _coerce_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


class OpenAISubtitleTranscriber:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        temperature: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key or settings.OPENAI_API_KEY
        if not self._api_key:
            raise SubtitleTranscriptionConfigurationError(
                "OPENAI_API_KEY is required for subtitle transcription"
            )

        self._base_url = (base_url or settings.OPENAI_API_BASE_URL).rstrip("/")
        self._model = model or settings.OPENAI_TRANSCRIPTION_MODEL
        self._timeout_seconds = (
            timeout_seconds or settings.OPENAI_TRANSCRIPTION_TIMEOUT_SECONDS
        )
        self._temperature = (
            temperature
            if temperature is not None
            else settings.OPENAI_TRANSCRIPTION_TEMPERATURE
        )
        self._client = client or httpx.Client(timeout=self._timeout_seconds)
        self._owns_client = client is None

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_prompt(self) -> bool:
        return self._model in _PROMPT_CAPABLE_MODELS and self._model != "gpt-4o-transcribe-diarize"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def transcribe(
        self,
        *,
        input_path: Path,
        language: str | None = None,
        prompt: str | None = None,
    ) -> SubtitleTranscriptionResult:
        if not input_path.is_file():
            raise SubtitleTranscriptionExecutionError(
                f"Subtitle transcription input does not exist: {input_path}"
            )

        form_fields: list[tuple[str, str]] = [("model", self._model)]
        if language:
            form_fields.append(("language", language))
        form_fields.append(("temperature", str(self._temperature)))

        if self._model == "whisper-1":
            form_fields.append(("response_format", "verbose_json"))
            form_fields.append(("timestamp_granularities[]", "segment"))
        elif self._model == "gpt-4o-transcribe-diarize":
            form_fields.append(("response_format", "diarized_json"))
            form_fields.append(("chunking_strategy", "auto"))
        else:
            form_fields.append(("response_format", "json"))

        if prompt and self.supports_prompt:
            form_fields.append(("prompt", prompt))

        content_type = mimetypes.guess_type(input_path.name)[0] or "application/octet-stream"
        try:
            with input_path.open("rb") as audio_file:
                response = self._client.post(
                    f"{self._base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=dict(form_fields),
                    files={
                        "file": (
                            input_path.name,
                            audio_file,
                            content_type,
                        )
                    },
                )
        except httpx.HTTPError as exc:
            raise SubtitleTranscriptionExecutionError(
                f"OpenAI transcription request failed: {exc}"
            ) from exc

        if response.status_code >= 400:
            detail = self._error_detail(response)
            raise SubtitleTranscriptionExecutionError(
                f"OpenAI transcription request failed: {detail}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SubtitleTranscriptionResultError(
                "OpenAI transcription response was not valid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise SubtitleTranscriptionResultError(
                "OpenAI transcription response was not a JSON object"
            )

        text = payload.get("text")
        if not isinstance(text, str):
            raise SubtitleTranscriptionResultError(
                "OpenAI transcription response did not include text"
            )

        return SubtitleTranscriptionResult(
            text=text,
            language=payload.get("language")
            if isinstance(payload.get("language"), str)
            else None,
            segments=self._parse_segments(payload),
            raw=payload,
            usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else {},
        )

    def _parse_segments(
        self,
        payload: dict[str, Any],
    ) -> list[SubtitleTranscriptionSegment]:
        raw_segments = payload.get("segments")
        if raw_segments is None:
            if self._model in _TIMED_SEGMENT_MODELS:
                raise SubtitleTranscriptionResultError(
                    f"Configured model '{self._model}' did not return timed segments"
                )
            return []
        if not isinstance(raw_segments, list):
            raise SubtitleTranscriptionResultError(
                "OpenAI transcription segments payload was invalid"
            )

        segments: list[SubtitleTranscriptionSegment] = []
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                continue
            start_seconds = _coerce_float(raw_segment.get("start"))
            end_seconds = _coerce_float(raw_segment.get("end"))
            text = raw_segment.get("text")
            if start_seconds is None or end_seconds is None or not isinstance(text, str):
                continue
            segments.append(
                SubtitleTranscriptionSegment(
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                    text=text,
                    speaker=raw_segment.get("speaker")
                    if isinstance(raw_segment.get("speaker"), str)
                    else None,
                )
            )

        if not segments and self._model in _TIMED_SEGMENT_MODELS:
            raise SubtitleTranscriptionResultError(
                f"Configured model '{self._model}' returned no usable timed segments"
            )
        return segments

    def _error_detail(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"]
        stripped_text = response.text.strip()
        if stripped_text:
            return stripped_text
        return f"HTTP {response.status_code}"

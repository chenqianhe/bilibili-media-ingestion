from __future__ import annotations

from typing import Any

_NUL_BYTE = "\x00"


def strip_nul_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace(_NUL_BYTE, "")


def strip_nul_bytes(value: Any) -> Any:
    if isinstance(value, str):
        return strip_nul_text(value)
    if isinstance(value, dict):
        return {
            strip_nul_text(key) if isinstance(key, str) else key: strip_nul_bytes(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [strip_nul_bytes(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_nul_bytes(item) for item in value)
    return value

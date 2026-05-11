from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.exc import NoInspectionAvailable

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


def strip_nul_bytes_from_model(value: object) -> None:
    try:
        inspected = inspect(value)
    except NoInspectionAvailable:
        return

    mapper = inspected.mapper
    for attribute in mapper.column_attrs:
        key = attribute.key
        current_value = getattr(value, key)
        sanitized_value = strip_nul_bytes(current_value)
        if sanitized_value != current_value:
            setattr(value, key, sanitized_value)

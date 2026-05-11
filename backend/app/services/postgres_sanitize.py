from __future__ import annotations

from typing import Any, overload

_POSTGRES_NUL_BYTE = "\x00"


@overload
def sanitize_postgres_text(value: str) -> str: ...


@overload
def sanitize_postgres_text(value: None) -> None: ...


def sanitize_postgres_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace(_POSTGRES_NUL_BYTE, "")


def sanitize_postgres_json(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_postgres_text(value)
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            sanitized_key = sanitize_postgres_text(key) if isinstance(key, str) else key
            sanitized[sanitized_key] = sanitize_postgres_json(item)
        return sanitized
    if isinstance(value, list | tuple):
        return [sanitize_postgres_json(item) for item in value]
    return value

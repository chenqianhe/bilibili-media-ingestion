from typing import Any

from sqlmodel import Session

from app.ingest_models import AuditEvent
from app.services.postgres_sanitize import (
    sanitize_postgres_json,
    sanitize_postgres_text,
)


def record_audit_event(
    *,
    session: Session,
    actor: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    message: str | None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor=sanitize_postgres_text(actor),
        action=sanitize_postgres_text(action),
        resource_type=sanitize_postgres_text(resource_type),
        resource_id=sanitize_postgres_text(resource_id),
        message=sanitize_postgres_text(message),
        payload=sanitize_postgres_json(payload or {}),
    )
    session.add(event)
    return event

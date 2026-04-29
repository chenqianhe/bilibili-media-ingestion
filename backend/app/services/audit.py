from typing import Any

from sqlmodel import Session

from app.ingest_models import AuditEvent


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
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        message=message,
        payload=payload or {},
    )
    session.add(event)
    return event


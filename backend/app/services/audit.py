from typing import Any

from sqlmodel import Session

from app.ingest_models import AuditEvent
from app.services.text_sanitization import strip_nul_bytes, strip_nul_text


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
        actor=strip_nul_text(actor),
        action=strip_nul_text(action) or "",
        resource_type=strip_nul_text(resource_type) or "",
        resource_id=strip_nul_text(resource_id),
        message=strip_nul_text(message),
        payload=strip_nul_bytes(payload or {}),
    )
    session.add(event)
    return event

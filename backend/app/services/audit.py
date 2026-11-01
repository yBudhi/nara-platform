import uuid

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "api_key",
    "apikey",
    "private_key",
)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def _sanitize(value):
    if isinstance(value, dict):
        return {
            key: _sanitize(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def record_audit_event(
    session: Session,
    *,
    company_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    before_data: dict | None = None,
    after_data: dict | None = None,
    metadata: dict | None = None,
    request_id: str | None = None,
) -> AuditLog:
    audit_log = AuditLog(
        company_id=company_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_data=_sanitize(before_data),
        after_data=_sanitize(after_data),
        audit_metadata=_sanitize(metadata),
        request_id=request_id,
    )
    session.add(audit_log)
    session.flush()
    return audit_log

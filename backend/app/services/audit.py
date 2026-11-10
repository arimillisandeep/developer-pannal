"""Audit trail: every privileged action is written with actor id + timestamp."""
import json

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def log(db: Session, actor: User | None, action: str, entity_type: str = "", entity_id: str = "",
        request: Request | None = None, **meta) -> AuditLog:
    ip, ua = "", ""
    if request is not None:
        ip = request.client.host if request.client else ""
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            ip = fwd.split(",")[0].strip()
        ua = (request.headers.get("user-agent") or "")[:400]

    entry = AuditLog(
        actor_id=getattr(actor, "id", None),
        actor_role=getattr(actor, "role", ""),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id or "",
        ip=ip,
        user_agent=ua,
        meta_json=json.dumps(meta, default=str),
    )
    db.add(entry)
    db.flush()
    return entry

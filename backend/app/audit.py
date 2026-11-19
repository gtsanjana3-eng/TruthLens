"""Append-only, hash-chained audit log."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog, utcnow

_lock = threading.Lock()


def _entry_hash(prev: str | None, action: str, user_id: str | None, case_id: str | None, evidence_id: str | None, detail: dict, ts: str) -> str:
    payload = json.dumps([prev, action, user_id, case_id, evidence_id, detail, ts], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def record(db: Session, action: str, *, user_id: str | None = None, case_id: str | None = None,
           evidence_id: str | None = None, detail: dict | None = None, ip: str | None = None, commit: bool = True) -> AuditLog:
    detail = detail or {}
    with _lock:
        prev = db.scalar(select(AuditLog.entry_hash).order_by(AuditLog.id.desc()).limit(1))
        ts = utcnow()
        entry = AuditLog(user_id=user_id, case_id=case_id, evidence_id=evidence_id, action=action, detail=detail,
                         ip_address=ip, prev_hash=prev, created_at=ts,
                         entry_hash=_entry_hash(prev, action, user_id, case_id, evidence_id, detail, ts.isoformat()))
        db.add(entry)
        if commit:
            db.commit()
        else:
            db.flush()
    return entry


def verify_chain(db: Session) -> dict:
    """Recompute the hash chain; returns the first broken entry id if tampering is detected."""
    prev = None
    count = 0
    for e in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        ts = (e.created_at.astimezone(timezone.utc) if e.created_at.tzinfo else e.created_at.replace(tzinfo=timezone.utc)).isoformat()  # DB may return another zone
        expected = _entry_hash(prev, e.action, e.user_id, e.case_id, e.evidence_id, e.detail, ts)
        if e.prev_hash != prev or e.entry_hash != expected:
            return {"valid": False, "entries_checked": count, "broken_at": e.id}
        prev = e.entry_hash
        count += 1
    return {"valid": True, "entries_checked": count, "broken_at": None}

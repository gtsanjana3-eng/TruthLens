"""Retention policy job: purge soft-deleted cases/evidence older than RETENTION_DAYS.

Originals are securely overwritten and unlinked only when no remaining evidence record
references the same content-addressed file. Every purge is written to the audit log.
Run periodically (cron / scheduled task):  python -m scripts.retention [--dry-run]
"""
from __future__ import annotations

import argparse
from datetime import timedelta

from sqlalchemy import select

from app import audit
from app.config import get_settings
from app.database import SessionLocal
from app.models import EvidenceFile, InvestigationCase, utcnow
from app.storage import evidence_path, secure_delete


def _aware(dt):
    from datetime import timezone

    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def purge(dry_run: bool = False, days: int | None = None) -> dict:
    s = get_settings()
    cutoff = utcnow() - timedelta(days=s.retention_days if days is None else days)
    db = SessionLocal()
    stats = {"cases": 0, "evidence": 0, "files_deleted": 0}
    try:
        cases = [c for c in db.scalars(select(InvestigationCase).where(InvestigationCase.deleted_at.is_not(None))) if _aware(c.deleted_at) <= cutoff]
        evs = [e for e in db.scalars(select(EvidenceFile).where(EvidenceFile.deleted_at.is_not(None))) if _aware(e.deleted_at) <= cutoff]
        doomed = {e.id: e for e in evs}
        for c in cases:
            for e in c.evidence:
                doomed[e.id] = e
        paths = {e.stored_path for e in doomed.values()}
        still_used = set(db.scalars(select(EvidenceFile.stored_path).where(EvidenceFile.stored_path.in_(paths), EvidenceFile.id.not_in(list(doomed)))))
        for p in paths - still_used:
            if not dry_run:
                secure_delete(evidence_path(p))
            stats["files_deleted"] += 1
        stats["evidence"], stats["cases"] = len(doomed), len(cases)
        if not dry_run:
            for e in evs:
                if e.case not in cases:
                    db.delete(e)
            for c in cases:
                db.delete(c)
            audit.record(db, "retention.purged", detail={**stats, "cutoff": cutoff.isoformat()})
    finally:
        db.close()
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=None)
    a = ap.parse_args()
    print(purge(a.dry_run, a.days))

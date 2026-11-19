"""Investigation case management: CRUD, timeline, dashboard."""
from __future__ import annotations

import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..database import get_db
from ..models import (AnalysisJob, AuditLog, CaseMember, CaseStatus, CrossModalFinding, EvidenceFile, EvidenceRelationship,
                      InvestigationCase, JobStatus, ManipulationIndicator, Role, User, utcnow)
from ..ratelimit import client_ip, limiter
from ..schemas import CaseCreate, CaseOut, CaseUpdate, Page
from ..security import get_case_for_user, get_current_user

router = APIRouter(prefix="/api/cases", tags=["cases"], dependencies=[Depends(limiter("default"))])


def _visible_cases(user: User):
    q = select(InvestigationCase).where(InvestigationCase.deleted_at.is_(None))
    if user.role != Role.admin:
        member_cases = select(CaseMember.case_id).where(CaseMember.user_id == user.id)
        q = q.where(or_(InvestigationCase.owner_id == user.id, InvestigationCase.id.in_(member_cases)))
    return q


def _latest_jobs(db: Session, case_ids: list[str]) -> dict[str, AnalysisJob]:
    if not case_ids:
        return {}
    sub = select(AnalysisJob.case_id, func.max(AnalysisJob.created_at).label("mx")).where(AnalysisJob.case_id.in_(case_ids)).group_by(AnalysisJob.case_id).subquery()
    rows = db.scalars(select(AnalysisJob).join(sub, (AnalysisJob.case_id == sub.c.case_id) & (AnalysisJob.created_at == sub.c.mx)))
    return {j.case_id: j for j in rows}


def _case_out(c: InvestigationCase, count: int, job: AnalysisJob | None) -> CaseOut:
    out = CaseOut.model_validate(c)
    out.evidence_count = count
    if job:
        out.latest_job_status = job.status
        out.overall_concern = (job.summary or {}).get("overall")
    return out


@router.get("", response_model=Page)
def list_cases(q: str | None = Query(None, max_length=100), status_: CaseStatus | None = Query(None, alias="status"),
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Page:
    base = _visible_cases(user)
    if q:
        like = f"%{q.lower()}%"
        base = base.where(or_(func.lower(InvestigationCase.title).like(like), func.lower(InvestigationCase.reference).like(like)))
    if status_:
        base = base.where(InvestigationCase.status == status_)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    cases = list(db.scalars(base.order_by(InvestigationCase.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)))
    ids = [c.id for c in cases]
    counts = dict(db.execute(select(EvidenceFile.case_id, func.count()).where(EvidenceFile.case_id.in_(ids), EvidenceFile.deleted_at.is_(None))
                             .group_by(EvidenceFile.case_id)).all()) if ids else {}
    jobs = _latest_jobs(db, ids)
    return Page(items=[_case_out(c, counts.get(c.id, 0), jobs.get(c.id)) for c in cases], total=total, page=page, page_size=page_size)


@router.post("", response_model=CaseOut, status_code=201)
def create_case(body: CaseCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CaseOut:
    if user.role == Role.reviewer:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Reviewers cannot create cases")
    ref = f"TL-{datetime.utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"
    case = InvestigationCase(reference=ref, title=body.title.strip(), description=body.description, context_statement=body.context_statement,
                             priority=body.priority, owner_id=user.id)
    db.add(case)
    db.flush()
    audit.record(db, "case.created", user_id=user.id, case_id=case.id, detail={"reference": ref}, ip=client_ip(request))
    return _case_out(case, 0, None)


@router.get("/dashboard/stats")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    visible = _visible_cases(user).subquery()
    ids = select(visible.c.id)
    by_status = dict(db.execute(select(visible.c.status, func.count()).group_by(visible.c.status)).all())
    by_media = dict(db.execute(select(EvidenceFile.media_type, func.count()).where(EvidenceFile.case_id.in_(ids), EvidenceFile.deleted_at.is_(None))
                               .group_by(EvidenceFile.media_type)).all())
    sev = dict(db.execute(select(ManipulationIndicator.severity, func.count()).join(AnalysisJob, AnalysisJob.id == ManipulationIndicator.job_id)
                          .where(AnalysisJob.case_id.in_(ids)).group_by(ManipulationIndicator.severity)).all())
    jobs = dict(db.execute(select(AnalysisJob.status, func.count()).where(AnalysisJob.case_id.in_(ids)).group_by(AnalysisJob.status)).all())
    recent = db.scalars(select(AuditLog).where(AuditLog.case_id.in_(ids)).order_by(AuditLog.id.desc()).limit(12)).all()
    k = lambda d: {getattr(a, "value", a): b for a, b in d.items()}  # noqa: E731
    return {"cases_by_status": k(by_status), "evidence_by_type": k(by_media), "indicators_by_severity": k(sev), "jobs_by_status": k(jobs),
            "recent_activity": [{"action": a.action, "case_id": a.case_id, "created_at": a.created_at, "detail": a.detail} for a in recent]}


@router.get("/{case_id}", response_model=CaseOut)
def get_case(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CaseOut:
    c = get_case_for_user(db, case_id, user)
    count = db.scalar(select(func.count()).where(EvidenceFile.case_id == c.id, EvidenceFile.deleted_at.is_(None))) or 0
    return _case_out(c, count, _latest_jobs(db, [c.id]).get(c.id))


@router.patch("/{case_id}", response_model=CaseOut)
def update_case(case_id: str, body: CaseUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CaseOut:
    c = get_case_for_user(db, case_id, user, write=True)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(c, k, v)
    c.updated_at = utcnow()
    audit.record(db, "case.updated", user_id=user.id, case_id=c.id, detail={"fields": sorted(changes)}, ip=client_ip(request))
    return get_case(case_id, user, db)


@router.delete("/{case_id}", status_code=204)
def delete_case(case_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    c = get_case_for_user(db, case_id, user, write=True)
    if user.role != Role.admin and c.owner_id != user.id:
        raise HTTPException(403, "Only the owner or an administrator can delete a case")
    c.deleted_at = utcnow()  # soft delete; purge happens via retention job (scripts/retention.py)
    audit.record(db, "case.deleted", user_id=user.id, case_id=c.id, ip=client_ip(request))


# ----- timeline -----
@router.get("/{case_id}/timeline")
def timeline(case_id: str, limit: int = Query(200, ge=1, le=1000), user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    c = get_case_for_user(db, case_id, user)
    users = {u.id: u.full_name for u in db.scalars(select(User))}
    events = [{"type": "audit", "action": a.action, "at": a.created_at, "actor": users.get(a.user_id), "detail": a.detail, "evidence_id": a.evidence_id}
              for a in db.scalars(select(AuditLog).where(AuditLog.case_id == c.id).order_by(AuditLog.id.desc()).limit(limit))]
    return sorted(events, key=lambda e: e["at"])

"""Analysis jobs, results, forensic reports and audit endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import audit, remote_storage
from ..config import get_settings
from ..database import get_db
from ..models import (AgentResult, AnalysisJob, AuditLog, CrossModalFinding, EvidenceFile, ForensicReport, JobStatus,
                      ManipulationIndicator, Role, User)
from ..ratelimit import client_ip, limiter
from ..schemas import AgentResultOut, AuditOut, FindingOut, IndicatorOut, JobDetail, JobOut, Page, ReportOut
from ..security import get_case_for_user, get_current_user, require_roles
from ..services import jobs
from ..services.orchestrator import artifact_dir_for
from ..services.reporting import build_pdf
from ..storage import safe_join, sha256_file

router = APIRouter(prefix="/api", tags=["analysis"], dependencies=[Depends(limiter("default"))])


@router.post("/cases/{case_id}/analyze", response_model=JobOut, status_code=202)
def start_analysis(case_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AnalysisJob:
    c = get_case_for_user(db, case_id, user, write=True)
    if not db.scalar(select(func.count()).where(EvidenceFile.case_id == c.id, EvidenceFile.deleted_at.is_(None))) and not c.context_statement.strip():
        raise HTTPException(422, "Add evidence before starting an analysis")
    active = db.scalar(select(AnalysisJob).where(AnalysisJob.case_id == c.id, AnalysisJob.status.in_([JobStatus.queued, JobStatus.running])))
    if active:
        raise HTTPException(409, "An analysis is already in progress for this case")
    job = AnalysisJob(case_id=c.id, requested_by=user.id)
    db.add(job)
    db.flush()
    audit.record(db, "analysis.started", user_id=user.id, case_id=c.id, detail={"job_id": job.id}, ip=client_ip(request))
    jobs.enqueue(job.id)
    db.refresh(job)
    return job


@router.get("/cases/{case_id}/jobs", response_model=list[JobOut])
def list_jobs(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[AnalysisJob]:
    c = get_case_for_user(db, case_id, user)
    return list(db.scalars(select(AnalysisJob).where(AnalysisJob.case_id == c.id).order_by(AnalysisJob.created_at.desc())))


def _job_for_user(db: Session, job_id: str, user: User) -> AnalysisJob:
    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    get_case_for_user(db, job.case_id, user)
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_status(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AnalysisJob:
    return _job_for_user(db, job_id, user)


def _detail(db: Session, job: AnalysisJob) -> JobDetail:
    results = db.scalars(select(AgentResult).where(AgentResult.job_id == job.id).order_by(AgentResult.id)).all()
    findings = db.scalars(select(CrossModalFinding).where(CrossModalFinding.job_id == job.id)).all()
    inds = db.scalars(select(ManipulationIndicator).where(ManipulationIndicator.job_id == job.id).order_by(ManipulationIndicator.evidence_id, ManipulationIndicator.id)).all()
    return JobDetail(**JobOut.model_validate(job).model_dump(), agent_results=[AgentResultOut.model_validate(r) for r in results],
                     findings=[FindingOut.model_validate(f) for f in findings], indicators=[IndicatorOut.model_validate(i) for i in inds])


@router.get("/jobs/{job_id}/results", response_model=JobDetail)
def job_results(job_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JobDetail:
    return _detail(db, _job_for_user(db, job_id, user))


@router.get("/cases/{case_id}/results/latest", response_model=JobDetail | None)
def latest_results(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JobDetail | None:
    c = get_case_for_user(db, case_id, user)
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.case_id == c.id, AnalysisJob.status.in_([JobStatus.completed, JobStatus.partial]))
                    .order_by(AnalysisJob.created_at.desc()).limit(1))
    return _detail(db, job) if job else None


# ---------------- Reports ----------------
def _report_context(db: Session, case, user: User) -> tuple[dict, AnalysisJob | None]:
    evidence = db.scalars(select(EvidenceFile).where(EvidenceFile.case_id == case.id, EvidenceFile.deleted_at.is_(None))
                          .options(selectinload(EvidenceFile.hashes)).order_by(EvidenceFile.created_at)).all()
    labels = {e.id: e.label for e in evidence}
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.case_id == case.id, AnalysisJob.status.in_([JobStatus.completed, JobStatus.partial]))
                    .order_by(AnalysisJob.created_at.desc()).limit(1))
    ev_rows, images = [], {}
    for e in evidence:
        up = next(h for h in e.hashes if h.purpose == "upload")
        ver = sorted([h for h in e.hashes if h.purpose == "verification"], key=lambda h: h.computed_at)
        ev_rows.append({"id": e.id, "label": e.label, "original_filename": e.original_filename, "media_type": e.media_type.value, "size_bytes": e.size_bytes,
                        "sha256": up.digest, "created_at": e.created_at.isoformat(),
                        "integrity": ("verified intact" if ver[-1].matches_original else "MISMATCH") + f" {ver[-1].computed_at:%Y-%m-%d %H:%M}" if ver else "not re-verified"})
        hm = artifact_dir_for(e) / "heatmap.jpg"
        if hm.exists() and job and db.scalar(select(func.count()).where(ManipulationIndicator.job_id == job.id, ManipulationIndicator.evidence_id == e.id,
                                                                         ManipulationIndicator.category == "region")):
            images[e.id] = str(hm)
    results, inds, findings = [], [], []
    if job:
        d = _detail(db, job)
        results = [{**r.model_dump(mode="json"), "evidence_label": labels.get(r.evidence_id)} for r in d.agent_results]
        inds = [{**i.model_dump(mode="json"), "evidence_label": labels.get(i.evidence_id, "?")} for i in d.indicators]
        findings = [f.model_dump(mode="json") for f in d.findings]
    history = db.scalars(select(AnalysisJob).where(AnalysisJob.case_id == case.id).order_by(AnalysisJob.created_at)).all()
    audit_rows = db.scalars(select(AuditLog).where(AuditLog.case_id == case.id).order_by(AuditLog.id)).all()
    ctx = {
        "case": {"id": case.id, "reference": case.reference, "title": case.title, "status": case.status.value, "priority": case.priority,
                 "description": case.description, "context_statement": case.context_statement},
        "generated_by": f"{user.full_name} ({user.role.value})",
        "evidence": ev_rows,
        "job": {"id": job.id, "status": job.status.value, "finished_at": job.finished_at.isoformat() if job.finished_at else None, "summary": job.summary} if job else None,
        "agent_results": results, "indicators": inds, "findings": findings,
        "history": [{"id": h.id, "status": h.status.value, "started_at": h.started_at.isoformat()[:19] if h.started_at else None,
                     "finished_at": h.finished_at.isoformat()[:19] if h.finished_at else None} for h in history],
        "audit": [{"created_at": a.created_at.isoformat(), "action": a.action, "detail": a.detail, "entry_hash": a.entry_hash} for a in audit_rows],
        "images": images,
    }
    return ctx, job


@router.post("/cases/{case_id}/reports", response_model=ReportOut, status_code=201)
def generate_report(case_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ForensicReport:
    c = get_case_for_user(db, case_id, user)
    ctx, job = _report_context(db, c, user)
    s = get_settings()
    s.ensure_dirs()
    rep = ForensicReport(case_id=c.id, job_id=job.id if job else None, generated_by=user.id, file_path="", sha256="")
    db.add(rep)
    db.flush()
    name = f"{c.reference}-{rep.id[:8]}.pdf"
    dest = safe_join(Path(s.report_dir), name)
    build_pdf(dest, ctx)
    rep.file_path, rep.sha256 = name, sha256_file(dest)
    remote_storage.push(f"reports/{name}", dest)
    rep.content = {k: v for k, v in ctx.items() if k not in ("images", "audit")} | {"audit_entries": len(ctx["audit"])}
    audit.record(db, "report.generated", user_id=user.id, case_id=c.id, detail={"report_id": rep.id, "sha256": rep.sha256}, ip=client_ip(request))
    return rep


@router.get("/cases/{case_id}/reports", response_model=list[ReportOut])
def list_reports(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[ForensicReport]:
    c = get_case_for_user(db, case_id, user)
    return list(db.scalars(select(ForensicReport).where(ForensicReport.case_id == c.id).order_by(ForensicReport.created_at.desc())))


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    rep = db.get(ForensicReport, report_id)
    if not rep:
        raise HTTPException(404, "Report not found")
    get_case_for_user(db, rep.case_id, user)
    path = safe_join(Path(get_settings().report_dir), rep.file_path)
    if not path.exists() and not remote_storage.pull(f"reports/{rep.file_path}", path):
        raise HTTPException(410, "Report file no longer available")
    audit.record(db, "report.downloaded", user_id=user.id, case_id=rep.case_id, detail={"report_id": rep.id}, ip=client_ip(request))
    return FileResponse(path, media_type="application/pdf", filename=rep.file_path, headers={"X-Report-SHA256": rep.sha256})


# ---------------- Audit ----------------
@router.get("/cases/{case_id}/audit", response_model=Page)
def case_audit(case_id: str, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Page:
    c = get_case_for_user(db, case_id, user)
    base = select(AuditLog).where(AuditLog.case_id == c.id)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[AuditOut.model_validate(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/audit/verify")
def verify_audit_chain(_: User = Depends(require_roles(Role.admin)), db: Session = Depends(get_db)) -> dict:
    return audit.verify_chain(db)


@router.get("/history", response_model=Page)
def analysis_history(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), status_: JobStatus | None = Query(None, alias="status"),
                     user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Page:
    from .cases import _visible_cases

    ids = select(_visible_cases(user).subquery().c.id)
    base = select(AnalysisJob).where(AnalysisJob.case_id.in_(ids))
    if status_:
        base = base.where(AnalysisJob.status == status_)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = db.scalars(base.options(selectinload(AnalysisJob.case)).order_by(AnalysisJob.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    items = [{**JobOut.model_validate(j).model_dump(mode="json"), "case_reference": j.case.reference, "case_title": j.case.title} for j in rows]
    return Page(items=items, total=total, page=page, page_size=page_size)

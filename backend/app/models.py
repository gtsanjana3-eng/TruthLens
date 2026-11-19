"""Relational schema for TruthLens AI.

Uploaded media is stored on disk (content-addressed, read-only after write);
the database only holds references, hashes and analysis results.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Role(str, enum.Enum):
    admin = "admin"
    investigator = "investigator"
    reviewer = "reviewer"


class CaseStatus(str, enum.Enum):
    open = "open"
    in_review = "in_review"
    closed = "closed"


class MediaType(str, enum.Enum):
    image = "image"
    video = "video"
    audio = "audio"
    text = "text"
    other = "other"  # any other file: preserved, hashed and metadata-checked, not content-analysed


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    partial = "partial"  # some agents failed / unavailable
    failed = "failed"


class AgentStatus(str, enum.Enum):
    completed = "completed"
    unavailable = "unavailable"
    failed = "failed"
    skipped = "skipped"


class Severity(str, enum.Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"


class RelationType(str, enum.Enum):
    supports = "supports"
    conflicts = "conflicts"
    uncertain = "uncertain"
    derived_from = "derived_from"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=20), default=Role.investigator, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    cases: Mapped[list["InvestigationCase"]] = relationship(back_populates="owner")


class InvestigationCase(TimestampMixin, Base):
    __tablename__ = "investigation_cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    reference: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    context_statement: Mapped[str] = mapped_column(Text, default="", nullable=False)  # claimed context of the media
    status: Mapped[CaseStatus] = mapped_column(Enum(CaseStatus, native_enum=False, length=20), default=CaseStatus.open, nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(10), default="medium", nullable=False)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    owner: Mapped[User] = relationship(back_populates="cases")
    members: Mapped[list["CaseMember"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    evidence: Mapped[list["EvidenceFile"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    jobs: Mapped[list["AnalysisJob"]] = relationship(back_populates="case", cascade="all, delete-orphan", order_by="AnalysisJob.created_at")
    notes: Mapped[list["InvestigationNote"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    reports: Mapped[list["ForensicReport"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class CaseMember(Base):
    """Users other than the owner who may access a case (e.g. assigned reviewers)."""
    __tablename__ = "case_members"
    __table_args__ = (UniqueConstraint("case_id", "user_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    case: Mapped[InvestigationCase] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class EvidenceFile(TimestampMixin, Base):
    __tablename__ = "evidence_files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)  # E1, E2 ... evidence reference
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, native_enum=False, length=10), index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(512), nullable=False)  # relative to storage dir
    preview_path: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    file_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    upload_ip: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    case: Mapped[InvestigationCase] = relationship(back_populates="evidence")
    hashes: Mapped[list["FileHash"]] = relationship(back_populates="evidence", cascade="all, delete-orphan")
    indicators: Mapped[list["ManipulationIndicator"]] = relationship(back_populates="evidence", cascade="all, delete-orphan")


class FileHash(Base):
    """Integrity record. A hash is recorded at upload and re-verified on demand."""
    __tablename__ = "file_hashes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence_files.id", ondelete="CASCADE"), index=True, nullable=False)
    algorithm: Mapped[str] = mapped_column(String(20), default="sha256", nullable=False)
    digest: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    purpose: Mapped[str] = mapped_column(String(20), default="upload", nullable=False)  # upload | verification
    matches_original: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    evidence: Mapped[EvidenceFile] = relationship(back_populates="hashes")


class AnalysisJob(TimestampMixin, Base):
    __tablename__ = "analysis_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True, nullable=False)
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, native_enum=False, length=20), default=JobStatus.queued, index=True, nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stage: Mapped[str] = mapped_column(String(120), default="queued", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # orchestrator synthesis

    case: Mapped[InvestigationCase] = relationship(back_populates="jobs")
    agent_results: Mapped[list["AgentResult"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    findings: Mapped[list["CrossModalFinding"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class AgentResult(Base):
    __tablename__ = "agent_results"
    __table_args__ = (Index("ix_agent_results_job_agent", "job_id", "agent_name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_files.id", ondelete="CASCADE"), index=True)
    agent_name: Mapped[str] = mapped_column(String(60), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus, native_enum=False, length=20), nullable=False)
    suspicion_score: Mapped[float | None] = mapped_column(Float)  # 0..1 heuristic indicator, NOT a probability of fakery
    confidence: Mapped[float | None] = mapped_column(Float)  # agent's confidence in its own signal quality
    observations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)  # measured facts
    interpretation: Mapped[str] = mapped_column(Text, default="", nullable=False)  # model interpretation
    signals: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    limitations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    errors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # heatmap paths, frame lists...
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    job: Mapped[AnalysisJob] = relationship(back_populates="agent_results")


class ManipulationIndicator(Base):
    __tablename__ = "manipulation_indicators"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("evidence_files.id", ondelete="CASCADE"), index=True, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(60), nullable=False)
    category: Mapped[str] = mapped_column(String(40), index=True, nullable=False)  # region|frame|segment|metadata|text
    indicator_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[Severity] = mapped_column(Enum(Severity, native_enum=False, length=10), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # bbox / frame / t_start,t_end / field
    score: Mapped[float | None] = mapped_column(Float)
    alternative_explanations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommended_checks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    evidence: Mapped[EvidenceFile] = relationship(back_populates="indicators")


class CrossModalFinding(Base):
    __tablename__ = "cross_modal_findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True, nullable=False)
    finding_type: Mapped[str] = mapped_column(String(60), nullable=False)
    effect: Mapped[str] = mapped_column(String(20), nullable=False)  # strengthens | weakens | neutral
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    job: Mapped[AnalysisJob] = relationship(back_populates="findings")


class EvidenceRelationship(Base):
    __tablename__ = "evidence_relationships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True, nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)  # evidence id or "finding:<id>"
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relation: Mapped[RelationType] = mapped_column(Enum(RelationType, native_enum=False, length=20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, default="", nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class InvestigationNote(TimestampMixin, Base):
    __tablename__ = "investigation_notes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True, nullable=False)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    evidence_id: Mapped[str | None] = mapped_column(ForeignKey("evidence_files.id", ondelete="SET NULL"))
    indicator_id: Mapped[int | None] = mapped_column(ForeignKey("manipulation_indicators.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(30))  # reviewer assessment: confirmed|dismissed|needs_more_evidence
    case: Mapped[InvestigationCase] = relationship(back_populates="notes")
    author: Mapped[User] = relationship()


class ForensicReport(Base):
    __tablename__ = "forensic_reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    case_id: Mapped[str] = mapped_column(ForeignKey("investigation_cases.id", ondelete="CASCADE"), index=True, nullable=False)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="SET NULL"))
    generated_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # structured report body
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    case: Mapped[InvestigationCase] = relationship(back_populates="reports")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_case_time", "case_id", "created_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    evidence_id: Mapped[str | None] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str | None] = mapped_column(String(64))  # hash chain for tamper evidence
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

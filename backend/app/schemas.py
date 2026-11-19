"""Pydantic request/response schemas (strict input validation)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import CaseStatus, JobStatus, MediaType, Role


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth ----------
def password_policy(v: str) -> str:
    if not (re.search(r"[A-Z]", v) and re.search(r"[a-z]", v) and re.search(r"\d", v)):
        raise ValueError("Password must contain upper-case, lower-case letters and a digit")
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password too long")
    return v


class RegisterIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=10, max_length=72)

    @field_validator("password")
    @classmethod
    def strong(cls, v: str) -> str:
        return password_policy(v)

    @field_validator("full_name")
    @classmethod
    def clean_name(cls, v: str) -> str:
        v = v.strip()
        if re.search(r"[<>]", v):
            raise ValueError("Name contains invalid characters")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(ORM):
    id: str
    email: str
    full_name: str
    role: Role
    preferences: dict
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=2, max_length=120)
    preferences: dict[str, Any] | None = None

    @field_validator("preferences")
    @classmethod
    def small(cls, v):
        if v is not None and len(str(v)) > 4000:
            raise ValueError("Preferences too large")
        return v


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=10, max_length=72)

    @field_validator("new_password")
    @classmethod
    def strong(cls, v: str) -> str:
        return password_policy(v)


class RoleUpdate(BaseModel):
    role: Role


# ---------- Cases ----------
class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field("", max_length=5000)
    context_statement: str = Field("", max_length=10000)
    priority: Literal["low", "medium", "high", "critical"] = "medium"


class CaseUpdate(BaseModel):
    title: str | None = Field(None, min_length=3, max_length=200)
    description: str | None = Field(None, max_length=5000)
    context_statement: str | None = Field(None, max_length=10000)
    priority: Literal["low", "medium", "high", "critical"] | None = None
    status: CaseStatus | None = None


class CaseOut(ORM):
    id: str
    reference: str
    title: str
    description: str
    context_statement: str
    status: CaseStatus
    priority: str
    owner_id: str
    created_at: datetime
    updated_at: datetime
    evidence_count: int = 0
    latest_job_status: JobStatus | None = None
    overall_concern: str | None = None


class Page(BaseModel):
    items: list[Any]
    total: int
    page: int
    page_size: int


class MemberIn(BaseModel):
    email: EmailStr
    can_edit: bool = False


# ---------- Evidence ----------
class HashOut(ORM):
    algorithm: str
    digest: str
    purpose: str
    matches_original: bool
    computed_at: datetime


class EvidenceOut(ORM):
    id: str
    case_id: str
    label: str
    media_type: MediaType
    original_filename: str
    mime_type: str
    size_bytes: int
    description: str
    file_metadata: dict
    uploaded_by: str
    created_at: datetime
    sha256: str | None = None
    preview_available: bool = False


class EvidenceUpdate(BaseModel):
    description: str = Field(max_length=2000)


# ---------- Analysis ----------
class IndicatorOut(ORM):
    id: int
    evidence_id: str
    agent_name: str
    category: str
    indicator_type: str
    severity: str
    description: str
    location: dict
    score: float | None
    alternative_explanations: list
    recommended_checks: list


class AgentResultOut(ORM):
    id: int
    evidence_id: str | None
    agent_name: str
    agent_version: str
    status: str
    suspicion_score: float | None
    confidence: float | None
    observations: list
    interpretation: str
    signals: list
    limitations: list
    errors: list
    artifacts: dict
    duration_ms: int
    created_at: datetime


class FindingOut(ORM):
    id: int
    finding_type: str
    effect: str
    description: str
    evidence_ids: list
    details: dict
    confidence: float | None


class JobOut(ORM):
    id: str
    case_id: str
    status: JobStatus
    progress: float
    stage: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: dict


class JobDetail(JobOut):
    agent_results: list[AgentResultOut]
    findings: list[FindingOut]
    indicators: list[IndicatorOut]


# ---------- Reports ----------
class ReportOut(ORM):
    id: str
    case_id: str
    job_id: str | None
    sha256: str
    created_at: datetime
    content: dict


class AuditOut(ORM):
    id: int
    user_id: str | None
    case_id: str | None
    evidence_id: str | None
    action: str
    detail: dict
    ip_address: str | None
    entry_hash: str
    created_at: datetime

"""Evidence upload, retrieval, artifacts and the Evidence Integrity Vault."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..database import get_db
from ..models import EvidenceFile, FileHash, MediaType, Role, User, utcnow
from ..ratelimit import client_ip, limiter
from ..schemas import EvidenceOut, EvidenceUpdate
from ..security import get_case_for_user, get_current_user
from ..services.isolation import run_isolated
from ..services.media_probe import inspect_media, make_image_preview
from ..storage import ValidationError, evidence_path, safe_display_name, safe_join, secure_delete, sha256_file, store_upload

router = APIRouter(prefix="/api", tags=["evidence"], dependencies=[Depends(limiter("default"))])
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9_\-/]{1,80}\.(jpg|png|wav)$")


def _out(e: EvidenceFile) -> EvidenceOut:
    o = EvidenceOut.model_validate(e)
    upload_hash = next((h for h in e.hashes if h.purpose == "upload"), None)
    o.sha256 = upload_hash.digest if upload_hash else None
    o.preview_available = bool(e.preview_path)
    return o


STILL_IMAGE_CODECS = {"mjpeg", "png", "bmp", "gif", "tiff", "webp", "jpeg2000", "jpegls", "pam", "pbm", "pgm", "ppm", "targa", "ico", "sgi", "pcx", "qdraw"}


def _real_stream(info: dict) -> bool:
    """For files with no recognised signature, only trust audio or a moving-picture codec lasting > 0.1 s."""
    if info.get("has_audio") and info.get("audio_codec"):
        return True
    codec = (info.get("video_codec") or "").lower()
    return bool(info.get("has_video") and codec and codec not in STILL_IMAGE_CODECS and (info.get("duration_s") or 0) > 0.1)


def _discard_unreferenced(db: Session, relative_path: str) -> None:
    """Remove a just-stored file that was rejected, unless another evidence record already uses it."""
    if not db.scalar(select(func.count()).where(EvidenceFile.stored_path == relative_path)):
        try:
            secure_delete(evidence_path(relative_path))
        except OSError:
            pass


def _evidence_for_user(db: Session, evidence_id: str, user: User, write: bool = False) -> EvidenceFile:
    e = db.get(EvidenceFile, evidence_id)
    if not e or e.deleted_at is not None:
        raise HTTPException(404, "Evidence not found")
    get_case_for_user(db, e.case_id, user, write=write)
    return e


@router.post("/cases/{case_id}/evidence", response_model=EvidenceOut, status_code=201, dependencies=[Depends(limiter("upload"))])
def upload(case_id: str, request: Request, file: UploadFile = File(...), description: str = Form("", max_length=2000),
           user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> EvidenceOut:
    case = get_case_for_user(db, case_id, user, write=True)
    if user.role == Role.reviewer and case.owner_id != user.id:
        raise HTTPException(403, "Reviewers cannot add evidence")
    s = get_settings()
    try:
        stored = store_upload(file.file, file.filename or "", file.content_type)
    except ValidationError as e:
        audit.record(db, "evidence.rejected", user_id=user.id, case_id=case.id, detail={"reason": str(e), "filename": safe_display_name(file.filename or "")}, ip=client_ip(request))
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e))
    path = evidence_path(stored.relative_path)
    meta: dict = {"detected_content": stored.detected}
    if stored.notes:
        meta["content_notes"] = stored.notes
    media, mime = stored.media, stored.mime
    # Stream probe (decodability, duration limits, real stream types) inside an isolated worker with timeout.
    # Unknown binaries are probed too – they may be audio/video in an uncommon container.
    if media in (MediaType.video, MediaType.audio, MediaType.other):
        try:
            info = run_isolated(inspect_media, str(path), "video" if media == MediaType.other else media.value, timeout=60)
        except Exception:  # noqa: BLE001
            info = None
        if media == MediaType.other and info and not _real_stream(info):
            info = None  # FFmpeg guesses from the file name (e.g. "x.jpg" -> 1-frame mjpeg); not real media
        if media == MediaType.other and info and (info.get("has_video") or info.get("has_audio")):
            media = MediaType.video if info.get("has_video") else MediaType.audio
            mime = f"{media.value}/x-unknown"
            meta["detected_content"] = f"{media.value} in an uncommon container"
        if not info or not (info.get("has_video") or info.get("has_audio")):
            if media != MediaType.other:  # accepted and preserved, but it cannot be played/analysed as media
                meta.setdefault("content_notes", []).append(f"Looks like {stored.detected} but no decodable audio/video stream was found "
                                                            "(corrupt, DRM-protected or unsupported codec) – kept as an unanalysed file.")
                media, mime = MediaType.other, "application/octet-stream"
            info = None
        if info and media == MediaType.video and not info.get("has_video") and info.get("has_audio"):
            media = MediaType.audio  # audio-only container, e.g. a browser .webm voice recording
            mime = "audio/" + stored.mime.split("/", 1)[1]
            meta["reclassified"] = "video container without a video stream – analysed as audio"
        dur = info.get("duration_s") if info else None
        limit = s.max_video_seconds if media == MediaType.video else s.max_audio_seconds
        if dur is not None:
            meta["duration_s"] = round(dur, 3)
            if dur > limit * 1.5:  # hard reject far beyond the analysis window
                _discard_unreferenced(db, stored.relative_path)
                raise HTTPException(422, f"{media.value.capitalize()} duration {dur:.0f}s exceeds the {limit}s limit")
        if info:
            meta.update({k: info[k] for k in ("video_codec", "audio_codec", "fps") if info.get(k)})
    n = (db.scalar(select(func.count()).where(EvidenceFile.case_id == case.id)) or 0) + 1
    ev = EvidenceFile(case_id=case.id, label=f"E{n}", media_type=media, original_filename=safe_display_name(file.filename or "upload"),
                      stored_path=stored.relative_path, mime_type=mime, size_bytes=stored.size, description=description.strip(),
                      file_metadata=meta, uploaded_by=user.id, upload_ip=client_ip(request))
    db.add(ev)
    db.flush()
    db.add(FileHash(evidence_id=ev.id, algorithm="sha256", digest=stored.sha256, purpose="upload"))
    if media == MediaType.image:
        prev = Path(s.preview_dir) / "thumbs" / f"{stored.sha256}.jpg"
        try:
            if prev.exists() or run_isolated(make_image_preview, str(path), str(prev), timeout=60):
                ev.preview_path = prev.name
        except Exception:  # noqa: BLE001
            ev.preview_path = None
    case.updated_at = utcnow()
    audit.record(db, "evidence.uploaded", user_id=user.id, case_id=case.id, evidence_id=ev.id,
                 detail={"label": ev.label, "sha256": stored.sha256, "size": stored.size, "type": media.value, "detected": meta.get("detected_content"), "filename": ev.original_filename},
                 ip=client_ip(request))
    db.refresh(ev)
    return _out(ev)


@router.get("/cases/{case_id}/evidence", response_model=list[EvidenceOut])
def list_evidence(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[EvidenceOut]:
    c = get_case_for_user(db, case_id, user)
    return [_out(e) for e in db.scalars(select(EvidenceFile).where(EvidenceFile.case_id == c.id, EvidenceFile.deleted_at.is_(None)).order_by(EvidenceFile.created_at))]


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
def get_evidence(evidence_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> EvidenceOut:
    return _out(_evidence_for_user(db, evidence_id, user))


@router.patch("/evidence/{evidence_id}", response_model=EvidenceOut)
def update_evidence(evidence_id: str, body: EvidenceUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> EvidenceOut:
    e = _evidence_for_user(db, evidence_id, user, write=True)
    e.description = body.description.strip()
    audit.record(db, "evidence.described", user_id=user.id, case_id=e.case_id, evidence_id=e.id)
    return _out(e)


@router.get("/evidence/{evidence_id}/file")
def download_original(evidence_id: str, request: Request, inline: bool = False, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    e = _evidence_for_user(db, evidence_id, user)
    audit.record(db, "evidence.accessed", user_id=user.id, case_id=e.case_id, evidence_id=e.id, detail={"inline": inline}, ip=client_ip(request))
    return FileResponse(evidence_path(e.stored_path), media_type=e.mime_type, filename=e.original_filename,
                        content_disposition_type="inline" if inline and e.media_type in (MediaType.image, MediaType.video, MediaType.audio) else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.get("/evidence/{evidence_id}/preview")
def preview(evidence_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    e = _evidence_for_user(db, evidence_id, user)
    if not e.preview_path:
        raise HTTPException(404, "No preview available")
    return FileResponse(safe_join(Path(get_settings().preview_dir) / "thumbs", e.preview_path), media_type="image/jpeg")


@router.get("/evidence/{evidence_id}/artifacts/{name:path}")
def artifact(evidence_id: str, name: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> FileResponse:
    """Serve analysis artifacts (heatmaps, frame thumbnails, spectrograms) – derived files, never originals."""
    e = _evidence_for_user(db, evidence_id, user)
    if not ARTIFACT_NAME.match(name) or ".." in name:
        raise HTTPException(400, "Invalid artifact name")
    base = Path(get_settings().preview_dir) / "artifacts" / Path(e.stored_path).stem
    try:
        p = safe_join(base, name)
    except ValidationError:
        raise HTTPException(400, "Invalid artifact name")
    if not p.is_file():
        raise HTTPException(404, "Artifact not found")
    mt = {"jpg": "image/jpeg", "png": "image/png", "wav": "audio/wav"}[p.suffix[1:]]
    return FileResponse(p, media_type=mt, headers={"Cache-Control": "private, max-age=3600"})


@router.post("/evidence/{evidence_id}/verify")
def verify(evidence_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Re-hash the stored original and compare with the upload hash (Integrity Vault)."""
    e = _evidence_for_user(db, evidence_id, user)
    original = next(h for h in e.hashes if h.purpose == "upload")
    path = evidence_path(e.stored_path)
    current = sha256_file(path) if path.exists() else "missing"
    ok = current == original.digest
    db.add(FileHash(evidence_id=e.id, algorithm="sha256", digest=current, purpose="verification", matches_original=ok))
    audit.record(db, "evidence.verified", user_id=user.id, case_id=e.case_id, evidence_id=e.id, detail={"match": ok}, ip=client_ip(request))
    return {"evidence_id": e.id, "original_sha256": original.digest, "current_sha256": current, "intact": ok}


@router.delete("/evidence/{evidence_id}", status_code=204)
def remove_evidence(evidence_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    e = _evidence_for_user(db, evidence_id, user, write=True)
    e.deleted_at = utcnow()  # soft-delete: original retained until the retention policy purges it
    audit.record(db, "evidence.removed", user_id=user.id, case_id=e.case_id, evidence_id=e.id, ip=client_ip(request))


@router.get("/cases/{case_id}/vault")
def vault(case_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    c = get_case_for_user(db, case_id, user)
    items = []
    for e in db.scalars(select(EvidenceFile).where(EvidenceFile.case_id == c.id).order_by(EvidenceFile.created_at)):
        items.append({"id": e.id, "label": e.label, "filename": e.original_filename, "media_type": e.media_type.value, "size_bytes": e.size_bytes,
                      "uploaded_at": e.created_at, "uploaded_by": e.uploaded_by, "upload_ip": e.upload_ip, "removed_at": e.deleted_at,
                      "hashes": [{"digest": h.digest, "purpose": h.purpose, "matches_original": h.matches_original, "computed_at": h.computed_at}
                                 for h in sorted(e.hashes, key=lambda h: h.computed_at)]})
    return {"case_id": c.id, "storage": "content-addressed, write-protected originals", "items": items}

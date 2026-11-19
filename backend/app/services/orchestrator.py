"""Multi-agent investigation orchestrator.

Coordinates the specialised agents for every evidence item in a case, persists each agent's
structured output (including failures and unavailable analyses), runs cross-modal correlation
and the review agent, and records relationships for the Evidence DNA Graph. Agent outputs are
stored individually so disagreements are never hidden by aggregation."""
from __future__ import annotations

import json
import re
import logging
import traceback
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import audit, remote_storage
from ..agents import audio_agent, image_agent, metadata_agent, ml_agent, sync_analyzer, text_agent, video_agent
from ..agents.base import AgentOutput
from ..agents.mediainfo import decode_to_wav, soundfile_can_read
from ..agents.crossmodal_agent import EvidenceBundle, correlate, dhash_image
from ..agents.review_agent import build_report_outline, review
from ..config import get_settings
from ..database import SessionLocal
from ..models import (AgentResult, AgentStatus, AnalysisJob, CrossModalFinding, EvidenceFile, EvidenceRelationship,
                      JobStatus, ManipulationIndicator, MediaType, RelationType, Severity, utcnow)
from ..storage import evidence_path
from .isolation import run_isolated

log = logging.getLogger(__name__)
LEAD_AGENT = "Lead Investigation Agent"


def artifact_dir_for(ev: EvidenceFile) -> Path:
    digest = Path(ev.stored_path).stem
    d = Path(get_settings().preview_dir) / "artifacts" / digest
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached(adir: Path, key: str, fn, *args, timeout: float | None = None) -> dict:
    """Cache agent output per (content hash, agent, version): identical evidence is not re-analysed."""
    # Agent names may contain characters like "/" – never let them form a path.
    cache = adir / (re.sub(r"[^A-Za-z0-9._@-]+", "_", key)[:150] + ".json")
    if cache.exists():
        try:
            return json.loads(cache.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    try:
        res = run_isolated(fn, *args, timeout=timeout)
    except Exception as e:  # noqa: BLE001 - isolation boundary: convert to a failed agent result
        name = key.split("@")[0]
        res = AgentOutput(name, key.split("@")[-1], status="failed", errors=[f"{type(e).__name__}: {str(e)[:200]}"]).to_dict()
        return res
    if res.get("status") == "completed":
        try:
            cache.write_text(json.dumps(res, default=str), "utf-8")
        except OSError as e:  # a cache miss next time is acceptable; failing the job is not
            log.warning("Could not write analysis cache %s: %s", cache.name, e)
    return res


def _decode_image_with_pillow(src: str, dest: str) -> bool:
    from PIL import Image, ImageSequence

    with Image.open(src) as im:
        frame = next(iter(ImageSequence.Iterator(im)))  # first frame of animated GIF/WebP
        frame.convert("RGB").save(dest, "PNG")
    return True


def _analysable_image(path: Path, adir: Path) -> Path:
    """OpenCV reads JPEG/PNG/WebP/TIFF/BMP. Anything else Pillow can open gets a lossless PNG analysis copy."""
    if cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_REDUCED_COLOR_8) is not None:
        return path
    dest = adir / "decoded.png"
    try:
        if dest.exists() or run_isolated(_decode_image_with_pillow, str(path), str(dest), timeout=120):
            return dest
    except Exception:  # noqa: BLE001 - the image agent will report the decode failure honestly
        pass
    return path


def _analysable_video(path: Path, adir: Path, timeout: int) -> Path:
    """If OpenCV cannot read frames (FLV, MPEG-TS, WMV variants…), transcode an MP4 analysis copy with the bundled FFmpeg."""
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    ok = cap.isOpened() and cap.read()[0]
    cap.release()
    if ok:
        return path
    dest = adir / "decoded.mp4"
    if dest.exists():
        return dest
    from ..agents.mediainfo import ffmpeg_exe
    import subprocess

    exe = ffmpeg_exe()
    if not exe:
        return path
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path), "-t", "900", "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", str(dest)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return path
    return dest if r.returncode == 0 and dest.exists() and dest.stat().st_size > 0 else path


def _brightness_and_hash(img_path: Path) -> tuple[float | None, int | None]:
    data = np.fromfile(str(img_path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_REDUCED_COLOR_4)
    if img is None:
        return None, None
    return float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()), dhash_image(img)


class Orchestrator:
    def __init__(self, db: Session, job: AnalysisJob):
        self.db = db
        self.job = job
        self.s = get_settings()

    def progress(self, value: float, stage: str) -> None:
        self.job.progress = round(min(1.0, value), 3)
        self.job.stage = stage[:120]
        self.db.commit()

    def _save(self, res: dict, evidence: EvidenceFile | None, label: str | None = None) -> AgentResult:
        name = res["agent_name"] + (f" ({label})" if label else "")
        ar = AgentResult(job_id=self.job.id, evidence_id=evidence.id if evidence else None, agent_name=name, agent_version=res["agent_version"],
                         status=AgentStatus(res["status"]), suspicion_score=res.get("suspicion_score"), confidence=res.get("confidence"),
                         observations=res.get("observations", []), interpretation=res.get("interpretation", ""), signals=res.get("signals", []),
                         limitations=res.get("limitations", []) + [f"{m['name']}: {m['status']} – {m['detail']}" for m in res.get("model_checks", []) if m["status"] != "completed"],
                         errors=res.get("errors", []), artifacts={**res.get("artifacts", {}), "model_checks": res.get("model_checks", [])},
                         duration_ms=res.get("duration_ms", 0))
        self.db.add(ar)
        if evidence:
            for ind in res.get("indicators", []):
                self.db.add(ManipulationIndicator(job_id=self.job.id, evidence_id=evidence.id, agent_name=name, category=ind["category"],
                                                  indicator_type=ind["indicator_type"], severity=Severity(ind["severity"]), description=ind["description"],
                                                  location=ind.get("location", {}), score=ind.get("score"),
                                                  alternative_explanations=ind.get("alternative_explanations", []), recommended_checks=ind.get("recommended_checks", [])))
        self.db.flush()
        return ar

    def _run_ai_model(self, ev: EvidenceFile, media: str, src, adir, text: str | None = None) -> dict:
        """Run the ONE validated model for this media type; report 'unavailable' (never a fabricated score) when it can't run."""
        s = self.s
        if not s.ml_enabled:
            return ml_agent.unavailable(ev.id, "AI models are disabled (ML_ENABLED=false).")
        ok, reason = ml_agent.dependencies_available()
        if not ok:
            return ml_agent.unavailable(ev.id, reason)
        cfg = ml_agent.config_from_settings(s)
        if not cfg.get(media):
            return ml_agent.unavailable(ev.id, f"No AI {media} model is configured (see README – model selection and validation).")
        model, rev = cfg[media]
        # Cache key: agent version + model revision + sampling + validation/decision policy -> never a stale verdict.
        key = f"{ml_agent.NAME}@{ml_agent.VERSION}-{media}-" + ml_agent.model_cache_key(model, rev[:10], str(s.ml_video_frames), ml_agent.policy_fingerprint())
        if media == "video":
            res = _cached(adir, key, ml_agent.analyze_video, str(src), ev.id, cfg, str(adir), timeout=s.ml_timeout_seconds)
        elif media == "image":
            res = _cached(adir, key, ml_agent.analyze_image, str(src), ev.id, cfg, timeout=s.ml_timeout_seconds)
        elif media == "audio":
            res = _cached(adir, key, ml_agent.analyze_audio, str(src), ev.id, cfg, s.max_audio_seconds, timeout=s.ml_timeout_seconds)
        else:
            res = run_isolated(ml_agent.analyze_text, text or "", ev.id, cfg, timeout=s.ml_timeout_seconds)
        res["evidence_id"] = ev.id
        return res

    def run(self) -> None:
        job, db = self.job, self.db
        case = job.case
        evidence = [e for e in case.evidence if e.deleted_at is None]
        job.status, job.started_at = JobStatus.running, utcnow()
        self.progress(0.02, "Preparing evidence")
        per_evidence: dict[str, dict[str, Any]] = {}
        bundles: list[EvidenceBundle] = []
        any_failed = False
        n = max(1, len(evidence))

        for k, ev in enumerate(evidence):
            base = 0.05 + 0.75 * k / n
            path = evidence_path(ev.stored_path)
            adir = artifact_dir_for(ev)
            outputs: list[dict] = []
            ai_src, ai_text = path, None
            bundle = EvidenceBundle(ev.id, ev.label, ev.media_type.value)
            self.progress(base, f"{ev.label}: metadata analysis")
            upload_meta = dict(ev.file_metadata or {})
            file_info = {"original_filename": ev.original_filename, "mime_type": ev.mime_type,
                         "detected_content": upload_meta.get("detected_content"), "content_notes": upload_meta.get("content_notes")}
            md_key = f"{metadata_agent.NAME}@{metadata_agent.VERSION}-{ev.media_type.value}-{len(file_info.get('content_notes') or [])}"
            md = _cached(adir, md_key, metadata_agent.analyze, str(path), ev.id, ev.media_type.value, file_info)
            md["evidence_id"] = ev.id
            self._save(md, ev)
            outputs.append(md)
            bundle.metadata = md.get("artifacts", {}).get("metadata", {})
            ev.file_metadata = {**upload_meta, **bundle.metadata}  # keep upload-time facts (duration, content notes)

            self.progress(base + 0.25 / n, f"{ev.label}: {ev.media_type.value} forensics")
            if ev.media_type == MediaType.image:
                path = ai_src = _analysable_image(path, adir)  # GIF/ICO/HEIC… -> lossless PNG analysis copy; original untouched
                res = _cached(adir, f"{image_agent.NAME}@{image_agent.VERSION}", image_agent.analyze, str(path), ev.id, str(adir))
                res["evidence_id"] = ev.id
                self._save(res, ev)
                outputs.append(res)
                bundle.brightness, bundle.dhash = _brightness_and_hash(path)
                bundle.pixel_indicators = len(res.get("indicators", []))
            elif ev.media_type == MediaType.video:
                path = ai_src = _analysable_video(path, adir, self.s.analysis_timeout_seconds)  # uncommon codecs -> MP4 analysis copy
                res = _cached(adir, f"{video_agent.NAME}@{video_agent.VERSION}", video_agent.analyze, str(path), ev.id, str(adir), self.s.max_video_seconds,
                              timeout=self.s.analysis_timeout_seconds * 2)
                res["evidence_id"] = ev.id
                self._save(res, ev)
                outputs.append(res)
                arts = res.get("artifacts", {})
                bundle.duration_s = arts.get("duration_s")
                bundle.pixel_indicators = len(res.get("indicators", []))
                frames = arts.get("frames", [])
                lum, hashes = [], []
                for f in frames:
                    if f.get("thumb"):
                        b, h = _brightness_and_hash(adir / f["thumb"])
                        if h is not None:
                            hashes.append((f["t"], h))
                            lum.append(b)
                bundle.frame_hashes = hashes
                bundle.brightness = float(np.mean(lum)) if lum else None
                if arts.get("audio_track"):
                    self.progress(base + 0.5 / n, f"{ev.label}: embedded audio + voice-face sync")
                    track = adir / arts["audio_track"]
                    ares = _cached(adir, f"{audio_agent.NAME}@{audio_agent.VERSION}-track", audio_agent.analyze, str(track), ev.id, str(adir / "track"), self.s.max_audio_seconds)
                    ares["evidence_id"] = ev.id
                    self._save(ares, ev, label="embedded track")
                    outputs.append(ares)
                    vres = self._run_ai_model(ev, "audio", track, adir / "track")
                    self._save({**vres, "agent_name": audio_agent.NAME}, ev, label="embedded track · AI voice model")
                    outputs.append(vres)
                    wf = ares.get("artifacts", {}).get("waveform")
                    if wf:
                        bundle.speech_envelope = (wf["t"], wf["rms"])
                    sres = run_isolated(sync_analyzer.analyze, frames, str(track), ev.id) if frames else None
                    if sres:
                        self._save(sres, ev, label="voice-face sync")
                        outputs.append(sres)
                        bundle.sync = sres
                else:
                    skipped = AgentOutput(sync_analyzer.NAME, sync_analyzer.VERSION, status="skipped", evidence_id=ev.id,
                                          interpretation="No decodable audio track in the video – synchronization analysis not applicable.").to_dict()
                    self._save(skipped, ev, label="voice-face sync")
                    outputs.append(skipped)
            elif ev.media_type == MediaType.audio:
                src, note = str(path), None
                # libsndfile reads WAV/FLAC/OGG-Vorbis/MP3. Other codecs (AAC/M4A, Opus, WMA, AMR, WebM) are decoded
                # by FFmpeg into a derived PCM WAV inside the artifact folder; the original stays untouched.
                if not run_isolated(soundfile_can_read, str(path), timeout=60):
                    decoded = adir / "decoded.wav"
                    if decoded.exists() or run_isolated(decode_to_wav, str(path), str(decoded), timeout=self.s.analysis_timeout_seconds):
                        src, note = str(decoded), "Analysed a PCM decode of the original (lossy codec); the original evidence file is unchanged."
                res = _cached(adir, f"{audio_agent.NAME}@{audio_agent.VERSION}", audio_agent.analyze, src, ev.id, str(adir), self.s.max_audio_seconds)
                if note and res.get("status") == "completed" and note not in res["limitations"]:
                    res["limitations"].append(note)
                res["evidence_id"] = ev.id
                self._save(res, ev)
                outputs.append(res)
                ai_src = src
                arts = res.get("artifacts", {})
                bundle.duration_s = arts.get("duration_s")
                if arts.get("waveform"):
                    bundle.speech_envelope = (arts["waveform"]["t"], arts["waveform"]["rms"])
            elif ev.media_type == MediaType.text:
                content = path.read_text("utf-8", errors="replace")
                ai_text = "\n".join(c["text"] for c in text_agent.parse_cues(content)) or content
                res = text_agent.analyze(content, ev.id, source=ev.label)
                self._save(res, ev)
                outputs.append(res)
                bundle.claims = res["artifacts"]["claims"]
                bundle.cues = res["artifacts"]["cues"]
            media = ev.media_type.value
            if media != "other":  # unrecognised files: preserved + metadata/SHA-256 only, no content model applies
                self.progress(base + 0.6 / n, f"{ev.label}: AI {media} model")
                owner = {"image": image_agent.NAME, "video": video_agent.NAME, "audio": audio_agent.NAME, "text": text_agent.NAME}[media]
                mres = self._run_ai_model(ev, media, ai_src if media in ("audio", "image", "video") else path, adir, text=ai_text if media == "text" else None)
                self._save({**mres, "agent_name": owner}, ev, label="AI model")
                outputs.append(mres)
            bundle.indicator_types = [i["indicator_type"] for o in outputs for i in o.get("indicators", [])]
            any_failed |= any(o["status"] == "failed" for o in outputs)
            per_evidence[ev.id] = {"label": ev.label, "outputs": outputs}
            remote_storage.push_dir_async(f"artifacts/{adir.name}", adir)  # heatmaps, frames, spectrograms
            bundles.append(bundle)
            db.commit()

        # Case context statement is analysed as text too
        context_claims = None
        if case.context_statement.strip():
            self.progress(0.82, "Analysing case context statement")
            cres = text_agent.analyze(case.context_statement, None, source="case context statement")
            self._save(cres, None, label="context statement")
            context_claims = cres["artifacts"]["claims"]

        self.progress(0.86, "Cross-modal correlation")
        cross_out, findings, rels = correlate(bundles, context_claims)
        for ind in cross_out.get("indicators", []):
            target = next((b for b in bundles if b.label == ind["location"].get("evidence")), None)
            if target:
                db.add(ManipulationIndicator(
                    job_id=job.id, evidence_id=target.evidence_id, agent_name=LEAD_AGENT, category=ind["category"], indicator_type=ind["indicator_type"],
                    severity=Severity(ind["severity"]), description=ind["description"], location=ind["location"], score=ind.get("score"),
                    alternative_explanations=ind["alternative_explanations"], recommended_checks=ind["recommended_checks"]))
        finding_rows = []
        for f in findings:
            row = CrossModalFinding(job_id=job.id, finding_type=f.finding_type, effect=f.effect, description=f.description,
                                    evidence_ids=f.evidence_ids, details=f.details, confidence=f.confidence)
            db.add(row)
            finding_rows.append(row)
        db.flush()
        db.execute(delete(EvidenceRelationship).where(EvidenceRelationship.case_id == case.id))
        for r in rels:
            db.add(EvidenceRelationship(case_id=case.id, job_id=job.id, source_id=r.source_id, target_id=r.target_id,
                                        relation=RelationType(r.relation), rationale=r.rationale, weight=r.weight))
        for fr in finding_rows:  # finding nodes link to the evidence they concern
            rel = {"strengthens": RelationType.conflicts, "weakens": RelationType.supports}.get(fr.effect, RelationType.uncertain)
            for eid in fr.evidence_ids:
                db.add(EvidenceRelationship(case_id=case.id, job_id=job.id, source_id=f"finding:{fr.id}", target_id=eid, relation=rel,
                                            rationale=fr.finding_type, weight=fr.confidence or 0.5))

        self.progress(0.92, "Evidence review")
        fdicts = [{"finding_type": f.finding_type, "effect": f.effect, "description": f.description, "evidence_ids": f.evidence_ids} for f in findings]
        rev = review(per_evidence, cross_out, fdicts)

        self.progress(0.96, "Report outline")
        db.flush()
        inds = db.scalars(select(ManipulationIndicator).where(ManipulationIndicator.job_id == job.id)).all()
        labels = {e.id: e.label for e in evidence}
        ind_dicts = [{"evidence_label": labels.get(i.evidence_id), "indicator_type": i.indicator_type, "location": i.location, "severity": i.severity.value,
                      "recommended_checks": i.recommended_checks} for i in inds]
        rep = build_report_outline({"id": case.id, "reference": case.reference, "title": case.title}, rev["artifacts"]["assessments"], ind_dicts, fdicts)
        lead = {"agent_name": LEAD_AGENT, "agent_version": rev["agent_version"], "status": "completed", "suspicion_score": cross_out.get("suspicion_score"),
                "confidence": None, "observations": rev["observations"] + cross_out["observations"],
                "interpretation": f"{rev['interpretation']} Cross-modal correlation: {cross_out.get('interpretation', '')}",
                "signals": cross_out.get("signals", []), "limitations": list(dict.fromkeys(rev["limitations"] + cross_out["limitations"])),
                "errors": cross_out.get("errors", []), "model_checks": [],
                "artifacts": {**rev["artifacts"], "findings": cross_out["artifacts"].get("findings", []),
                              "relationships": cross_out["artifacts"].get("relationships", []), "report_outline": rep["artifacts"]},
                "duration_ms": rev["duration_ms"] + cross_out["duration_ms"] + rep["duration_ms"]}
        self._save(lead, None)

        job.summary = {"overall": rev["artifacts"]["overall"], "assessments": rev["artifacts"]["assessments"],
                       "disagreements": rev["artifacts"]["disagreements"], "indicator_count": len(inds), "finding_count": len(findings),
                       "cross_modal": cross_out.get("interpretation", "")}
        job.status = JobStatus.partial if any_failed else JobStatus.completed
        job.finished_at = utcnow()
        self.progress(1.0, "Completed" if not any_failed else "Completed with failed analyses")
        audit.record(db, "analysis.completed", user_id=job.requested_by, case_id=case.id,
                     detail={"job_id": job.id, "status": job.status.value, "indicators": len(inds), "findings": len(findings)})


def run_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(AnalysisJob, job_id)
        if not job:
            return
        try:
            Orchestrator(db, job).run()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.error("Job %s failed: %s", job_id, traceback.format_exc())
            job = db.get(AnalysisJob, job_id)
            job.status, job.error, job.finished_at, job.stage = JobStatus.failed, f"{type(e).__name__}: analysis pipeline error", utcnow(), "Failed"
            db.commit()
            audit.record(db, "analysis.failed", user_id=job.requested_by, case_id=job.case_id, detail={"job_id": job_id, "error": type(e).__name__})
    finally:
        db.close()

"""Cross-Modal Correlation Agent.

Compares signals across image, video, audio and text evidence and reports findings that
strengthen or weaken the case for manipulation, plus evidence relationships for the
Evidence DNA Graph. Disagreements between agents are surfaced, never averaged away."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from .base import AgentOutput, Indicator, Signal, Timer, clamp01
from .explanations import enrich

NAME = "Cross-Modal Correlation Agent"
VERSION = "1.1.0"


@dataclass
class Finding:
    finding_type: str
    effect: str  # strengthens | weakens | neutral
    description: str
    evidence_ids: list[str]
    details: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5


@dataclass
class Relationship:
    source_id: str
    target_id: str
    relation: str  # supports | conflicts | uncertain | derived_from
    rationale: str
    weight: float = 0.5


@dataclass
class EvidenceBundle:
    evidence_id: str
    label: str
    media_type: str
    metadata: dict = field(default_factory=dict)
    duration_s: float | None = None
    brightness: float | None = None
    dhash: int | None = None
    frame_hashes: list[tuple[float, int]] = field(default_factory=list)  # (t, dhash) for video thumbs
    speech_envelope: tuple[list[float], list[float]] | None = None  # (times, rms)
    claims: dict | None = None
    cues: list[dict] = field(default_factory=list)
    pixel_indicators: int = 0
    indicator_types: list[str] = field(default_factory=list)
    sync: dict | None = None


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dhash_image(img) -> int:
    """64-bit difference hash of a BGR/gray numpy image."""
    import cv2

    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    s = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (s[:, 1:] > s[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def _capture_dates(b: EvidenceBundle) -> list[datetime]:
    out = []
    md = b.metadata
    for k in ("DateTimeOriginal", "DateTimeDigitized"):
        v = md.get(k)
        if v:
            try:
                out.append(datetime.strptime(str(v)[:19], "%Y:%m:%d %H:%M:%S"))
            except ValueError:
                pass
    ct = md.get("creation_time")
    if ct and not str(ct).startswith("1904"):
        try:
            out.append(datetime.fromisoformat(str(ct)).replace(tzinfo=None))
        except ValueError:
            pass
    return out


def _speech_overlap(cues: list[dict], env: tuple[list[float], list[float]]) -> float:
    t, rms = np.asarray(env[0]), np.asarray(env[1])
    if t.size < 5:
        return float("nan")
    db = 20 * np.log10(rms + 1e-9)
    active = db > max(np.percentile(db, 10) + 12, db.max() - 45)
    total = hit = 0.0
    for c in cues:
        m = (t >= c["start"]) & (t <= c["end"])
        if m.any():
            total += 1
            hit += float(active[m].mean())
    return hit / total if total else float("nan")


def correlate(bundles: list[EvidenceBundle], context_claims: dict | None) -> tuple[dict, list[Finding], list[Relationship]]:
    out = AgentOutput(NAME, VERSION)
    findings: list[Finding] = []
    rels: list[Relationship] = []
    with Timer() as t:
        media = [b for b in bundles if b.media_type != "text"]
        texts = [b for b in bundles if b.media_type == "text" and b.claims]
        claim_sources: list[tuple[str | None, str, dict]] = [(b.evidence_id, b.label, b.claims) for b in texts]
        if context_claims:
            claim_sources.append((None, "case context statement", context_claims))

        for src_id, src_label, claims in claim_sources:
            for m in media:
                # Dates
                caps = _capture_dates(m)
                cdates = [datetime.fromisoformat(d["date"]) for d in claims.get("dates", [])]
                if caps and cdates:
                    best = min(abs((c.date() - d.date()).days) for c in caps for d in cdates)
                    ok = best <= 1
                    findings.append(Finding("date_consistency", "weakens" if ok else "strengthens",
                        f"{src_label} claims date(s) {', '.join(d.date().isoformat() for d in cdates[:3])}; {m.label} metadata records {caps[0].date().isoformat()}"
                        + ("" if ok else f" ({best} day(s) apart)"), [x for x in (src_id, m.evidence_id) if x],
                        {"claimed": [d.date().isoformat() for d in cdates], "recorded": [c.isoformat() for c in caps], "gap_days": best}, 0.55))
                    rels.append(Relationship(src_id or "context", m.evidence_id, "supports" if ok else "conflicts", f"Claimed vs recorded date ({best} day gap)", 0.6))
                    if not ok:
                        out.indicators.append(enrich(Indicator("metadata", "context_mismatch", "medium",
                            f"Claimed date in {src_label} differs from {m.label} capture metadata by {best} day(s)", location={"evidence": m.label, "field": "date"}, score=clamp01(best / 30))))
                elif cdates and not caps:
                    findings.append(Finding("date_unverifiable", "neutral", f"{src_label} claims a date but {m.label} has no capture-date metadata to compare",
                                            [x for x in (src_id, m.evidence_id) if x], {}, 0.3))
                    rels.append(Relationship(src_id or "context", m.evidence_id, "uncertain", "Date claim cannot be checked against metadata", 0.3))
                # Durations
                for d in claims.get("durations", []):
                    if m.duration_s and m.media_type in ("video", "audio"):
                        rel_err = abs(m.duration_s - d["seconds"]) / max(d["seconds"], 1)
                        ok = rel_err <= 0.15
                        findings.append(Finding("duration_consistency", "weakens" if ok else "strengthens",
                            f"{src_label} describes '{d['text']}'; {m.label} lasts {m.duration_s:.1f}s", [x for x in (src_id, m.evidence_id) if x],
                            {"claimed_s": d["seconds"], "actual_s": m.duration_s}, 0.6))
                        rels.append(Relationship(src_id or "context", m.evidence_id, "supports" if ok else "conflicts", "Claimed vs actual duration", 0.5))
                        if not ok:
                            out.indicators.append(enrich(Indicator("text", "context_mismatch", "low",
                                f"Claimed duration '{d['text']}' does not match {m.label} ({m.duration_s:.1f}s) – possible trimming or different clip",
                                location={"evidence": m.label, "field": "duration"}, score=clamp01(rel_err))))
                # Device
                devs = claims.get("devices", [])
                make = " ".join(str(m.metadata.get(k, "")) for k in ("Make", "Model")).lower() + " " + " ".join(m.metadata.get("encoder_strings", [])).lower()
                if devs and make.strip():
                    alias = {"iphone": "apple", "galaxy": "samsung", "pixel": "google"}
                    ok = any(d in make or alias.get(d, "#") in make for d in devs)
                    findings.append(Finding("device_consistency", "weakens" if ok else "strengthens",
                        f"{src_label} mentions {', '.join(devs)}; {m.label} metadata indicates '{make.strip()[:60]}'", [x for x in (src_id, m.evidence_id) if x], {}, 0.45))
                    rels.append(Relationship(src_id or "context", m.evidence_id, "supports" if ok else "conflicts", "Claimed vs recorded device", 0.4))
                # Time of day vs brightness
                tod = claims.get("time_of_day")
                if tod and m.brightness is not None and m.media_type in ("image", "video"):
                    # Three-way: only clearly dark/bright scenes count; mid-range luminance is inconclusive
                    # (artificial lighting, exposure compensation and night modes all blur this cue).
                    b = m.brightness
                    conflict = (tod == "night" and b > 150) or (tod == "day" and b < 45)
                    consistent = (tod == "night" and b < 60) or (tod == "day" and b > 110)
                    effect, rel = ("strengthens", "conflicts") if conflict else ("weakens", "supports") if consistent else ("neutral", "uncertain")
                    findings.append(Finding("lighting_consistency", effect,
                        f"{src_label} says {tod}; mean luminance of {m.label} is {b:.0f}/255" + ("" if conflict or consistent else " (inconclusive)"),
                        [x for x in (src_id, m.evidence_id) if x], {"claimed": tod, "luminance": round(b, 1)}, 0.3))
                    rels.append(Relationship(src_id or "context", m.evidence_id, rel, "Claimed time of day vs scene brightness (weak cue)", 0.3))
                # "Not edited" claims vs editing traces
                if claims.get("negated_media_claims") and ("editing_software_tag" in m.indicator_types or "generator_metadata" in m.indicator_types):
                    findings.append(Finding("editing_claim_conflict", "strengthens",
                        f"{src_label} asserts the media is unedited, but {m.label} metadata records editing/generation software", [x for x in (src_id, m.evidence_id) if x], {}, 0.6))
                    rels.append(Relationship(src_id or "context", m.evidence_id, "conflicts", "'Unedited' claim vs editing metadata", 0.6))

        # Transcripts vs speech activity
        for tb in texts:
            if not tb.cues:
                continue
            for m in media:
                if not m.speech_envelope:
                    continue
                ov = _speech_overlap(tb.cues, m.speech_envelope)
                beyond = sum(1 for c in tb.cues if m.duration_s and c["start"] > m.duration_s + 1)
                if np.isnan(ov):
                    continue
                ok = ov >= 0.5 and beyond == 0
                findings.append(Finding("transcript_alignment", "weakens" if ok else "strengthens",
                    f"{ov * 100:.0f}% of transcript cues in {tb.label} coincide with detected speech in {m.label}"
                    + (f"; {beyond} cue(s) start after the media ends" if beyond else ""), [tb.evidence_id, m.evidence_id],
                    {"speech_overlap": round(ov, 3), "cues_beyond_end": beyond}, 0.55))
                rels.append(Relationship(tb.evidence_id, m.evidence_id, "supports" if ok else "conflicts", f"Transcript/speech overlap {ov * 100:.0f}%", 0.6))
                if not ok:
                    out.indicators.append(enrich(Indicator("text", "transcript_alignment", "medium" if ov < 0.3 or beyond else "low",
                        f"Transcript {tb.label} poorly aligned with speech in {m.label} ({ov * 100:.0f}% overlap)", location={"evidence": m.label}, score=clamp01(1 - ov))))

        # Visual provenance: image ↔ video frames, image ↔ image
        imgs = [b for b in media if b.media_type == "image" and b.dhash is not None]
        vids = [b for b in media if b.media_type == "video" and b.frame_hashes]
        for im in imgs:
            for v in vids:
                best = min(((hamming(im.dhash, h), tt) for tt, h in v.frame_hashes), default=None)
                if best and best[0] <= 8:
                    findings.append(Finding("visual_provenance", "neutral", f"{im.label} closely matches the frame at {best[1]:.2f}s of {v.label} (dHash distance {best[0]})",
                                            [im.evidence_id, v.evidence_id], {"t": best[1], "distance": best[0]}, 0.7))
                    rels.append(Relationship(im.evidence_id, v.evidence_id, "derived_from", f"Matches video frame at {best[1]:.2f}s", 0.8))
        for i, a in enumerate(imgs):
            for b in imgs[i + 1:]:
                d = hamming(a.dhash, b.dhash)
                if d <= 10:
                    findings.append(Finding("near_duplicate", "neutral", f"{a.label} and {b.label} are near-duplicates (dHash distance {d}); compare for differences",
                                            [a.evidence_id, b.evidence_id], {"distance": d}, 0.7))
                    rels.append(Relationship(a.evidence_id, b.evidence_id, "derived_from", f"Near-duplicate images (distance {d})", 0.7))

        # Separate audio vs video soundtrack
        auds = [b for b in media if b.media_type == "audio" and b.speech_envelope]
        for a in auds:
            for v in [b for b in media if b.media_type == "video" and b.speech_envelope]:
                ea, ev = np.asarray(a.speech_envelope[1]), np.asarray(v.speech_envelope[1])
                n = min(ea.size, ev.size)
                if n < 50:
                    continue
                ea, ev = (ea[:n] - ea[:n].mean()) / (ea[:n].std() + 1e-9), (ev[:n] - ev[:n].mean()) / (ev[:n].std() + 1e-9)
                c = float(np.max(np.correlate(ea, ev, "full")) / n)
                same = c > 0.6
                findings.append(Finding("audio_track_match", "neutral",
                    f"{a.label} {'appears to be the same recording as' if same else 'does not match'} the soundtrack of {v.label} (envelope correlation {c:.2f})",
                    [a.evidence_id, v.evidence_id], {"corr": round(c, 3)}, 0.5))
                rels.append(Relationship(a.evidence_id, v.evidence_id, "derived_from" if same else "uncertain", f"Audio envelope correlation {c:.2f}", 0.5))

        # Lip-sync
        for v in vids + [b for b in media if b.media_type == "video" and not b.frame_hashes]:
            if v.sync and v.sync.get("status") == "completed":
                n_bad = len(v.sync.get("indicators", []))
                findings.append(Finding("voice_face_sync", "strengthens" if n_bad else "weakens",
                    f"Voice-face sync for {v.label}: {v.sync.get('interpretation', '')}", [v.evidence_id], v.sync.get("artifacts", {}), 0.4))

        # Agent agreement / disagreement per evidence
        for m in media:
            pix = m.pixel_indicators
            edit_md = "editing_software_tag" in m.indicator_types or "generator_metadata" in m.indicator_types
            if pix and edit_md:
                findings.append(Finding("agent_agreement", "strengthens", f"{m.label}: content-level indicators and editing/generation metadata point the same way",
                                        [m.evidence_id], {"pixel_indicators": pix}, 0.6))
            elif pix and m.metadata.get("Make") and not edit_md:
                findings.append(Finding("agent_disagreement", "neutral",
                    f"{m.label}: content-level indicators found, but metadata shows an unedited camera file – agents disagree; human review required",
                    [m.evidence_id], {"pixel_indicators": pix}, 0.5))
            elif "generator_metadata" in m.indicator_types and not pix:
                findings.append(Finding("agent_disagreement", "neutral",
                    f"{m.label}: metadata references a generator, but no pixel-level anomalies were found (fully synthetic images often lack them)",
                    [m.evidence_id], {}, 0.5))

        out.observe("Evidence items compared", len(bundles))
        out.observe("Cross-modal findings", len(findings))
        strengthen = sum(1 for f in findings if f.effect == "strengthens")
        weaken = sum(1 for f in findings if f.effect == "weakens")
        out.observe("Findings strengthening concern", strengthen)
        out.observe("Findings weakening concern", weaken)
        if strengthen + weaken:
            out.signals.append(Signal("Cross-modal conflicts", round(strengthen / (strengthen + weaken), 3), 1.0, "suspicious" if strengthen else "reassuring"))
            out.suspicion_score = round(strengthen / (strengthen + weaken), 4)
        out.confidence = 0.5 if len(bundles) > 1 else 0.2
        if len(bundles) < 2 and not context_claims:
            out.status = "skipped"
            out.interpretation = "Only one evidence item and no context statement – nothing to correlate."
        else:
            out.interpretation = f"{strengthen} finding(s) increase concern, {weaken} reduce it, {len(findings) - strengthen - weaken} neutral/provenance."
        out.limitations += ["Correlation is limited to extractable, machine-readable claims and metadata.",
                            "A consistent context does not prove authenticity; metadata and captions can both be fabricated."]
        out.artifacts = {"findings": [asdict(f) for f in findings], "relationships": [asdict(r) for r in rels]}
    out.duration_ms = t.ms
    return out.to_dict(), findings, rels

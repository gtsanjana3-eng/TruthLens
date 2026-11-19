"""AI-model analysis (one validated pre-trained model per media type).

  image -> AI-generated image classifier          (Image Forensics Agent)
  video -> face-swap deepfake classifier on faces (Video Analysis Agent)
  audio -> synthetic / cloned voice classifier    (Audio Analysis Agent)
  text  -> AI-written text classifier             (Text Analysis Agent)

Rules (all measured, nothing hard-coded):
* A model runs only on input it was built for (faces for the face model, camera-like images for the image
  model, speech windows for the voice model, >= 40 words for the text model); otherwise "not applicable".
* A model may decide the result only if its ROC-AUC measured on held-out labelled data OF THE SAME MEDIA TYPE
  is >= 0.75 ("decision-grade"). Otherwise its score is shown for reference only.
* The confidence shown for a decision is the model's measured precision ("likely AI") or negative predictive
  value ("no indication") on that held-out data, with a 95% interval – never a raw model score.
* Scores are softmax outputs of uncalibrated classifiers ("model scores"), not probabilities.
Safety: pinned revisions, safetensors only, no remote code, YuNet hash verified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import hf_models
from .base import AgentOutput, Indicator, ModelCheck, Signal, Timer
from .explanations import enrich

NAME = "AI model"
VERSION = "3.0.0"
LIKELY_FAKE, LIKELY_REAL = 0.70, 0.30
MIN_FACE_FRAMES = 3
MIN_TEXT_WORDS = 40
DECISION_GRADE_AUC = 0.75
EVAL_DIR = Path(__file__).resolve().parents[2] / "evaluation"
VALIDATION_FILES = {"video": EVAL_DIR / "video_results.json", "image": EVAL_DIR / "image_results.json",
                    "audio": EVAL_DIR / "audio_results.json", "text": EVAL_DIR / "text_results.json"}
ROLE = {"video": "Face-swap deepfake model", "image": "AI-generated image model", "audio": "AI voice model", "text": "AI text model"}
SCOPE = {"video": "face-swap manipulation of visible faces (not fully AI-generated video or lip-sync)",
         "image": "fully AI-generated images (not local edits – see manipulation analysis)",
         "audio": "synthetic / cloned speech", "text": "machine-written English text"}


def dependencies_available() -> tuple[bool, str]:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception as e:  # noqa: BLE001
        return False, f"PyTorch/Transformers not installed ({type(e).__name__}). Install backend/requirements-ml.txt to enable."
    return True, "ok"


# ---------------------------------------------------------------- validation & confidence
def _wilson(k: int, n: int, z: float = 1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


def validation_for(model_id: str, media: str) -> dict | None:
    """Held-out measurements of `model_id` on this media type (from evaluation/<media>_results.json)."""
    try:
        data = json.loads(VALIDATION_FILES[media].read_text("utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    try:
        if media == "video":
            det = next((d for d in data["detectors"].values() if d.get("model") == model_id), None)
            if not det or det.get("roc_auc") is None:
                return None
            m = data.get("app_headline", {}).get("on_decided_only", {})
            tp, fp, tn, fn = (m.get(k, 0) for k in ("tp", "fp", "tn", "fn"))
            auc, n, dataset = det["roc_auc"], det["applicable_videos"], data["meta"]["dataset"]
        else:
            r = data["results"].get(model_id)
            if not r or r.get("roc_auc") is None:
                return None
            m = r["rule_likely_ai"]
            tp, fp = m["tp"], m["fp"]
            tn, fn = r["no_indication_npv"]["k"], r["no_indication_npv"]["n"] - r["no_indication_npv"]["k"]
            auc, n, dataset = r["roc_auc"], r["n"], data["dataset"]
    except (KeyError, TypeError):
        return None
    return {"roc_auc": auc, "n": n, "dataset": dataset, "decision_grade": auc >= DECISION_GRADE_AUC,
            "precision": {"k": tp, "n": tp + fp, "value": round(tp / (tp + fp), 3) if tp + fp else None, "ci95": _wilson(tp, tp + fp)},
            "npv": {"k": tn, "n": tn + fn, "value": round(tn / (tn + fn), 3) if tn + fn else None, "ci95": _wilson(tn, tn + fn)}}


def policy_fingerprint() -> str:
    """Changes whenever validation data or the decision rule changes, so cached verdicts are never stale."""
    h = hashlib.sha256(f"{VERSION}|{DECISION_GRADE_AUC}|{LIKELY_FAKE}|{LIKELY_REAL}".encode())
    for f in sorted(VALIDATION_FILES.values()):
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(b"-")
    return h.hexdigest()[:10]


def confidence_for(verdict: str, validation: dict | None, media: str) -> dict:
    """Confidence Score System: measured hit-rate of this kind of decision on held-out data (never a hard-coded number)."""
    if not validation or not validation["decision_grade"]:
        return {"level": "none", "value": None, "basis": "No decision-grade model for this media type – no confidence can be stated."}
    key = "precision" if verdict == "likely_ai" else "npv" if verdict == "no_indication" else None
    if not key:
        return {"level": "none", "value": None, "basis": "The model's score fell between the decision thresholds – no decision, so no confidence."}
    m = validation[key]
    if not m["n"]:
        return {"level": "none", "value": None, "basis": "Not enough held-out decisions of this kind to measure confidence."}
    lo = (m["ci95"] or [0, 0])[0]
    level = "high" if lo >= 0.8 else "medium" if lo >= 0.6 else "low"
    what = "said 'likely AI'" if key == "precision" else "said 'no indication'"
    return {"level": level, "value": m["value"], "ci95": m["ci95"], "k": m["k"], "n": m["n"],
            "basis": f"When this model {what} on {validation['n']} held-out {media} samples it was right {m['k']} of {m['n']} times "
                     f"(95% range {m['ci95'][0] * 100:.0f}–{m['ci95'][1] * 100:.0f}%)."}


# ---------------------------------------------------------------- verdicts
def band(mean_p: float, frac: float) -> str:
    if mean_p >= LIKELY_FAKE and frac >= 0.6:
        return "likely_ai"
    if mean_p <= LIKELY_REAL and frac <= 0.2:
        return "no_indication"
    return "inconclusive"


BAND_TEXT = {
    "likely_ai": "Likely AI-generated / manipulated (model indication)",
    "no_indication": "No indication from the validated model – not proof of authenticity",
    "inconclusive": "Inconclusive – the model is not confident either way",
    "not_applicable": "AI model not applicable to this content",
    "no_validated_detector": "No validated model for this media type – score shown for reference only",
    "unavailable": "AI model unavailable",
}


def summarize(scores: list[float]) -> dict:
    arr = np.asarray(scores, dtype=float)
    mean_p, frac = float(arr.mean()), float((arr >= 0.5).mean())
    return {"n": int(arr.size), "mean": round(mean_p, 4), "median": round(float(np.median(arr)), 4), "max": round(float(arr.max()), 4),
            "fraction_ge_0_5": round(frac, 4), "fraction_ge_0_7": round(float((arr >= LIKELY_FAKE).mean()), 4), "verdict": band(mean_p, frac)}


COMMON_LIMITS = [
    "Model scores are softmax outputs of an uncalibrated classifier, not probabilities.",
    "A model indication is one signal; it is never proof on its own and must be reviewed by a person.",
]


def _detector(media: str, cfg: dict) -> dict:
    model_id, rev = cfg[media]
    return {"key": media, "role": ROLE[media], "model": model_id, "revision": rev, "scope": SCOPE[media],
            "url": f"https://huggingface.co/{model_id}/tree/{rev}"}


def _decide(out: AgentOutput, d: dict, media: str, require_validation: bool) -> str:
    """Attach validation, decide the verdict, set score/confidence/limitations consistently from ONE rule."""
    v = validation_for(d["model"], media)
    d["validation"] = v
    d["decision_grade"] = bool(v["decision_grade"]) if v else (not require_validation)
    out.model_checks.append(ModelCheck(f"{d['role']}: {d['model']}@{d['revision'][:8]}",
                                       "completed" if d["status"] == "completed" else "unavailable",
                                       d.get("reason") or d.get("error") or "safetensors, pinned revision"))
    if d["status"] != "completed":
        verdict = "unavailable" if d["status"] in ("unavailable", "failed") else "not_applicable"
    elif not d["decision_grade"]:
        verdict = "no_validated_detector"
    else:
        verdict = d["summary"]["verdict"]
    if v:
        out.observe(f"Model accuracy measured on {v['n']} held-out {media} samples (ROC-AUC – property of the model, not this file)", v["roc_auc"])
    if d["status"] == "completed":
        s = d["summary"]
        out.observe("Model score for this file (mean)", s["mean"])
        out.observe("Inputs scored ≥ 0.70", f"{s['fraction_ge_0_7'] * 100:.0f}% of {s['n']}")
        out.signals.append(Signal(f"{d['role']} score", s["mean"], 1.0 if d["decision_grade"] else 0.0,
                                  ("suspicious" if s["mean"] >= 0.5 else "reassuring") if d["decision_grade"] else "neutral",
                                  "Uncalibrated model score" + ("" if d["decision_grade"] else " – reference only, not decision-grade")))
        out.suspicion_score = s["mean"] if d["decision_grade"] else None
    if d["status"] == "completed" and not d["decision_grade"]:
        out.limitations.append(f"{d['model']} is not validated on held-out {media} data at ROC-AUC ≥ {DECISION_GRADE_AUC}"
                               + (f" (measured {v['roc_auc']:.2f})" if v else "") + "; its score is reference only and cannot decide the result.")
    conf = confidence_for(verdict, v, media)
    out.confidence = conf.get("value")
    text = BAND_TEXT[verdict] + "."
    if d["status"] == "completed":
        text += f" {d['role']} score {d['summary']['mean']:.2f}."
    elif d.get("reason"):
        text += f" Reason: {d['reason']}."
    text += f" Scope: {SCOPE[media]}."
    if conf.get("value") is not None:
        text += " " + conf["basis"]
    out.interpretation = text
    out.limitations += COMMON_LIMITS
    out.artifacts.update({"verdict": verdict, "verdict_text": BAND_TEXT[verdict], "confidence": conf, "detector": d,
                          "thresholds": {"likely_ai": LIKELY_FAKE, "no_indication": LIKELY_REAL, "decision_grade_auc": DECISION_GRADE_AUC}})
    return verdict


def _run(d: dict, fn, *args) -> Any:
    try:
        res = fn(*args)
        d["status"] = "completed"
        return res
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        offline_err = "local_files_only" in msg or "offline" in msg.lower() or "connect" in msg.lower()
        d["status"] = "unavailable" if offline_err else "failed"
        d["error"] = f"{type(e).__name__}: {msg[:200]}"
        return None


# ---------------------------------------------------------------- face detection & content profile
_yunet: dict[str, Any] = {}
_haar = None


def _yunet_detector(path: str | None, sha256: str | None):
    if not path or not Path(path).exists():
        return None
    if path not in _yunet:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        if sha256 and digest != sha256:
            raise ValueError("YuNet model hash mismatch – refusing to load")
        _yunet[path] = cv2.FaceDetectorYN.create(str(path), "", (320, 320), 0.7, 0.3, 50)
    return _yunet[path]


def detect_face(bgr: np.ndarray, yunet: tuple[str, str] | None = None, margin: float = 0.3) -> tuple[np.ndarray | None, list[int] | None, str]:
    """Return (square face crop, [x,y,w,h] in frame px, detector name) for the largest confident face."""
    global _haar
    h, w = bgr.shape[:2]
    scale = min(1.0, 640.0 / max(h, w))
    small = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else bgr
    det = _yunet_detector(*yunet) if yunet else None
    box, name = None, ("YuNet" if det is not None else "Haar")
    if det is not None:
        det.setInputSize((small.shape[1], small.shape[0]))
        _, faces = det.detect(small)
        if faces is not None and len(faces):
            f = max(faces, key=lambda r: r[2] * r[3])
            box = [float(v) / scale for v in f[:4]]
    else:
        if _haar is None:
            _haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = _haar.detectMultiScale(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), 1.15, 5, minSize=(40, 40))
        if len(faces):
            box = [float(v) / scale for v in max(faces, key=lambda r: r[2] * r[3])]
    if box is None:
        return None, None, name
    x, y, fw, fh = box
    if fw < 32 or fh < 32:
        return None, None, name
    side = max(fw, fh) * (1 + 2 * margin)
    cx, cy = x + fw / 2, y + fh / 2
    x0, y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
    x1, y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
    crop = bgr[y0:y1, x0:x1]
    return (crop if crop.size else None), [int(x), int(y), int(fw), int(fh)], name


def content_profile(bgr: np.ndarray) -> dict:
    """Camera footage has sensor noise and few perfectly flat areas; rendered graphics/slides have neither."""
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    r = g[1:, 1:] - g[:-1, 1:] - g[1:, :-1] + g[:-1, :-1]
    noise = float(np.median(np.abs(r)) / 0.6745)
    hb, wb = g.shape[0] // 8, g.shape[1] // 8
    blocks = g[: hb * 8, : wb * 8].reshape(hb, 8, wb, 8).std(axis=(1, 3))
    flat = float((blocks < 1.0).mean())
    return {"noise_sigma": round(noise, 3), "flat_fraction": round(flat, 3), "rendered": bool(noise < 0.6 and flat > 0.7)}


def fake_probabilities(images: list[np.ndarray], model_id: str, revision: str, cache_dir: str, offline: bool = False, batch: int = 16) -> list[float]:
    """Image-model scores for RGB uint8 images (kept for the evaluation scripts)."""
    return hf_models.score_images(images, model_id, revision, cache_dir, offline, batch)


# ---------------------------------------------------------------- per-media analysis
def analyze_image(path: str, evidence_id: str, cfg: dict) -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    with Timer() as t:
        bgr = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            out.status = "failed"
            out.errors.append("Image could not be decoded")
            return out.to_dict()
        content = content_profile(bgr)
        out.observe("Content profile", f"{'computer-rendered graphics' if content['rendered'] else 'camera-like image'} (noise σ {content['noise_sigma']:.2f})")
        d = _detector("image", cfg)
        if content["rendered"]:
            d.update(status="not_applicable", reason="computer-rendered graphics (screenshot, slide, drawing) – outside the model's photo domain")
        else:
            scores = _run(d, hf_models.score_images, [cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)], d["model"], d["revision"], cfg["cache_dir"], cfg.get("offline", False))
            if scores is not None:
                d["scores"] = [round(s, 4) for s in scores]
                d["summary"] = summarize(scores)
        verdict = _decide(out, d, "image", cfg.get("require_validation", False))
        if verdict == "likely_ai":
            out.indicators.append(enrich(Indicator("region", "learned_model_ai_image", "medium",
                f"Validated AI-image model scores the whole image {d['summary']['mean']:.2f} (≥ {LIKELY_FAKE})",
                location={"scope": "whole_image", "model": d["model"]}, score=d["summary"]["mean"])))
        out.artifacts["content"] = content
    out.duration_ms = t.ms
    return out.to_dict()


def analyze_audio(path: str, evidence_id: str, cfg: dict, max_seconds: float = 900) -> dict:
    from .audio_agent import load_audio

    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    with Timer() as t:
        d = _detector("audio", cfg)
        try:
            x, _, _ = load_audio(path, max_seconds)
        except Exception as e:  # noqa: BLE001
            out.status = "failed"
            out.errors.append(f"Audio decoding failed: {type(e).__name__}")
            return out.to_dict()
        if x.size < 16000 * 1.5:
            d.update(status="not_applicable", reason="less than 1.5 s of audio")
            windows = None
        else:
            windows = _run(d, hf_models.score_audio, x, 16000, d["model"], d["revision"], cfg["cache_dir"], cfg.get("offline", False))
        if windows:
            d["windows"] = [{"t_start": t0, "t_end": round(t0 + 4.0, 2), "score": round(s, 4)} for t0, s in windows]
            d["summary"] = summarize([s for _, s in windows])
        verdict = _decide(out, d, "audio", cfg.get("require_validation", False))
        if windows and d.get("decision_grade"):
            for w in d["windows"]:
                if w["score"] >= LIKELY_FAKE:
                    out.indicators.append(enrich(Indicator("segment", "learned_model_ai_voice", "medium" if verdict == "likely_ai" else "low",
                        f"Voice model scores {w['t_start']:.1f}–{w['t_end']:.1f}s as synthetic speech ({w['score']:.2f})",
                        location={"t_start": w["t_start"], "t_end": w["t_end"], "model": d["model"]}, score=w["score"])))
        out.artifacts["duration_s"] = round(x.size / 16000, 2)
    out.duration_ms = t.ms
    return out.to_dict()


def analyze_text(text: str, evidence_id: str | None, cfg: dict) -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    with Timer() as t:
        d = _detector("text", cfg)
        words = len(text.split())
        out.observe("Words analysed", words)
        if words < MIN_TEXT_WORDS:
            d.update(status="not_applicable", reason=f"only {words} words – AI-text models are unreliable below {MIN_TEXT_WORDS} words")
            chunks = None
        else:
            chunks = _run(d, hf_models.score_text, text, d["model"], d["revision"], cfg["cache_dir"], cfg.get("offline", False))
        if chunks:
            ends = [c[0] for c in chunks[1:]] + [len(text)]
            d["chunks"] = [{"start": a, "end": e, "score": round(s, 4), "preview": text[a:min(e, a + 120)]} for (a, s), e in zip(chunks, ends)]
            d["summary"] = summarize([s for _, s in chunks])
        verdict = _decide(out, d, "text", cfg.get("require_validation", False))
        if chunks and d.get("decision_grade"):
            for c in d["chunks"]:
                if c["score"] >= LIKELY_FAKE:
                    out.indicators.append(enrich(Indicator("text", "learned_model_ai_text", "medium" if verdict == "likely_ai" else "low",
                        f"Text model scores this passage as machine-written ({c['score']:.2f}): “{c['preview'][:80]}…”",
                        location={"char_start": c["start"], "char_end": c["end"], "model": d["model"]}, score=c["score"])))
    out.duration_ms = t.ms
    return out.to_dict()


def analyze_video(path: str, evidence_id: str, cfg: dict, artifact_dir: str | None = None) -> dict:
    """Face crops from frames sampled across the whole video, scored by the face-swap model."""
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    art = Path(artifact_dir) / "ml" if artifact_dir else None
    if art:
        art.mkdir(parents=True, exist_ok=True)
    with Timer() as t:
        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
        frames: list[dict] = []
        raw: list[np.ndarray] = []
        try:
            fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            if total <= 0 or fps <= 0:
                from .mediainfo import probe

                ff = probe(path)
                fps = fps if fps > 0 else (ff.fps if ff and ff.fps else 25.0)
                total = total if total > 0 else int((ff.duration_s or 0) * fps) if ff else 0
            wanted = sorted(set(np.linspace(0, max(0, total - 1), num=min(int(cfg.get("n_frames", 24)), max(1, total))).astype(int).tolist()))
            want, i = set(wanted), 0
            while want and i <= wanted[-1]:
                if not cap.grab():
                    break
                if i in want:
                    ok, frame = cap.retrieve()
                    if ok:
                        raw.append(frame)
                        frames.append({"frame": i, "t": round(i / fps, 3)})
                    want.discard(i)
                i += 1
        finally:
            cap.release()
        if not frames:
            out.status = "failed"
            out.errors.append("No frames could be decoded")
            return out.to_dict()
        faces, profiles, det_name = [], [], "none"
        for f, img in zip(frames, raw):
            crop, box, det_name = detect_face(img, cfg.get("yunet"))
            f["face"], f["face_box"] = crop is not None, box
            if crop is not None:
                faces.append(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            profiles.append(content_profile(img))
        content = {"noise_sigma": float(np.median([p["noise_sigma"] for p in profiles])), "flat_fraction": float(np.median([p["flat_fraction"] for p in profiles]))}
        content["rendered"] = float(np.mean([p["rendered"] for p in profiles])) >= 0.5 and len(faces) < MIN_FACE_FRAMES
        out.observe("Frames analysed (evenly across the whole video)", len(frames))
        out.observe(f"Frames containing a face ({det_name} detector)", f"{len(faces)} of {len(frames)}")
        d = _detector("video", cfg)
        if len(faces) < MIN_FACE_FRAMES:
            d.update(status="not_applicable", reason=f"only {len(faces)} of {len(frames)} sampled frames contain a detectable face (needs ≥{MIN_FACE_FRAMES})")
        else:
            scores = _run(d, hf_models.score_images, faces, d["model"], d["revision"], cfg["cache_dir"], cfg.get("offline", False))
            if scores is not None:
                d["scores"] = [round(s, 4) for s in scores]
                d["summary"] = summarize(scores)
                for f, s in zip([f for f in frames if f["face"]], d["scores"]):
                    f["score"] = s
        verdict = _decide(out, d, "video", cfg.get("require_validation", False))
        graded = d.get("decision_grade") and d["status"] == "completed"
        for f in frames:
            f["suspicious"] = bool(graded and f.get("score", 0) >= LIKELY_FAKE)
        # temporal: abrupt jumps in the face score between consecutive face frames
        seq = [f for f in frames if "score" in f]
        jumps = [{"t_start": a["t"], "t_end": b["t"], "from": a["score"], "to": b["score"]} for a, b in zip(seq, seq[1:]) if abs(b["score"] - a["score"]) >= 0.5]
        segs, start, prev = [], None, None
        for f in frames:
            if f["suspicious"]:
                start, prev = (f["t"] if start is None else start), f["t"]
            elif start is not None:
                segs.append((start, prev))
                start = None
        if start is not None:
            segs.append((start, prev))
        for s0, s1 in segs[:8]:
            out.indicators.append(enrich(Indicator("frame", "learned_model_deepfake", "medium" if verdict == "likely_ai" else "low",
                f"Face-swap model scores frames {s0:.2f}s–{s1:.2f}s ≥ {LIKELY_FAKE}", location={"t_start": s0, "t_end": s1},
                score=max(f.get("score", 0) for f in frames if s0 <= f["t"] <= s1))))
        if graded:
            for j in jumps[:5]:
                out.indicators.append(enrich(Indicator("frame", "learned_model_temporal_jump", "low",
                    f"Face-swap score jumps {j['from']:.2f}→{j['to']:.2f} between {j['t_start']:.2f}s and {j['t_end']:.2f}s",
                    location={"t_start": j["t_start"], "t_end": j["t_end"]}, score=round(abs(j["to"] - j["from"]), 3))))
        evidence = []
        if art and seq:
            for i in sorted(sorted(range(len(frames)), key=lambda k: -frames[k].get("score", -1))[:6]):
                f, img = frames[i], raw[i]
                if "score" not in f:
                    continue
                fn = f"ml/frame_{f['frame']:07d}.jpg"
                cv2.imwrite(str(art / Path(fn).name), cv2.resize(img, (320, max(1, int(img.shape[0] * 320 / img.shape[1])))), [cv2.IMWRITE_JPEG_QUALITY, 80])
                item = {"t": f["t"], "frame": f["frame"], "image": fn, "score": f["score"], "suspicious": f["suspicious"]}
                crop, _, _ = detect_face(img, cfg.get("yunet"))
                if crop is not None:
                    cfn = f"ml/face_{f['frame']:07d}.jpg"
                    cv2.imwrite(str(art / Path(cfn).name), cv2.resize(crop, (160, 160)), [cv2.IMWRITE_JPEG_QUALITY, 85])
                    item["face_image"] = cfn
                evidence.append(item)
        out.artifacts.update({"frames": frames, "faces_found": len(faces), "frames_analysed": len(frames), "face_detector": det_name,
                              "content": content, "temporal_jumps": jumps, "visual_evidence": evidence})
    out.duration_ms = t.ms
    return out.to_dict()


def unavailable(evidence_id: str, reason: str) -> dict:
    out = AgentOutput(NAME, VERSION, status="unavailable", evidence_id=evidence_id,
                      interpretation="AI model not available – no model result was produced (nothing is simulated).")
    out.limitations.append(reason)
    out.artifacts = {"verdict": "unavailable", "verdict_text": BAND_TEXT["unavailable"], "confidence": {"level": "none", "value": None, "basis": reason}}
    return out.to_dict()


def config_from_settings(s) -> dict:
    def m(mid, rev):
        return (mid, rev) if mid else None
    return {"video": m(s.hf_video_model, s.hf_video_model_revision), "image": m(s.hf_image_model, s.hf_image_model_revision),
            "audio": m(s.hf_audio_model, s.hf_audio_model_revision), "text": m(s.hf_text_model, s.hf_text_model_revision),
            "cache_dir": ensure_dir(s.hf_cache_dir), "offline": s.hf_offline, "yunet": (str(s.yunet_path), s.yunet_sha256),
            "n_frames": s.ml_video_frames, "require_validation": True}


def model_cache_key(*parts: str) -> str:
    return "-".join(p.replace("/", "_")[:60] for p in parts)


def ensure_dir(p: str | Path) -> str:
    Path(p).mkdir(parents=True, exist_ok=True)
    return str(p)

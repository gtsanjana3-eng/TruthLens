"""Video Forensics Agent.

Streams the video with OpenCV (never loads it whole), sampling at <= 10 fps:
  * temporal discontinuities (frame difference + histogram correlation outliers)
  * duplicated / frozen frames
  * face-region sharpness inconsistency relative to the rest of the frame (face-swap blending cue)
  * mouth-region motion series (input to the Voice-Face Synchronization Analyzer)
Also writes frame thumbnails for the frame-by-frame inspector and extracts the audio track
with a bundled FFmpeg binary (argument list, no shell, with timeout).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import cv2
import numpy as np

from .base import AgentOutput, Indicator, Signal, Timer, aggregate_signals, clamp01, robust_z
from .explanations import enrich

NAME = "Video Analysis Agent"
VERSION = "1.3.1"
ANALYSIS_FPS = 10.0
MAX_ANALYSIS_FRAMES = 1800
_face = None


def _face_detector():
    global _face
    if _face is None:
        _face = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return _face


def ffmpeg_exe() -> str | None:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def extract_audio(video: str, dest: Path, timeout: int = 120) -> tuple[bool, str]:
    exe = ffmpeg_exe()
    if not exe:
        return False, "FFmpeg not available"
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-t", "900", "-f", "wav", str(dest)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, "Audio extraction timed out"
    if r.returncode != 0 or not dest.exists() or dest.stat().st_size < 1000:
        dest.unlink(missing_ok=True)
        return False, "No decodable audio track"
    return True, "ok"


def probe(path: str) -> dict:
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)  # never MSMF: it can block process exit on Windows
    try:
        if not cap.isOpened():
            raise ValueError("Video could not be opened")
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    finally:
        cap.release()
    # Some containers (WebM/MKV from browsers, streamed MP4) report no frame count or fps to OpenCV:
    # fall back to FFmpeg stream metadata and estimate the frame count from duration.
    if fps <= 0 or n <= 0 or fps > 1000:
        from .mediainfo import probe as ff_probe

        ff = ff_probe(path)
        if ff and ff.has_video:
            if (fps <= 0 or fps > 1000) and ff.fps:
                fps = ff.fps
            if n <= 0 and ff.duration_s and fps > 0:
                n = int(ff.duration_s * fps)
    dur = n / fps if fps > 0 else 0.0
    return {"fps": round(fps, 3), "frame_count": n, "width": w, "height": h, "duration_s": round(dur, 3)}


def analyze(path: str, evidence_id: str, artifact_dir: str, max_seconds: float = 600, max_thumbs: int = 240) -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    art = Path(artifact_dir)
    (art / "frames").mkdir(parents=True, exist_ok=True)
    with Timer() as t:
        try:
            info = probe(path)
        except Exception as e:  # noqa: BLE001
            out.status = "failed"
            out.errors.append(f"Video decoding failed: {type(e).__name__}")
            return out.to_dict()
        fps, n_frames = info["fps"], info["frame_count"]
        for k, v in info.items():
            out.observe(k, v)
        if fps <= 0 or n_frames <= 1:
            out.status = "failed"
            out.errors.append("Video has no readable frames")
            return out.to_dict()
        if info["duration_s"] > max_seconds:
            out.limitations.append(f"Only the first {max_seconds}s were analysed (duration limit).")
        last_frame = min(n_frames, int(max_seconds * fps))
        step = max(1, int(round(fps / ANALYSIS_FPS)))
        step = max(step, int(np.ceil(last_frame / MAX_ANALYSIS_FRAMES)))
        n_samples = last_frame // step
        thumb_every = max(1, int(np.ceil(n_samples / max_thumbs)))

        cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)  # never MSMF: it can block process exit on Windows
        det = _face_detector()
        rows: list[dict] = []
        prev_small = prev_hist = prev_mouth = None
        idx = sample_no = 0
        try:
            while idx < last_frame:
                if not cap.grab():
                    break
                if idx % step == 0:
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    h0, w0 = frame.shape[:2]
                    scale = 400.0 / w0
                    small = cv2.resize(frame, (400, max(1, int(h0 * scale))), interpolation=cv2.INTER_AREA)
                    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
                    cv2.normalize(hist, hist)
                    row = {"frame": idx, "t": round(idx / fps, 3),
                           "diff": float(np.mean(cv2.absdiff(gray, prev_small))) if prev_small is not None else 0.0,
                           "hist": float(cv2.compareHist(hist, prev_hist, cv2.HISTCMP_CORREL)) if prev_hist is not None else 1.0,
                           "sharp": float(cv2.Laplacian(gray, cv2.CV_64F).var()), "face": None, "mouth": None}
                    faces = det.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(40, 40))
                    if len(faces):
                        x, y, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                        face = gray[y: y + fh, x: x + fw]
                        row["face"] = [round(x / scale), round(y / scale), round(fw / scale), round(fh / scale)]
                        row["face_sharp"] = float(cv2.Laplacian(face, cv2.CV_64F).var())
                        mouth = gray[y + int(fh * 0.65): y + fh, x + int(fw * 0.25): x + int(fw * 0.75)]
                        mouth = cv2.resize(mouth, (48, 24)).astype(np.float32) if mouth.size else None
                        if mouth is not None and prev_mouth is not None:
                            row["mouth"] = float(np.mean(np.abs(mouth - prev_mouth)))
                        prev_mouth = mouth
                    else:
                        prev_mouth = None
                    if sample_no % thumb_every == 0:
                        th = cv2.resize(frame, (320, max(1, int(h0 * 320 / w0))), interpolation=cv2.INTER_AREA)
                        cv2.imwrite(str(art / "frames" / f"f{idx:07d}.jpg"), th, [cv2.IMWRITE_JPEG_QUALITY, 70])
                        row["thumb"] = f"frames/f{idx:07d}.jpg"
                    rows.append(row)
                    prev_small, prev_hist = gray, hist
                    sample_no += 1
                idx += 1
        finally:
            cap.release()

        out.observe("Frames analysed", len(rows))
        out.observe("Sampling interval", step, "frames")
        if len(rows) < 3:
            out.status = "failed"
            out.errors.append("Too few frames decoded")
            return out.to_dict()

        diffs = np.array([r["diff"] for r in rows[1:]])
        hists = np.array([r["hist"] for r in rows[1:]])
        z = robust_z(diffs)
        flagged: dict[int, list[str]] = {}

        # Abrupt changes
        abrupt = [i + 1 for i in np.where((z > 6) & (hists < 0.75) & (diffs > 8))[0]]
        for i in abrupt[:10]:
            r = rows[i]
            flagged.setdefault(i, []).append("abrupt_temporal_change")
            out.indicators.append(enrich(Indicator("frame", "abrupt_temporal_change", "low",
                f"Abrupt visual change at {r['t']:.2f}s (frame {r['frame']})", location={"frame": r["frame"], "t_start": rows[i - 1]["t"], "t_end": r["t"]},
                score=round(clamp01(float(z[i - 1]) / 20), 3))))
        out.signals.append(Signal("Temporal discontinuities", clamp01(len(abrupt) / 5), 0.6, "suspicious" if abrupt else "reassuring",
                                  "Scene cuts also cause this"))

        # Duplicated / frozen frames: a run of >=3 near-identical sampled frames while the frames immediately
        # on BOTH sides of the run are moving. Local context avoids both missing freezes in calm talking-head footage
        # and flagging genuinely static scenes.
        low = diffs < 0.15
        runs: list[tuple[int, int]] = []
        k = 0
        while k < low.size:
            if low[k]:
                e = k
                while e < low.size and low[e]:
                    e += 1
                left, right = diffs[max(0, k - 8):k], diffs[e:e + 8]
                moving = left.size >= 2 and right.size >= 2 and float(np.median(left)) >= 0.8 and float(np.median(right)) >= 0.8
                if e - k >= 3 and moving:
                    runs.append((k + 1, e))  # diffs[i] compares rows[i] and rows[i+1]
                k = e
            else:
                k += 1
        dups = [i for r0, r1 in runs for i in range(r0, r1 + 1)]
        for i in dups[:40]:
            flagged.setdefault(i, []).append("duplicated_frames")
        for r0, r1 in runs[:5]:
            out.indicators.append(enrich(Indicator("frame", "duplicated_frames", "low",
                f"Frozen picture: {r1 - r0 + 1} sampled frames are near-identical from {rows[r0]['t']:.2f}s to {rows[r1]['t']:.2f}s while the surrounding footage moves",
                location={"frames": [rows[i]["frame"] for i in range(r0, r1 + 1)][:20], "t_start": rows[r0]["t"], "t_end": rows[r1]["t"]},
                score=clamp01((r1 - r0 + 1) / 10))))
        out.signals.append(Signal("Duplicated frames", clamp01(len(dups) / 10), 0.4, "suspicious" if dups else "reassuring"))

        # Face sharpness consistency
        face_rows = [i for i, r in enumerate(rows) if r["face"] is not None]
        out.observe("Frames with a detected face", len(face_rows))
        if len(face_rows) >= 10:
            ratio = np.array([rows[i]["face_sharp"] / (rows[i]["sharp"] + 1e-6) for i in face_rows])
            fz = robust_z(np.log(ratio + 1e-6))
            bad = [face_rows[j] for j in np.where(np.abs(fz) > 4.5)[0]]
            if bad:
                for i in bad[:10]:
                    flagged.setdefault(i, []).append("face_region_inconsistency")
                out.indicators.append(enrich(Indicator("frame", "face_region_inconsistency", "medium" if len(bad) >= 5 else "low",
                    f"Face-region sharpness deviates from the rest of the frame in {len(bad)} frame(s) (first at {rows[bad[0]]['t']:.2f}s)",
                    location={"frames": [rows[i]["frame"] for i in bad[:20]], "t_start": rows[bad[0]]["t"], "t_end": rows[bad[-1]]["t"], "bbox": rows[bad[0]]["face"]},
                    score=clamp01(len(bad) / 10))))
            out.signals.append(Signal("Face-region inconsistency", clamp01(len(bad) / 10), 0.9, "suspicious" if bad else "reassuring"))
        else:
            out.limitations.append("Fewer than 10 frames with a detectable frontal face – face consistency checks skipped.")

        # Frame timeline for the inspector / heatmap (measured values only)
        dz = np.concatenate([[0.0], np.clip(z, 0, None)])
        frames_out = []
        for i, r in enumerate(rows):
            frames_out.append({"frame": r["frame"], "t": r["t"], "diff": round(r["diff"], 3), "hist": round(r["hist"], 3),
                               "face": r["face"], "mouth": None if r["mouth"] is None else round(r["mouth"], 3),
                               "thumb": r.get("thumb"), "anomaly": round(float(min(1.0, dz[i] / 12)), 3), "flags": flagged.get(i, [])})
        out.artifacts = {"frames": frames_out, "fps": fps, "step": step, "duration_s": info["duration_s"], "width": info["width"], "height": info["height"],
                         "heatmap_basis": "Per-frame temporal anomaly = robust z-score of inter-frame difference (measured)"}

        # Audio track
        ok, msg = extract_audio(path, art / "audio_track.wav")
        out.artifacts["audio_track"] = "audio_track.wav" if ok else None
        out.observe("Embedded audio track", "extracted" if ok else msg)

        out.limitations.append("Learned (pre-trained) AI-generation/deepfake models are not part of this classical agent; they run separately as the 'Learned AI/Deepfake Classifier' agent, whose result and validation status are reported there.")
        out.suspicion_score = aggregate_signals(out.signals)
        out.confidence = 0.5 if face_rows else 0.35
        out.limitations += ["Haar-cascade face detection only finds near-frontal faces.",
                            f"Frames sampled every {step} frame(s); very short manipulations between samples can be missed.",
                            "Classical temporal cues cannot detect high-quality, temporally consistent deepfakes on their own."]
        out.interpretation = (f"{len(out.indicators)} temporal/visual indicator(s) flagged at specific timestamps for frame review." if out.indicators
                              else "No temporal discontinuities or face-region inconsistencies detected by classical cues.")
    out.duration_ms = t.ms
    return out.to_dict()

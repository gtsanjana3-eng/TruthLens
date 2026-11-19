"""Voice-Face Synchronization Analyzer.

Correlates the mouth-region motion series (from the video agent) with the speech energy
envelope of the audio track. Low correlation where speech is present and a face is visible
is flagged for human review; it is NOT proof of lip-sync manipulation."""
from __future__ import annotations

import numpy as np

from .audio_agent import TARGET_SR, frame_rms, load_audio, speech_activity
from .base import AgentOutput, Indicator, Signal, Timer, clamp01
from .explanations import enrich

NAME = "Video Analysis Agent"
VERSION = "1.0.0"


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 5 or a.std() < 1e-9 or b.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def analyze(frames: list[dict], audio_path: str, evidence_id: str, window_s: float = 4.0) -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    with Timer() as t:
        valid = [f for f in frames if f.get("mouth") is not None]
        out.observe("Frames with mouth tracking", len(valid))
        if len(valid) < 20:
            out.status = "skipped"
            out.interpretation = "Insufficient visible-face frames for synchronization analysis (inconclusive)."
            out.limitations.append("Needs at least 20 frames with a detectable frontal face.")
            return out.to_dict()
        try:
            x, _, _ = load_audio(audio_path)
        except Exception as e:  # noqa: BLE001
            out.status = "failed"
            out.errors.append(f"Audio load failed: {type(e).__name__}")
            return out.to_dict()
        rms, times = frame_rms(x, TARGET_SR, hop_s=0.01, win_s=0.03)
        if rms.size == 0:
            out.status = "skipped"
            out.interpretation = "Audio track too short."
            return out.to_dict()
        active = speech_activity(rms)
        env = np.where(active, rms, 0.0)
        # Speech-energy *change* aligns better with mouth motion than raw energy.
        denv = np.abs(np.diff(np.concatenate([[env[0]], env])))
        ft = np.array([f["t"] for f in valid])
        mouth = np.array([f["mouth"] for f in valid])
        dt = float(np.median(np.diff(ft))) if ft.size > 1 else 0.1

        def env_at(shift: float) -> np.ndarray:
            return np.interp(ft + shift, times, np.convolve(denv, np.ones(5) / 5, "same"), left=0, right=0)

        lags = np.arange(-0.5, 0.501, max(0.02, dt / 2))
        scores = [(_corr(mouth, env_at(l)), l) for l in lags]
        scores = [(c, l) for c, l in scores if not np.isnan(c)]
        if not scores:
            out.status = "skipped"
            out.interpretation = "No speech energy variation overlapping visible-face frames (inconclusive)."
            return out.to_dict()
        best_c, best_lag = max(scores)
        out.observe("Global mouth/speech correlation", round(best_c, 3))
        out.observe("Best audio offset", round(best_lag, 3), "s")
        speech_at = np.interp(ft, times, active.astype(float)) > 0.5
        out.observe("Face-visible frames with speech", int(speech_at.sum()))

        windows = []
        start = ft[0]
        while start + window_s <= ft[-1] + 1e-6:
            m = (ft >= start) & (ft < start + window_s)
            if m.sum() >= 8 and speech_at[m].mean() >= 0.4:
                c = _corr(mouth[m], env_at(best_lag)[m])
                windows.append({"t_start": round(float(start), 2), "t_end": round(float(start + window_s), 2), "corr": None if np.isnan(c) else round(c, 3)})
            start += window_s / 2
        suspicious = [w for w in windows if w["corr"] is not None and w["corr"] < 0.0]
        for w in suspicious[:8]:
            out.indicators.append(enrich(Indicator("sync", "lip_sync_mismatch", "low",
                f"Mouth motion does not follow speech energy between {w['t_start']:.1f}s and {w['t_end']:.1f}s (r={w['corr']:.2f})",
                location={"t_start": w["t_start"], "t_end": w["t_end"]}, score=round(clamp01(-w["corr"] + 0.3), 3))))
        if abs(best_lag) >= 0.3:
            out.indicators.append(enrich(Indicator("sync", "lip_sync_mismatch", "low",
                f"Best alignment requires shifting audio by {best_lag:+.2f}s", location={"offset_s": round(best_lag, 3)}, score=0.4)))
        ratio = len(suspicious) / len(windows) if windows else 0.0
        out.signals.append(Signal("Out-of-sync speech windows", round(ratio, 3), 0.8, "suspicious" if suspicious else "reassuring"))
        out.signals.append(Signal("Global sync correlation", round(clamp01(0.3 - best_c), 3), 0.5, "suspicious" if best_c < 0.1 else "reassuring"))
        out.suspicion_score = round(0.6 * ratio + 0.4 * clamp01(0.3 - best_c), 4) if windows else None
        out.confidence = 0.4 if len(windows) >= 3 else 0.2
        out.artifacts = {"windows": windows, "best_lag_s": round(best_lag, 3), "global_corr": round(best_c, 3)}
        out.limitations += ["Mouth motion is a coarse pixel-difference proxy, not a learned lip-reading model.",
                            "Off-camera speakers, voice-overs, profile views and occlusions produce false mismatches.",
                            "Correlation values are uncalibrated indicators."]
        if not windows:
            out.interpretation = "No windows with simultaneous speech and visible face – synchronization inconclusive."
        else:
            out.interpretation = (f"{len(suspicious)} of {len(windows)} speech windows show weak mouth/speech correspondence; review those timestamps."
                                  if suspicious else f"Mouth motion broadly follows speech energy across {len(windows)} windows (r={best_c:.2f}).")
    out.duration_ms = t.ms
    return out.to_dict()

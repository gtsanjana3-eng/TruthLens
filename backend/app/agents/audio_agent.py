"""Audio & Voice Forensics Agent.

Explainable signal analysis with NumPy/SciPy:
  * splice discontinuities (sample-level jumps far outside local statistics)
  * background noise-floor shifts between adjacent windows
  * digital-silence runs inside active audio (exact zeros typical of edits)
  * bandwidth cutoff (band-limited / upsampled audio)
  * pitch variability & jitter of voiced frames (very flat prosody is a weak synthetic-speech cue)
  * clipping
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from scipy import signal as sps

from .base import AgentOutput, Indicator, ModelCheck, Signal, Timer, aggregate_signals, clamp01, robust_z
from .explanations import enrich

NAME = "Audio Analysis Agent"
VERSION = "1.1.1"
TARGET_SR = 16000


def load_audio(path: str | Path, max_seconds: float | None = None) -> tuple[np.ndarray, int, int]:
    """Return mono float32 audio resampled to 16 kHz, the native rate, and channel count."""
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        native_sr, channels = f.samplerate, f.channels
        frames = int(max_seconds * native_sr) if max_seconds else -1
        data = f.read(frames=frames, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if native_sr != TARGET_SR and mono.size:
        g = np.gcd(native_sr, TARGET_SR)
        mono = sps.resample_poly(mono, TARGET_SR // g, native_sr // g).astype(np.float32)
    return mono, native_sr, channels


def native_bandwidth(path: str | Path, seconds: float = 30) -> tuple[float, int]:
    """Highest frequency with meaningful energy in the native-rate signal."""
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        sr = f.samplerate
        x = f.read(frames=int(seconds * sr), dtype="float32", always_2d=True).mean(axis=1)
    if x.size < 2048:
        return float(sr / 2), sr
    freqs, psd = sps.welch(x, fs=sr, nperseg=4096)
    db = 10 * np.log10(psd + 1e-20)
    ref = np.percentile(db[freqs < 4000], 90) if np.any(freqs < 4000) else db.max()
    above = freqs[db > ref - 70]
    return float(above.max() if above.size else 0.0), sr


def frame_rms(x: np.ndarray, sr: int, hop_s: float = 0.02, win_s: float = 0.04) -> tuple[np.ndarray, np.ndarray]:
    hop, win = int(sr * hop_s), int(sr * win_s)
    if x.size < win:
        return np.array([]), np.array([])
    n = 1 + (x.size - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt((x[idx] ** 2).mean(axis=1) + 1e-12)
    times = (np.arange(n) * hop + win / 2) / sr
    return rms, times


def speech_activity(rms: np.ndarray) -> np.ndarray:
    if rms.size == 0:
        return rms.astype(bool)
    db = 20 * np.log10(rms + 1e-9)
    floor = np.percentile(db, 10)
    return db > max(floor + 12, db.max() - 45)


def pitch_track(x: np.ndarray, sr: int, active: np.ndarray, times: np.ndarray) -> np.ndarray:
    """Autocorrelation F0 for active frames (60–400 Hz). Returns array of F0 (nan when unvoiced)."""
    win = int(sr * 0.04)
    lo, hi = int(sr / 400), int(sr / 60)
    f0 = np.full(times.size, np.nan)
    for i in np.where(active)[0][::2]:
        c = int(times[i] * sr)
        seg = x[max(0, c - win // 2): c + win // 2]
        if seg.size < hi + 2:
            continue
        seg = seg - seg.mean()
        ac = np.correlate(seg, seg, "full")[seg.size - 1:]
        if ac[0] <= 0:
            continue
        ac = ac / ac[0]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] > 0.45:
            f0[i] = sr / k
    return f0


def analyze(path: str, evidence_id: str, artifact_dir: str, max_seconds: float = 900) -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    art = Path(artifact_dir)
    art.mkdir(parents=True, exist_ok=True)
    with Timer() as t:
        try:
            x, native_sr, channels = load_audio(path, max_seconds)
        except Exception as e:  # noqa: BLE001
            out.status = "failed"
            out.errors.append(f"Audio decoding failed: {type(e).__name__}")
            return out.to_dict()
        sr = TARGET_SR
        dur = x.size / sr
        out.observe("Duration", round(dur, 2), "s")
        out.observe("Native sample rate", native_sr, "Hz")
        out.observe("Channels", channels)
        if dur < 1.0:
            out.status = "failed"
            out.errors.append("Audio shorter than 1 second – insufficient for analysis")
            return out.to_dict()

        rms, times = frame_rms(x, sr)
        active = speech_activity(rms)
        out.observe("Active (voiced/sound) ratio", round(float(active.mean()), 3))
        timeline = np.zeros(int(np.ceil(dur)), dtype=np.float32)  # per-second suspicion from measured anomalies

        # Clipping
        clip = float(np.mean(np.abs(x) > 0.99))
        out.observe("Clipped samples", f"{clip * 100:.3f}%")
        if clip > 0.001:
            out.indicators.append(enrich(Indicator("segment", "clipping", "info", f"{clip * 100:.2f}% of samples clipped", location={"scope": "whole_file"})))

        # Splice discontinuities: derivative outliers relative to local energy
        d = np.abs(np.diff(x))
        local = sps.medfilt(d[:: 8], 101) if d.size > 808 else np.full(d[::8].shape, d.mean())
        local = np.repeat(local, 8)[: d.size] + 1e-4
        ratio = d / local
        edge = int(0.1 * sr)  # file boundaries are not splices (and the median filter is unreliable there)
        cand = np.where(ratio > 60)[0]
        cand = cand[(cand > edge) & (cand < d.size - edge)]
        splices: list[float] = []
        for c in cand:
            ts = c / sr
            if not splices or ts - splices[-1] > 0.25:
                splices.append(ts)
        splices = splices[:20]
        for ts in splices[:8]:
            out.indicators.append(enrich(Indicator("segment", "audio_splice_discontinuity", "low",
                f"Abrupt waveform discontinuity at {ts:.2f}s", location={"t_start": round(max(0, ts - 0.05), 3), "t_end": round(ts + 0.05, 3)}, score=0.5)))
            timeline[min(int(ts), timeline.size - 1)] = max(timeline[min(int(ts), timeline.size - 1)], 0.5)
        out.observe("Waveform discontinuities", len(splices))
        out.signals.append(Signal("Waveform discontinuities", clamp01(len(splices) / 6), 0.8, "suspicious" if splices else "reassuring"))

        # Noise floor shifts (1 s windows, 10th percentile RMS)
        win = 50  # 50 * 20ms = 1 s
        floors = np.array([np.percentile(rms[i: i + win], 10) for i in range(0, rms.size - win + 1, win)])
        shifts = []
        if floors.size >= 4:
            fdb = 20 * np.log10(floors + 1e-9)
            jumps = np.abs(np.diff(fdb))
            z = robust_z(jumps)
            for i in np.where((z > 5) & (jumps > 8))[0]:
                shifts.append((i + 1, float(jumps[i])))
        for sec, jump in shifts[:6]:
            out.indicators.append(enrich(Indicator("segment", "noise_floor_shift", "low",
                f"Background noise level changes by {jump:.1f} dB at ~{sec}s", location={"t_start": float(sec) - 0.5, "t_end": float(sec) + 0.5}, score=clamp01(jump / 30))))
            timeline[min(sec, timeline.size - 1)] = max(timeline[min(sec, timeline.size - 1)], clamp01(jump / 30))
        out.signals.append(Signal("Noise-floor shifts", clamp01(len(shifts) / 3), 0.7, "suspicious" if shifts else "reassuring"))

        # Digital silence runs (exact zeros) inside active regions
        zero = (x == 0).astype(np.int8)
        runs = []
        if zero.any():
            edges = np.diff(np.concatenate([[0], zero, [0]]))
            starts, ends = np.where(edges == 1)[0], np.where(edges == -1)[0]
            for s, e in zip(starts, ends):
                if (e - s) / sr >= 0.05 and 0.5 < s / sr < dur - 0.5:
                    runs.append((s / sr, e / sr))
        for s, e in runs[:5]:
            out.indicators.append(enrich(Indicator("segment", "digital_silence", "low",
                f"Exact digital silence {s:.2f}–{e:.2f}s inside the recording", location={"t_start": round(s, 3), "t_end": round(e, 3)}, score=0.45)))
            timeline[min(int(s), timeline.size - 1)] = max(timeline[min(int(s), timeline.size - 1)], 0.45)
        out.signals.append(Signal("Digital-silence insertions", clamp01(len(runs) / 3), 0.6, "suspicious" if runs else "reassuring"))

        # Bandwidth
        try:
            bw, nsr = native_bandwidth(path)
            out.observe("Effective bandwidth", round(bw), "Hz")
            if nsr >= 32000 and bw < 0.4 * nsr / 2 and bw < 9000:
                out.indicators.append(enrich(Indicator("segment", "band_limited_audio", "info",
                    f"Energy stops near {bw:.0f} Hz although file is sampled at {nsr} Hz (upsampled or narrow-band source)", location={"scope": "whole_file"}, score=0.3)))
                out.signals.append(Signal("Band-limited content", 0.3, 0.3, "suspicious", "Also typical of phone/VoIP audio"))
        except Exception as e:  # noqa: BLE001
            out.errors.append(f"Bandwidth estimate failed: {type(e).__name__}")

        # Pitch variability
        f0 = pitch_track(x, sr, active, times)
        voiced = f0[~np.isnan(f0)]
        out.observe("Voiced frames analysed", int(voiced.size))
        if voiced.size >= 30:
            semis = 12 * np.log2(voiced / np.median(voiced))
            spread = float(np.std(semis))
            jitter = float(np.median(np.abs(np.diff(voiced)) / voiced[:-1]))
            out.observe("Pitch spread", round(spread, 2), "semitones")
            out.observe("Frame-to-frame pitch jitter", round(jitter, 4))
            if spread < 1.0:
                out.indicators.append(enrich(Indicator("segment", "low_pitch_variability", "low",
                    f"Unusually flat intonation (pitch spread {spread:.2f} semitones)", location={"scope": "voiced_frames"}, score=0.4)))
                out.signals.append(Signal("Flat prosody", clamp01((1.0 - spread)), 0.5, "suspicious", "Weak cue; monotone speakers exist"))
            else:
                out.signals.append(Signal("Natural pitch variation", 0.0, 0.3, "reassuring"))
        else:
            out.limitations.append("Too few voiced frames for prosody analysis.")

        # Spectrogram visual (not a suspicion map)
        try:
            f, tt, S = sps.spectrogram(x[: sr * 120], fs=sr, nperseg=512, noverlap=384)
            img = 10 * np.log10(S + 1e-12)
            img = np.clip((img - img.max() + 80) / 80, 0, 1)
            img = cv2.resize((img[::-1] * 255).astype(np.uint8), (min(1600, max(400, tt.size)), 256))
            cv2.imwrite(str(art / "spectrogram.png"), cv2.applyColorMap(img, cv2.COLORMAP_MAGMA))
            out.artifacts["spectrogram"] = "spectrogram.png"
        except Exception as e:  # noqa: BLE001
            out.errors.append(f"Spectrogram failed: {type(e).__name__}")

        step = max(1, rms.size // 600)
        out.artifacts.update({
            "waveform": {"t": [round(float(v), 2) for v in times[::step]], "rms": [round(float(v), 4) for v in rms[::step]]},
            "timeline": {"resolution_s": 1, "values": [round(float(v), 3) for v in timeline],
                         "basis": "Per-second maximum of measured anomalies (discontinuities, noise shifts, silence runs)"},
            "duration_s": round(dur, 3),
        })
        out.model_checks.append(ModelCheck("Learned anti-spoofing / voice-clone classifier", "unavailable",
                                           "No validated speech anti-spoofing model is bundled; configure one to enable."))
        out.suspicion_score = aggregate_signals(out.signals)
        out.confidence = 0.55 if dur > 5 else 0.35
        out.limitations += ["Signal heuristics cannot reliably detect high-quality voice cloning on their own.",
                            "Compression, noise suppression and phone codecs can mimic or mask edit artefacts.",
                            "Analysis performed at 16 kHz mono (bandwidth measured at native rate)."]
        out.interpretation = (f"{len(out.indicators)} audio observation(s) flagged for listening review." if out.indicators
                              else "No edit artefacts detected by signal heuristics; synthetic speech cannot be ruled out.")
    out.duration_ms = t.ms
    return out.to_dict()

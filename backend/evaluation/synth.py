"""Deterministic synthetic media generator for tests and the evaluation dataset.

IMPORTANT: these are *synthetic* genuine/manipulated pairs created with known, documented
operations (copy-move, splicing, frame insertion, audio splicing...). They measure whether
each pipeline detects those specific operations; they are NOT a substitute for real-world
deepfake benchmarks (see evaluation/README.md)."""
from __future__ import annotations

import wave
from pathlib import Path

import cv2
import numpy as np


def _scene(rng: np.random.Generator, h: int = 480, w: int = 640) -> np.ndarray:
    """Photo-like scene: smooth illumination, textured shapes and sensor-like noise."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    base = np.stack([80 + 60 * np.sin(xx / (90 + rng.integers(0, 40))) + yy * 0.1,
                     100 + 50 * np.cos(yy / (70 + rng.integers(0, 40))),
                     120 + 40 * np.sin((xx + yy) / 150)], axis=2)
    img = base.copy()
    for _ in range(int(rng.integers(6, 12))):
        c = tuple(int(v) for v in rng.integers(20, 235, 3))
        x, y = int(rng.integers(0, w - 60)), int(rng.integers(0, h - 60))
        if rng.random() < 0.5:
            cv2.circle(img, (x + 30, y + 30), int(rng.integers(15, 60)), c, -1)
        else:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(30, 120)), y + int(rng.integers(30, 120))), c, -1)
    for _ in range(int(h * w / 600)):  # fine structure (foliage/gravel-like) so keypoint methods have texture
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        if rng.random() < 0.6:
            cv2.circle(img, (x, y), int(rng.integers(1, 4)), c, -1)
        else:
            cv2.line(img, (x, y), (x + int(rng.integers(-8, 9)), y + int(rng.integers(-8, 9))), c, 1)
    tex = cv2.GaussianBlur(rng.normal(0, 18, (h, w, 3)).astype(np.float32), (0, 0), 1.2)
    img = img + tex + rng.normal(0, 3.0, (h, w, 3))
    img = cv2.GaussianBlur(img, (3, 3), 0.6)
    return np.clip(img, 0, 255).astype(np.uint8)


def write_jpeg(path: Path, img: np.ndarray, q: int = 92) -> None:
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    path.write_bytes(enc.tobytes())


def make_image_pair(out_dir: Path, seed: int, kind: str) -> tuple[Path, Path]:
    """Return (genuine, manipulated). kind: copy_move | splice"""
    rng = np.random.default_rng(seed)
    img = _scene(rng)
    g = out_dir / f"img_{seed}_genuine.jpg"
    write_jpeg(g, img)
    m_img = cv2.imdecode(np.fromfile(str(g), np.uint8), cv2.IMREAD_COLOR)
    h, w = m_img.shape[:2]
    if kind == "copy_move":
        sx, sy = int(rng.integers(20, w // 2 - 140)), int(rng.integers(20, h - 160))
        patch = m_img[sy: sy + 130, sx: sx + 130].copy()
        dx, dy = int(rng.integers(w // 2, w - 150)), int(rng.integers(10, h - 150))
        m_img[dy: dy + 130, dx: dx + 130] = patch
    else:  # splice: paste a heavily compressed, differently-noised foreign region
        other = _scene(np.random.default_rng(seed + 10_000))
        ok, enc = cv2.imencode(".jpg", other, [cv2.IMWRITE_JPEG_QUALITY, 35])
        other = cv2.imdecode(enc, cv2.IMREAD_COLOR)
        other = np.clip(other.astype(np.float32) + np.random.default_rng(seed).normal(0, 9, other.shape), 0, 255).astype(np.uint8)
        x, y = int(rng.integers(20, w - 220)), int(rng.integers(20, h - 200))
        m_img[y: y + 180, x: x + 200] = other[y: y + 180, x: x + 200]
    m = out_dir / f"img_{seed}_{kind}.jpg"
    write_jpeg(m, m_img)
    return g, m


def _speech_like(rng: np.random.Generator, seconds: float, sr: int, noise_db: float = -45) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    syll = 0.5 * (1 + np.sin(2 * np.pi * rng.uniform(3, 5) * t + rng.uniform(0, 6)))
    phrase = 1 / (1 + np.exp(-12 * (np.sin(2 * np.pi * 0.25 * t + rng.uniform(0, 6)) + 0.6)))  # smooth pauses
    env = np.clip(syll, 0, 1) ** 2 * phrase
    f0 = rng.uniform(110, 190) * (1 + 0.12 * np.sin(2 * np.pi * 0.7 * t) + 0.05 * np.sin(2 * np.pi * 2.3 * t))
    phase = 2 * np.pi * np.cumsum(f0) / sr
    voice = sum((0.6 / k) * np.sin(k * phase) for k in range(1, 8))
    x = 0.3 * env * voice + (10 ** (noise_db / 20)) * rng.normal(0, 1, t.size)
    return x.astype(np.float32)


def write_wav(path: Path, x: np.ndarray, sr: int = 16000) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def make_audio_pair(out_dir: Path, seed: int, sr: int = 16000) -> tuple[Path, Path]:
    rng = np.random.default_rng(seed)
    genuine = _speech_like(rng, 12, sr)
    g = out_dir / f"aud_{seed}_genuine.wav"
    write_wav(g, genuine, sr)
    # Splice: segment from a different recording with a different noise floor, hard cuts, digital silence.
    other = _speech_like(np.random.default_rng(seed + 5000), 12, sr, noise_db=-22)
    a, b = int(4 * sr), int(8 * sr)
    m = genuine.copy()
    m[a:b] = other[a:b] + 0.25
    m[b: b + int(0.12 * sr)] = 0.0
    p = out_dir / f"aud_{seed}_spliced.wav"
    write_wav(p, m, sr)
    return g, p


def make_video_pair(out_dir: Path, seed: int, seconds: int = 6, fps: int = 15) -> tuple[Path, Path]:
    rng = np.random.default_rng(seed)
    bg = _scene(rng, 240, 320)
    frames = []
    for i in range(seconds * fps):
        f = np.roll(bg, i * 2, axis=1).copy()
        cx = int(60 + 200 * (i / (seconds * fps)))
        cv2.circle(f, (cx, 120), 25, (40, 200, 240), -1)
        frames.append(f)
    def write(path: Path, fr: list[np.ndarray]) -> None:
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (320, 240))
        for f in fr:
            vw.write(f)
        vw.release()
    g = out_dir / f"vid_{seed}_genuine.mp4"
    write(g, frames)
    # Manipulation: insert foreign frames (abrupt splice) and frozen duplicates
    foreign = _scene(np.random.default_rng(seed + 7000), 240, 320)
    mf = list(frames)
    k = len(mf) // 2
    mf[k: k + 3] = [foreign] * 3
    mf[k + 20: k + 26] = [mf[k + 20]] * 6
    m = out_dir / f"vid_{seed}_spliced.mp4"
    write(m, mf)
    return g, m

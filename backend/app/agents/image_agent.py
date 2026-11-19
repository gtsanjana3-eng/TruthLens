"""Image Forensics Agent.

Classical, explainable signal analysis (no hidden model):
  1. Error Level Analysis (ELA) – contrast-normalised block recompression error outliers.
  2. Noise consistency – contiguous blocks whose robust noise sigma differs from the image.
  3. Copy-move detection – clusters of SIFT self-matches sharing one displacement.
  4. Frequency-spectrum periodicity – peaks typical of resampling / generator upsampling.
The suspicion heatmap is only built from measured block maps (1 & 2) and copy-move boxes.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .base import AgentOutput, Indicator, Signal, Timer, aggregate_signals, clamp01, robust_z
from .explanations import enrich

NAME = "Image Forensics Agent"
VERSION = "1.3.1"
MAX_SIDE = 1600  # analysis resolution cap (original evidence untouched)
BLOCK = 16


def _load(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)  # handles non-ASCII paths on Windows
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Image could not be decoded")
    h, w = img.shape[:2]
    scale = min(1.0, MAX_SIDE / max(h, w))
    if scale < 1.0:
        img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return img


def _block_mean(m: np.ndarray, block: int = BLOCK) -> np.ndarray:
    h, w = m.shape[:2]
    hb, wb = h // block, w // block
    m = m[: hb * block, : wb * block]
    return m.reshape(hb, block, wb, block).mean(axis=(1, 3))


def _regions_from_mask(mask: np.ndarray, scale_y: float, scale_x: float, min_blocks: int = 3) -> list[dict]:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    regions = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_blocks:
            continue
        regions.append({"x": int(x * scale_x), "y": int(y * scale_y), "w": int(w * scale_x), "h": int(h * scale_y), "blocks": int(area)})
    regions.sort(key=lambda r: -r["blocks"])
    return regions[:5]


def error_level_analysis(img: np.ndarray, quality: int = 90) -> np.ndarray:
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    rec = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return np.abs(img.astype(np.int16) - rec.astype(np.int16)).astype(np.float32).mean(axis=2)


NOISE_BLOCK = 32


def noise_sigma_map(img: np.ndarray) -> np.ndarray:
    """Per-block robust noise estimate (MAD of a second-order diagonal residual)."""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    r = g[1:, 1:] - g[:-1, 1:] - g[1:, :-1] + g[:-1, :-1]
    h, w = r.shape
    hb, wb = h // NOISE_BLOCK, w // NOISE_BLOCK
    if hb < 2 or wb < 2:
        return np.zeros((max(hb, 1), max(wb, 1)), np.float32)
    bl = r[: hb * NOISE_BLOCK, : wb * NOISE_BLOCK].reshape(hb, NOISE_BLOCK, wb, NOISE_BLOCK).transpose(0, 2, 1, 3).reshape(hb, wb, -1)
    return np.median(np.abs(bl), axis=2) / 0.6745


def copy_move(img: np.ndarray) -> tuple[list[dict], int]:
    """SIFT self-matching with a ratio test; true clones share a single displacement vector."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sift = cv2.SIFT_create(nfeatures=4000)
    kps, des = sift.detectAndCompute(gray, None)
    if des is None or len(kps) < 20:
        return [], 0
    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(des, des, k=3)
    min_dist = max(30.0, max(img.shape[:2]) * 0.03)
    q = 6
    bins: dict[tuple[int, int], list] = {}
    n_pairs = 0
    for ms in matches:
        if len(ms) < 3:
            continue
        a, b = ms[1], ms[2]  # ms[0] is the keypoint matching itself
        p1, p2 = np.array(kps[a.queryIdx].pt), np.array(kps[a.trainIdx].pt)
        if np.linalg.norm(p1 - p2) < min_dist or a.distance >= 0.5 * b.distance:
            continue
        n_pairs += 1
        d = p2 - p1
        if d[0] < 0 or (d[0] == 0 and d[1] < 0):  # canonical direction so A->B and B->A merge
            p1, p2, d = p2, p1, -d
        bins.setdefault((int(round(d[0] / q)), int(round(d[1] / q))), []).append((p1, p2))
    clusters = []
    for key in bins:
        # merge neighbouring offset bins (quantisation tolerance)
        allm = [m for dk in ((0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)) for m in bins.get((key[0] + dk[0], key[1] + dk[1]), [])]
        if len(allm) < 6:
            continue
        src = np.array([m[0] for m in allm])
        dst = np.array([m[1] for m in allm])

        def box(a):
            x0, y0 = a.min(axis=0)
            x1, y1 = a.max(axis=0)
            return {"x": int(x0), "y": int(y0), "w": int(max(8, x1 - x0)), "h": int(max(8, y1 - y0))}

        clusters.append({"source": box(src), "target": box(dst), "matches": len(allm), "offset": [int(key[0] * q), int(key[1] * q)]})
    clusters.sort(key=lambda c: -c["matches"])
    dedup: list[dict] = []
    for c in clusters:
        if all(abs(c["offset"][0] - d["offset"][0]) > q * 2 or abs(c["offset"][1] - d["offset"][1]) > q * 2 for d in dedup):
            dedup.append(c)
    return dedup[:3], n_pairs


def spectral_periodicity(img: np.ndarray) -> float:
    """Ratio of strongest isolated high-frequency peak to local spectral background."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = gray[: 512, : 512] if min(gray.shape) >= 512 else gray
    gray = gray - cv2.GaussianBlur(gray, (0, 0), 3)
    f = np.fft.fftshift(np.abs(np.fft.fft2(gray * np.outer(np.hanning(gray.shape[0]), np.hanning(gray.shape[1])))))
    logf = np.log1p(f)
    h, w = logf.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    hf = r > min(h, w) * 0.15
    bg = cv2.medianBlur(logf.astype(np.float32), 5)
    excess = (logf - bg)[hf]
    if excess.size == 0:
        return 0.0
    return float(np.percentile(excess, 99.95) / (excess.std() + 1e-6))


def analyze(path: str, evidence_id: str, artifact_dir: str) -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    art = Path(artifact_dir)
    art.mkdir(parents=True, exist_ok=True)
    with Timer() as t:
        try:
            img = _load(Path(path))
        except Exception as e:  # noqa: BLE001 - untrusted media: report, never crash
            out.status = "failed"
            out.errors.append(f"Decoding failed: {type(e).__name__}")
            return out.to_dict()
        h, w = img.shape[:2]
        is_jpeg = Path(path).suffix.lower() in (".jpg", ".jpeg")
        out.observe("Analysis resolution", f"{w}x{h}")
        out.observe("Compression", "JPEG (lossy)" if is_jpeg else "Lossless / other")

        # 1. ELA (normalised by local block contrast so edges/texture are not flagged by default)
        ela = error_level_analysis(img)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        ela_blocks = _block_mean(ela)
        sd = np.sqrt(np.maximum(_block_mean(gray ** 2) - _block_mean(gray) ** 2, 0))
        z_ela = robust_z(np.log(ela_blocks / (sd + 4) + 1e-3).ravel()).reshape(ela_blocks.shape)
        ela_mask = z_ela > 6.0
        out.observe("ELA mean error", round(float(ela.mean()), 3))
        out.observe("ELA outlier blocks", f"{int(ela_mask.sum())} of {ela_mask.size}")
        ela_regions = _regions_from_mask(ela_mask, BLOCK, BLOCK, min_blocks=12)
        ela_strength = clamp01(ela_regions[0]["blocks"] / 40) if ela_regions else 0.0
        out.signals.append(Signal("ELA localized outliers", round(ela_strength, 3), 0.6 if is_jpeg else 0.2,
                                  "suspicious" if ela_regions else "reassuring",
                                  "Contiguous blocks recompress very differently from similar-contrast areas" if ela_regions else "No large contiguous ELA outlier region"))
        for r in ela_regions[:2]:
            out.indicators.append(enrich(Indicator("region", "ela_outlier_region", "low",
                f"Region ({r['x']},{r['y']}) {r['w']}x{r['h']}px shows outlying JPEG error level", location={**r, "space": "analysis_px", "image_w": w, "image_h": h}, score=round(ela_strength, 3))))

        # 2. Noise consistency (block noise sigma, contiguous outlier clusters only)
        sigma = noise_sigma_map(img)
        z_noise = robust_z(np.log(sigma.ravel() + 0.5)).reshape(sigma.shape)
        noise_mask = np.abs(z_noise) > 2.5
        noise_regions = _regions_from_mask(noise_mask, NOISE_BLOCK, NOISE_BLOCK, min_blocks=4)
        noise_strength = clamp01(noise_regions[0]["blocks"] / 20) if noise_regions else 0.0
        out.observe("Median noise sigma", round(float(np.median(sigma)), 2))
        out.observe("Noise-inconsistent regions", len(noise_regions))
        out.signals.append(Signal("Noise level inconsistency", round(noise_strength, 3), 1.0,
                                  "suspicious" if noise_regions else "reassuring"))
        for r in noise_regions[:2]:
            out.indicators.append(enrich(Indicator("region", "noise_inconsistency", "medium" if noise_strength >= 0.5 else "low",
                f"Region ({r['x']},{r['y']}) {r['w']}x{r['h']}px has a noise level inconsistent with the rest of the image",
                location={**r, "space": "analysis_px", "image_w": w, "image_h": h}, score=round(noise_strength, 3))))

        # 3. Copy-move
        clusters, n_pairs = copy_move(img)
        out.observe("Self-similar keypoint pairs", n_pairs)
        cm_strength = clamp01(clusters[0]["matches"] / 20) if clusters else 0.0
        out.signals.append(Signal("Copy-move clusters", round(cm_strength, 3), 0.9, "suspicious" if clusters else "reassuring"))
        for c in clusters:
            out.indicators.append(enrich(Indicator("region", "copy_move_cluster", "medium" if c["matches"] >= 10 else "low",
                f"{c['matches']} keypoints repeated with a consistent offset {c['offset']} (possible cloned area)",
                location={**c["target"], "source": c["source"], "space": "analysis_px", "image_w": w, "image_h": h}, score=round(cm_strength, 3))))

        # 4. Spectrum
        per = spectral_periodicity(img)
        per_strength = clamp01((per - 8) / 12)
        out.observe("Spectral peak ratio", round(per, 2))
        out.signals.append(Signal("High-frequency spectral peaks", round(per_strength, 3), 0.4, "suspicious" if per_strength > 0.3 else "neutral",
                                  "Weak signal – also caused by resizing and JPEG grids"))
        if per_strength > 0.5:
            out.indicators.append(enrich(Indicator("region", "spectral_periodicity", "low",
                "Isolated periodic peaks in the frequency spectrum (whole image)", location={"scope": "whole_image"}, score=round(per_strength, 3))))

        # Heatmap from measured maps only
        heat_ela = cv2.resize((np.clip(z_ela / 10, 0, 1) * ela_mask).astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST) * (1.0 if is_jpeg else 0.5)
        heat_noise = cv2.resize((np.clip(np.abs(z_noise) / 5, 0, 1) * noise_mask).astype(np.float32), (w, h), interpolation=cv2.INTER_NEAREST)
        heat_full = np.maximum(heat_ela, heat_noise)
        for c in clusters:
            b = c["target"]
            heat_full[b["y"]: b["y"] + b["h"], b["x"]: b["x"] + b["w"]] = np.maximum(heat_full[b["y"]: b["y"] + b["h"], b["x"]: b["x"] + b["w"]], 0.8)
        colored = cv2.applyColorMap((heat_full * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
        overlay = cv2.addWeighted(img, 0.55, colored, 0.45, 0)
        cv2.imwrite(str(art / "heatmap.jpg"), overlay, [cv2.IMWRITE_JPEG_QUALITY, 85])
        ela_vis = cv2.applyColorMap(np.clip(ela * 12, 0, 255).astype(np.uint8), cv2.COLORMAP_JET)
        cv2.imwrite(str(art / "ela.jpg"), ela_vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
        out.artifacts = {"heatmap": "heatmap.jpg", "ela": "ela.jpg", "analysis_size": [w, h],
                         "heatmap_basis": "Contiguous ELA outlier blocks, noise-sigma outlier blocks and copy-move boxes (measured, not model-predicted)",
                         "localized": bool(ela_regions or noise_regions or clusters)}

        out.limitations.append("Learned (pre-trained) AI-generation/deepfake models are not part of this classical agent; they run separately as the 'Learned AI/Deepfake Classifier' agent, whose result and validation status are reported there.")
        out.suspicion_score = aggregate_signals(out.signals)
        out.confidence = 0.6 if is_jpeg else 0.4
        out.limitations += [
            "Heuristic signals are uncalibrated indicators, not probabilities of manipulation.",
            "ELA is most informative for JPEG files and is weakened by repeated recompression or resizing.",
            "Fully AI-generated images may show no local inconsistencies; classical methods cannot rule them out.",
            f"Analysis performed on a downscaled copy (max side {MAX_SIDE}px); small edits may be missed.",
        ]
        if not is_jpeg:
            out.limitations.append("Non-JPEG input: ELA weight reduced.")
        n = len(out.indicators)
        out.interpretation = (f"{n} localized indicator(s) found; these warrant human inspection but are not proof of editing."
                              if n else "No localized pixel-level inconsistencies detected by classical methods. This does not establish authenticity.")
    out.duration_ms = t.ms
    return out.to_dict()

"""Screen candidate Hugging Face detectors on a *screening split* of the labelled videos.

The 120 labelled videos are split once (fixed seed) into 40 screening videos (20 real / 20 fake) and
80 held-out videos (40 / 40). Candidates are compared ONLY on the screening split; the selected model is
then measured once on the untouched held-out split (evaluation/run_video_eval.py --split heldout).
Frames and face crops are extracted once with the application's own pipeline and cached.

Usage:  python -m evaluation.screen_models
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from app.agents import ml_agent
from app.config import get_settings

from .run_video_eval import auc

HERE = Path(__file__).parent
DATA = HERE.parent / "data" / "eval_videos"
SPLIT_FILE = HERE / "video_split.json"

# (model id, pinned revision, input type)
CANDIDATES = [
    ("prithivMLmods/Deep-Fake-Detector-v2-Model", "3a99ae26f52c7ac7c3a53103b6cf3a8b617f7093", "face"),        # current A (baseline)
    ("dima806/deepfake_vs_real_image_detection", "29e4cf9efc543845610045f6ba7e88e5cf9d9301", "face"),         # current B (baseline)
    ("Organika/sdxl-detector", "b37fede8562cb72b89ec201c0987f96ba21b518a", "frame"),                           # current frame (baseline)
    ("hchcsuim/batch-size16_DFDC_opencv-1FPS_faces-expand30-aligned_unaugmentation", "03870974c7ddca0f6508c7ae68b87772e6b8e09c", "face"),
    ("hchcsuim/batch-size16_DFDC_opencv-1FPS_faces-expand50-aligned_unaugmentation", "5c9aa417b1e9118441877de06536b37b25019889", "face"),
    ("buildborderless/CommunityForensics-DeepfakeDet-ViT", "ac6ee457bea904a373065754107451793b56db00", "frame"),
]


def make_split(seed: int = 7) -> dict:
    if SPLIT_FILE.exists():
        return json.loads(SPLIT_FILE.read_text())
    items = json.loads((DATA / "manifest.json").read_text())["items"]
    rng = random.Random(seed)
    split = {"seed": seed, "screen": [], "heldout": []}
    for lab in ("real", "fake"):
        files = sorted(i["file"] for i in items if i["label"] == lab)
        screen = set(rng.sample(files, 20))
        split["screen"] += sorted(screen)
        split["heldout"] += [f for f in files if f not in screen]
    SPLIT_FILE.write_text(json.dumps(split, indent=1))
    return split


def crops_for(item: dict, cfg: dict) -> dict:
    """24 evenly spaced frames (downscaled) and YuNet face crops, cached as .npz."""
    cdir = DATA / "crops"
    cdir.mkdir(exist_ok=True)
    cf = cdir / (Path(item["file"]).stem + ".npz")
    if cf.exists():
        z = np.load(cf, allow_pickle=False)
        return {"frames": list(z["frames"]), "faces": list(z["faces"]) if z["faces"].size else []}
    cap = cv2.VideoCapture(item["path"], cv2.CAP_FFMPEG)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    want = set(np.linspace(0, total - 1, 24).astype(int).tolist())
    frames, faces, i = [], [], 0
    while want and cap.grab():
        if i in want:
            ok, f = cap.retrieve()
            if ok:
                crop, _, _ = ml_agent.detect_face(f, cfg["yunet"])
                if crop is not None:
                    faces.append(cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB), (256, 256), interpolation=cv2.INTER_AREA))
                h, w = f.shape[:2]
                s = 448 / max(h, w)
                frames.append(cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2RGB), (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA))
            want.discard(i)
        i += 1
    cap.release()
    fr = np.stack([cv2.resize(x, (448, 252)) for x in frames]) if frames else np.zeros((0,), np.uint8)
    fa = np.stack(faces) if faces else np.zeros((0,), np.uint8)
    np.savez_compressed(cf, frames=fr, faces=fa)
    return {"frames": list(fr), "faces": list(fa)}


def score_split(names: list[str], items: dict, cfg: dict, candidates=CANDIDATES) -> dict:
    out = {}
    for model, rev, kind in candidates:
        t0 = time.time()
        rows = []
        for name in names:
            it = items[name]
            c = crops_for(it, cfg)
            imgs = c["faces"] if kind == "face" else c["frames"]
            if len(imgs) < 3:
                continue
            p = ml_agent.fake_probabilities(imgs, model, rev, cfg["cache_dir"], True)
            rows.append({"file": name, "label": it["label"], "mean": float(np.mean(p)), "frac_ge_0_5": float(np.mean(np.array(p) >= 0.5))})
        pos = [r["mean"] for r in rows if r["label"] == "fake"]
        neg = [r["mean"] for r in rows if r["label"] == "real"]
        out[model] = {"revision": rev, "input": kind, "videos": len(rows), "roc_auc": auc(pos, neg),
                      "mean_fake": round(float(np.mean(pos)), 4) if pos else None, "mean_real": round(float(np.mean(neg)), 4) if neg else None,
                      "seconds": round(time.time() - t0, 1), "rows": rows}
        print(f"{model[:70]:70} {kind:5} n={len(rows):3} AUC={out[model]['roc_auc']}  fake={out[model]['mean_fake']} real={out[model]['mean_real']}  ({out[model]['seconds']}s)", flush=True)
    return out


def main() -> None:
    s = get_settings()
    cfg = ml_agent.config_from_settings(s)
    split = make_split()
    items = {i["file"]: i for i in json.loads((DATA / "manifest.json").read_text())["items"]}
    print(f"screening on {len(split['screen'])} videos; {len(split['heldout'])} held out (never used for selection)", flush=True)
    for n, name in enumerate(split["screen"] + split["heldout"], 1):  # warm the crop cache for both splits
        crops_for(items[name], cfg)
        if n % 20 == 0:
            print(f"  crops cached {n}/120", flush=True)
    res = score_split(split["screen"], items, cfg)
    (HERE / "screening_results.json").write_text(json.dumps({"split_seed": split["seed"], "results": res}, indent=1))


if __name__ == "__main__":
    sys.exit(main())

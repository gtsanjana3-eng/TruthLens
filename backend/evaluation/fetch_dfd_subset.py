"""Download a fixed, reproducible labelled subset of angads24/deepfake-video (Google/Jigsaw DFD clips).

Research/evaluation use only. Files stay in backend/data/eval_videos (git-ignored); the manifest records
exactly which files were used. Usage: python -m evaluation.fetch_dfd_subset --per-class 60
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

REPO = "angads24/deepfake-video"
REVISION = "3470af66b50e6d715f108bdfa67342907137e1f4"
OUT = Path(__file__).resolve().parent.parent / "data" / "eval_videos"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-class", type=int, default=60)
    ap.add_argument("--seed", type=int, default=2026)
    a = ap.parse_args()
    files = HfApi().list_repo_files(REPO, repo_type="dataset", revision=REVISION)
    rng = random.Random(a.seed)
    manifest = []
    for label in ("real", "fake"):
        pool = sorted(f for f in files if f.startswith(f"dataset/{label}/") and f.endswith(".mp4"))
        for f in rng.sample(pool, a.per_class):
            for attempt in range(6):  # tolerate transient network/DNS failures; downloads resume from cache
                try:
                    p = hf_hub_download(REPO, f, repo_type="dataset", revision=REVISION, cache_dir=str(OUT / ".cache"))
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 5:
                        raise
                    print(f"  retry {attempt + 1} for {Path(f).name}: {type(e).__name__}", flush=True)
                    time.sleep(10 * (attempt + 1))
            manifest.append({"file": f, "label": label, "path": p})
            print(f"{len(manifest):4d} {label} {Path(f).name}", flush=True)
    (OUT / "manifest.json").write_text(json.dumps({"repo": REPO, "revision": REVISION, "seed": a.seed, "items": manifest}, indent=1))
    print("DONE", len(manifest))


if __name__ == "__main__":
    main()

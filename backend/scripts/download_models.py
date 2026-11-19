"""Download the four pinned, validated models into the local cache and smoke-test each one.

Usage:  python -m scripts.download_models
Afterwards you may set HF_OFFLINE=true in backend/.env so analysis never touches the network.
"""
from __future__ import annotations

import time

import numpy as np

from app.agents import hf_models, ml_agent
from app.config import get_settings


def main() -> None:
    s = get_settings()
    ok, reason = ml_agent.dependencies_available()
    if not ok:
        raise SystemExit(reason)
    cache = ml_agent.ensure_dir(s.hf_cache_dir)
    for media, model, rev in (("image", s.hf_video_model, s.hf_video_model_revision), ("image", s.hf_image_model, s.hf_image_model_revision),
                              ("audio", s.hf_audio_model, s.hf_audio_model_revision), ("text", s.hf_text_model, s.hf_text_model_revision)):
        if not model:
            print(f"--  {media}: no model configured")
            continue
        t0 = time.time()
        _, m, fake = hf_models.load(model, rev, media, cache)
        if media == "image":
            sc = hf_models.score_images([np.full((224, 224, 3), 128, np.uint8)], model, rev, cache)[0]
        elif media == "audio":
            sc = hf_models.score_audio(np.zeros(16000 * 2, np.float32), 16000, model, rev, cache)[0][1]
        else:
            sc = hf_models.score_text("This is a short smoke test sentence. " * 10, model, rev, cache)[0][1]
        print(f"OK  {model}@{rev[:10]}  labels={m.config.id2label} fake_idx={fake}  smoke score={sc:.3f}  ({time.time() - t0:.1f}s)")
    print(f"Models cached in {cache}")


if __name__ == "__main__":
    main()

"""Generic, safe loaders and scorers for pre-trained Hugging Face classifiers (image, audio, text).

Safety: pinned revisions, `.safetensors` only, `trust_remote_code=False`, explicit fake-class indices per
model (taken from each model's config.json and pinned here – never guessed from label wording).
Scores are softmax mass on the fake class(es): uncalibrated model scores, not probabilities.
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np

# model_id -> indices of the "fake / AI-generated / spoofed" classes, verified against config.json id2label.
FAKE_CLASSES: dict[str, list[int]] = {
    # video (face-swap, applied to face crops)
    "hchcsuim/batch-size16_DFDC_opencv-1FPS_faces-expand50-aligned_unaugmentation": [0],  # {0: Fake, 1: Real}
    "hchcsuim/batch-size16_DFDC_opencv-1FPS_faces-expand30-aligned_unaugmentation": [0],  # {0: Fake, 1: Real} (screening only)
    "prithivMLmods/Deep-Fake-Detector-v2-Model": [1],          # {0: Realism, 1: Deepfake} (screening only)
    "dima806/deepfake_vs_real_image_detection": [1],           # {0: Real, 1: Fake} (screening only)
    # audio (AI voice)
    "mo-thecreator/Deepfake-audio-detection": [0],              # {0: fake, 1: real}
    "MelodyMachine/Deepfake-audio-detection-V2": [0],          # {0: fake, 1: real}
    "Hemgg/Deepfake-audio-detection": [0],                     # {0: AIVoice, 1: HumanVoice}
    "Vansh180/deepfake-audio-wav2vec2": [1],                   # {0: real, 1: fake}
    "abhishtagatya/wav2vec2-base-960h-itw-deepfake": [1],      # {0: bona-fide, 1: spoof}
    # image (AI-generated)
    "Ateeqq/ai-vs-human-image-detector": [0],                  # {0: ai, 1: hum}
    "haywoodsloan/ai-image-detector-deploy": [0],              # {0: artificial, 1: real}
    "dima806/ai_vs_human_generated_image_detection": [1],      # {0: human, 1: AI-generated}
    "NYUAD-ComNets/NYUAD_AI-generated_images_detector": [0, 2],  # {0: dalle, 1: real, 2: sd}
    "aaronkantrowitz/ai-human-generated-image-detection": [1],  # {0: human, 1: AI-generated}
    "Organika/sdxl-detector": [0],                             # {0: artificial, 1: human}
    # text (AI-written)
    "fakespot-ai/roberta-base-ai-text-detection-v1": [1],      # {0: Human, 1: AI}
    "openai-community/roberta-base-openai-detector": [0],      # {0: Fake, 1: Real}
    "openai-community/roberta-large-openai-detector": [0],     # {0: Fake, 1: Real}
}
SINGLE_LOGIT_IS_FAKE = {"buildborderless/CommunityForensics-DeepfakeDet-ViT": True}

_cache: dict[tuple[str, str, str], tuple[Any, Any, list[int]]] = {}


def _threads() -> None:
    import torch

    torch.set_num_threads(max(1, os.cpu_count() or 1))


def load(model_id: str, revision: str, kind: str, cache_dir: str, offline: bool = False):
    key = (model_id, revision, kind)
    if key in _cache:
        return _cache[key]
    import transformers as T

    _threads()
    kw = dict(revision=revision, cache_dir=cache_dir, local_files_only=offline, trust_remote_code=False)
    if kind == "image":
        proc = T.AutoImageProcessor.from_pretrained(model_id, **kw)
        model = T.AutoModelForImageClassification.from_pretrained(model_id, use_safetensors=True, **kw)
    elif kind == "audio":
        proc = T.AutoFeatureExtractor.from_pretrained(model_id, **kw)
        model = T.AutoModelForAudioClassification.from_pretrained(model_id, use_safetensors=True, **kw)
    elif kind == "text":
        proc = T.AutoTokenizer.from_pretrained(model_id, **kw)
        model = T.AutoModelForSequenceClassification.from_pretrained(model_id, use_safetensors=True, **kw)
    else:
        raise ValueError(kind)
    model.eval()
    if model.config.num_labels == 1:
        if model_id not in SINGLE_LOGIT_IS_FAKE:
            raise ValueError(f"{model_id}: single output with undocumented direction")
        fake = [-1] if SINGLE_LOGIT_IS_FAKE[model_id] else [-2]
    else:
        if model_id not in FAKE_CLASSES:
            raise ValueError(f"{model_id}: fake class indices not pinned – refusing to guess from labels {model.config.id2label}")
        fake = FAKE_CLASSES[model_id]
        if max(fake) >= model.config.num_labels:
            raise ValueError(f"{model_id}: pinned fake indices {fake} do not fit {model.config.num_labels} labels")
    _cache[key] = (proc, model, fake)
    return _cache[key]


def _to_score(logits, fake: list[int]) -> list[float]:
    import torch

    if fake[0] < 0:
        p = torch.sigmoid(logits[:, 0])
        return [float(v if fake[0] == -1 else 1 - v) for v in p]
    probs = torch.softmax(logits, dim=-1)
    return [float(probs[i, fake].sum()) for i in range(probs.shape[0])]


def score_images(images: list[np.ndarray], model_id: str, revision: str, cache_dir: str, offline: bool = False, batch: int = 16) -> list[float]:
    """images: RGB uint8 arrays."""
    import torch
    from PIL import Image

    proc, model, fake = load(model_id, revision, "image", cache_dir, offline)
    out: list[float] = []
    with torch.no_grad():
        for i in range(0, len(images), batch):
            inputs = proc(images=[Image.fromarray(x) for x in images[i: i + batch]], return_tensors="pt")
            out += _to_score(model(**inputs).logits, fake)
    return out


def audio_windows(x: np.ndarray, sr: int, window_s: float = 4.0, max_windows: int = 12) -> list[tuple[float, np.ndarray]]:
    """Split mono audio into up to `max_windows` evenly spread windows (skip near-silent ones)."""
    n = int(window_s * sr)
    if x.size <= n:
        return [(0.0, x)]
    starts = np.linspace(0, x.size - n, num=min(max_windows, int(np.ceil(x.size / n)))).astype(int)
    wins = [(s / sr, x[s: s + n]) for s in starts]
    voiced = [w for w in wins if float(np.sqrt(np.mean(w[1] ** 2))) > 1e-3]
    return voiced or wins


def score_audio(x: np.ndarray, sr: int, model_id: str, revision: str, cache_dir: str, offline: bool = False) -> list[tuple[float, float]]:
    """Returns [(window_start_seconds, score)] for 16 kHz-resampled mono audio."""
    import torch

    proc, model, fake = load(model_id, revision, "audio", cache_dir, offline)
    target = getattr(proc, "sampling_rate", 16000) or 16000
    if sr != target:
        from scipy.signal import resample_poly

        g = np.gcd(int(sr), int(target))
        x = resample_poly(x, target // g, sr // g).astype(np.float32)
        sr = target
    out = []
    with torch.no_grad():
        for t0, w in audio_windows(x, sr):
            inputs = proc(w, sampling_rate=sr, return_tensors="pt")
            out.append((round(t0, 2), _to_score(model(**inputs).logits, fake)[0]))
    return out


def score_text(text: str, model_id: str, revision: str, cache_dir: str, offline: bool = False, max_chunks: int = 8) -> list[tuple[int, float]]:
    """Returns [(chunk_start_char, score)] over ~400-token chunks of the text."""
    import torch

    tok, model, fake = load(model_id, revision, "text", cache_dir, offline)
    ids = tok(text, add_special_tokens=False, return_offsets_mapping=True)
    offsets = ids["offset_mapping"]
    step = 400
    starts = list(range(0, max(1, len(offsets)), step))[:max_chunks]
    out = []
    with torch.no_grad():
        for s in starts:
            seg = offsets[s: s + step]
            if not seg:
                continue
            a, b = seg[0][0], seg[-1][1]
            inputs = tok(text[a:b], return_tensors="pt", truncation=True, max_length=512)
            out.append((a, _to_score(model(**inputs).logits, fake)[0]))
    return out

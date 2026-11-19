"""Fetch fixed, reproducible labelled samples for AI-voice, AI-image and AI-text validation.

Samples are drawn with fixed seeds via the Hugging Face dataset-viewer API (individual files, not whole
archives) and split into a SCREENING part (used only to choose a model) and a HELD-OUT part (used once to
measure the chosen model). Files stay in backend/data/eval_<modality>/ (git-ignored). Research/evaluation use.

Usage: python -m evaluation.fetch_modalities [--only audio|image|text]
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent.parent / "data"
API = "https://datasets-server.huggingface.co/rows"
UA = {"User-Agent": "truthlens-eval/1.0"}

AUDIO = {"repo": "garystafford/deepfake-audio-detection", "revision": "fcf5344bb7f82b54b6b932291326d29750ef1e82", "total": 1866,
         "per_class": 150, "screen_per_class": 50}
IMAGE = {"repo": "Parveshiiii/AI-vs-Real", "revision": "bce7ac5b95c36c5013389341b94c75aa44882165", "total": 13999,
         "per_class": 150, "screen_per_class": 50}
TEXT_HELDOUT = {"repo": "textsightai/ai-text-detection-benchmark-2026", "revision": "423b1768f0827675295fd6dc3cd255ad2f411183",
                "files": {"ai": "data/ai_generated_2026-09-29.jsonl",
                          "human": ["data/human_literature_publicdomain.jsonl", "data/human_pmc_ccby_nonanglophone.jsonl",
                                    "data/human_wikipedia_pre2022.jsonl"]}}
TEXT_SCREEN = {"repo": "Ateeqq/AI-and-Human-Generated-Text", "per_class": 60}


def _get(client: httpx.Client, url: str, **kw) -> httpx.Response:
    for attempt in range(6):
        try:
            r = client.get(url, timeout=60, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                raise httpx.HTTPError(f"HTTP {r.status_code}")
            r.raise_for_status()
        except httpx.HTTPError as e:
            if attempt == 5:
                raise
            print(f"  retry {attempt + 1}: {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("unreachable")


def _rows(client, repo, offset, length=100, config="default", split="train"):
    return _get(client, API, params={"dataset": repo, "config": config, "split": split, "offset": offset, "length": length}).json()["rows"]


def _sample_rows(client, spec, label_of, seed):
    """Walk random 100-row pages until per_class examples of each label are collected."""
    rng = random.Random(seed)
    pages = list(range(0, spec["total"], 100))
    rng.shuffle(pages)
    got: dict[str, list] = {"real": [], "fake": []}
    for off in pages:
        for r in _rows(client, spec["repo"], off):
            lab = label_of(r["row"])
            if lab in got and len(got[lab]) < spec["per_class"]:
                got[lab].append((off + r["row_idx"] - off if "row_idx" in r else off, r["row"]))
        if all(len(v) >= spec["per_class"] for v in got.values()):
            break
    return got


def _split(items: list[dict], screen_per_class: int, seed: int) -> None:
    rng = random.Random(seed)
    for lab in {i["label"] for i in items}:
        group = [i for i in items if i["label"] == lab]
        for i in rng.sample(group, screen_per_class):
            i["split"] = "screen"
    for i in items:
        i.setdefault("split", "heldout")


def fetch_audio(client):
    out = DATA / "eval_audio"
    out.mkdir(parents=True, exist_ok=True)
    got = _sample_rows(client, AUDIO, lambda row: {0: "real", 1: "fake"}.get(row["label"]), seed=11)
    items = []
    for lab, rows in got.items():
        for k, (_, row) in enumerate(rows):
            src = row["audio"][0]["src"] if isinstance(row["audio"], list) else row["audio"]["src"]
            ext = ".wav" if "wav" in src.split("?")[0][-6:] else (".mp3" if ".mp3" in src else ".flac")
            p = out / f"{lab}_{k:03d}{ext}"
            if not p.exists():
                p.write_bytes(_get(client, src).content)
            items.append({"file": p.name, "path": str(p), "label": lab})
        print(f"audio {lab}: {len(rows)}", flush=True)
    _split(items, AUDIO["screen_per_class"], seed=12)
    (out / "manifest.json").write_text(json.dumps({"dataset": AUDIO["repo"], "revision": AUDIO["revision"], "items": items}, indent=1))


def fetch_image(client):
    out = DATA / "eval_image"
    out.mkdir(parents=True, exist_ok=True)
    first = _rows(client, IMAGE["repo"], 0, 5)
    print("image label field sample:", [r["row"]["binary_label"] for r in first], flush=True)
    # Dataset card (pinned revision): "0 -> AI-generated images, 1 -> Real (human-captured) images".
    got = _sample_rows(client, IMAGE, lambda row: {0: "fake", 1: "real"}.get(int(row["binary_label"])), seed=21)
    items = []
    for lab, rows in got.items():
        for k, (_, row) in enumerate(rows):
            src = row["image"]["src"]
            p = out / f"{lab}_{k:03d}.jpg"
            if not p.exists():
                p.write_bytes(_get(client, src).content)
            items.append({"file": p.name, "path": str(p), "label": lab})
        print(f"image {lab}: {len(rows)}", flush=True)
    _split(items, IMAGE["screen_per_class"], seed=22)
    (out / "manifest.json").write_text(json.dumps({"dataset": IMAGE["repo"], "revision": IMAGE["revision"], "items": items}, indent=1))


def fetch_text(client):
    out = DATA / "eval_text"
    out.mkdir(parents=True, exist_ok=True)
    base = f"https://huggingface.co/datasets/{TEXT_HELDOUT['repo']}/resolve/{TEXT_HELDOUT['revision']}/"
    held = []
    for line in _get(client, base + TEXT_HELDOUT["files"]["ai"]).text.splitlines():
        if line.strip():
            held.append({"text": json.loads(line)["text"], "label": "fake", "source": "ai_generated_2026"})
    human = []
    for f in TEXT_HELDOUT["files"]["human"]:
        for line in _get(client, base + f).text.splitlines():
            if line.strip():
                human.append({"text": json.loads(line)["text"], "label": "real", "source": Path(f).stem})
    rng = random.Random(31)
    held += rng.sample(human, min(len(human), 150))
    for i in held:
        i["split"] = "heldout"
    # screening set: a different dataset (scientific abstracts), fixed sample
    rows = []
    for off in random.Random(32).sample(range(0, 22900, 100), 8):
        rows += [r["row"] for r in _rows(client, TEXT_SCREEN["repo"], off)]
    screen = {"real": [], "fake": []}
    for r in rows:
        lab = {0: "real", 1: "fake"}.get(int(r["label"]))
        if lab and len(screen[lab]) < TEXT_SCREEN["per_class"] and r.get("abstract"):
            screen[lab].append({"text": r["abstract"], "label": lab, "source": "ateeqq_abstracts", "split": "screen"})
    items = held + screen["real"] + screen["fake"]
    (out / "manifest.json").write_text(json.dumps({"heldout_dataset": TEXT_HELDOUT["repo"], "heldout_revision": TEXT_HELDOUT["revision"],
                                                  "screen_dataset": TEXT_SCREEN["repo"], "items": items}, indent=1))
    print(f"text: heldout {sum(i['label'] == 'fake' for i in held)} AI / {sum(i['label'] == 'real' for i in held)} human; "
          f"screen {len(screen['fake'])} / {len(screen['real'])}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["audio", "image", "text"])
    a = ap.parse_args()
    with httpx.Client(headers=UA, follow_redirects=True) as client:
        for name, fn in (("text", fetch_text), ("audio", fetch_audio), ("image", fetch_image)):
            if not a.only or a.only == name:
                fn(client)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()

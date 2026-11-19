"""Reproducible evaluation of the TruthLens pipelines.

Builds a deterministic synthetic dataset (documented in evaluation/README.md), runs the real
agents, and records accuracy, precision, recall, F1, FPR, FNR and processing time per modality.
Results are written to evaluation/results.json and evaluation/RESULTS.md exactly as measured.

Usage:  python -m evaluation.run_eval [--pairs 10]
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agents import audio_agent, image_agent, text_agent, video_agent
from app.agents.crossmodal_agent import EvidenceBundle, correlate

from .synth import make_audio_pair, make_image_pair, make_video_pair

HERE = Path(__file__).parent
FLAG_SEVERITIES = {"low", "medium", "high"}  # decision rule: any non-informational indicator => "flagged"


def flagged(res: dict) -> bool:
    return any(i["severity"] in FLAG_SEVERITIES for i in res["indicators"])


def metrics(rows: list[tuple[bool, bool, float]]) -> dict:
    tp = sum(1 for y, p, _ in rows if y and p)
    tn = sum(1 for y, p, _ in rows if not y and not p)
    fp = sum(1 for y, p, _ in rows if not y and p)
    fn = sum(1 for y, p, _ in rows if y and not p)
    n = len(rows)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    times = [t for _, _, t in rows]
    return {"samples": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "accuracy": round((tp + tn) / n, 4) if n else None, "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0,
            "false_positive_rate": round(fp / (fp + tn), 4) if fp + tn else 0.0, "false_negative_rate": round(fn / (fn + tp), 4) if fn + tp else 0.0,
            "mean_processing_ms": round(statistics.mean(times), 1) if times else None, "max_processing_ms": round(max(times), 1) if times else None}


def timed(fn, *a):
    t0 = time.perf_counter()
    r = fn(*a)
    return r, (time.perf_counter() - t0) * 1000


def run(pairs: int, seed0: int = 5000) -> dict:
    work = Path(tempfile.mkdtemp(prefix="tl-eval-"))
    results: dict = {}
    try:
        rows = []
        for i in range(pairs):
            kind = "copy_move" if i % 2 == 0 else "splice"
            g, m = make_image_pair(work, seed0 + i, kind)
            for path, label in ((g, False), (m, True)):
                r, ms = timed(image_agent.analyze, str(path), "e", str(work / "art"))
                rows.append((label, flagged(r), ms))
        results["image"] = metrics(rows)

        rows = []
        for i in range(pairs):
            g, m = make_video_pair(work, seed0 + 100 + i)
            for path, label in ((g, False), (m, True)):
                r, ms = timed(video_agent.analyze, str(path), "e", str(work / f"v{i}{label}"))
                rows.append((label, flagged(r), ms))
        results["video"] = metrics(rows)

        rows = []
        for i in range(pairs):
            g, m = make_audio_pair(work, seed0 + 200 + i)
            for path, label in ((g, False), (m, True)):
                r, ms = timed(audio_agent.analyze, str(path), "e", str(work / "a"))
                rows.append((label, flagged(r), ms))
        results["audio"] = metrics(rows)

        # Text & context: caption claims vs media metadata (consistent vs contradicted captions)
        rows = []
        import random

        rnd = random.Random(seed0)
        for i in range(pairs):
            day = 1 + rnd.randrange(27)
            meta = {"DateTimeOriginal": f"2024:05:{day:02d} 14:00:00", "Make": "Apple", "Model": "iPhone 14"}
            consistent = f"Photo taken on 2024-05-{day:02d} with an iPhone during the afternoon."
            wrong_day = (day + 7 + rnd.randrange(10)) % 28 + 1
            contradicted = f"Exclusive: photo taken on 2024-0{rnd.choice([6, 7, 8])}-{wrong_day:02d} with a Samsung Galaxy."
            for caption, label in ((consistent, False), (contradicted, True)):
                t0 = time.perf_counter()
                tr = text_agent.analyze(caption, "t")
                b_img = EvidenceBundle("i", "E1", "image", metadata=meta, brightness=120)
                b_txt = EvidenceBundle("t", "E2", "text", claims=tr["artifacts"]["claims"])
                cross, findings, _ = correlate([b_img, b_txt], None)
                pred = flagged(tr) or flagged(cross) or any(f.effect == "strengthens" for f in findings)
                rows.append((label, pred, (time.perf_counter() - t0) * 1000))
        results["text_context"] = metrics(rows)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=10)
    args = ap.parse_args()
    t0 = time.time()
    res = run(args.pairs)
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(), "pairs_per_modality": args.pairs, "python": platform.python_version(),
            "platform": platform.platform(), "decision_rule": "flagged if any indicator with severity low/medium/high",
            "dataset": "Deterministic synthetic genuine/manipulated pairs (evaluation/synth.py) – NOT real-world deepfakes",
            "agent_versions": {"image": image_agent.VERSION, "video": video_agent.VERSION, "audio": audio_agent.VERSION, "text": text_agent.VERSION},
            "wall_time_s": round(time.time() - t0, 1)}
    out = {"meta": meta, "results": res}
    (HERE / "results.json").write_text(json.dumps(out, indent=2), "utf-8")
    lines = ["# TruthLens evaluation results (measured)", "", f"Generated {meta['generated_at']} · {args.pairs} genuine + {args.pairs} manipulated samples per modality · "
             f"Python {meta['python']}", "", f"**Dataset:** {meta['dataset']}.  ", f"**Decision rule:** {meta['decision_rule']}.", "",
             "| Modality | N | Accuracy | Precision | Recall | F1 | FPR | FNR | Mean ms | Max ms |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, m in res.items():
        lines.append(f"| {k} | {m['samples']} | {m['accuracy']} | {m['precision']} | {m['recall']} | {m['f1']} | {m['false_positive_rate']} | "
                     f"{m['false_negative_rate']} | {m['mean_processing_ms']} | {m['max_processing_ms']} |")
    lines += ["", "These figures only show that each pipeline detects the specific, documented synthetic manipulations. They say nothing about "
              "accuracy on real-world deepfakes, which requires licensed benchmarks (see README)."]
    (HERE / "RESULTS.md").write_text("\n".join(lines) + "\n", "utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()

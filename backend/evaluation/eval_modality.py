"""Screen / validate AI-voice, AI-image and AI-text detectors on labelled data.

  python -m evaluation.eval_modality --modality audio --stage screen              # compare candidates (screening split)
  python -m evaluation.eval_modality --modality audio --stage heldout --model X   # measure the chosen model once

Writes evaluation/<modality>_screening.json or evaluation/<modality>_results.json (+ <MODALITY>_RESULTS.md).
Fixed a-priori rule (same as video): "likely AI" if score >= 0.70, "no indication" if score <= 0.30, otherwise inconclusive.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from app.agents import hf_models
from app.config import get_settings

from .run_video_eval import auc, metrics, wilson

HERE = Path(__file__).parent
DATA = HERE.parent / "data"
LIKELY, NOIND = 0.70, 0.30

CANDIDATES = {
    "audio": [("MelodyMachine/Deepfake-audio-detection-V2", "de3cde5a29c449bb5268814e421b46bf6ebdcd72"), ("abhishtagatya/wav2vec2-base-960h-itw-deepfake", "cb2350dec8246c67a5924462659c1796214f019f")],
    "image": [("Ateeqq/ai-vs-human-image-detector", "60e82406916921b823616bee33397baab38af3f0"), ("haywoodsloan/ai-image-detector-deploy", "4ae1822603cb1ec980c217cfde1ef1b8e0477a16")],
    "text": [("fakespot-ai/roberta-base-ai-text-detection-v1", "f9cdb14d1f8b105f597d80fa7b56f20c6ea0e9db"), ("openai-community/roberta-base-openai-detector", "6cba99c003b711c7fe94f8a3aa2be35a792cb6fa")],
}


def full_revision(model_id: str, short: str) -> str:
    """Resolve a short revision prefix to the full commit sha (pinned in results for reproducibility)."""
    if len(short) == 40:
        return short
    from huggingface_hub import HfApi

    return HfApi().model_info(model_id).sha if HfApi().model_info(model_id).sha.startswith(short) else short


def items_for(modality: str, split: str) -> list[dict]:
    m = json.loads((DATA / f"eval_{modality}" / "manifest.json").read_text("utf-8"))
    return [i for i in m["items"] if i["split"] == split]


def score_item(modality: str, item: dict, model: str, rev: str, cache: str) -> float:
    if modality == "image":
        import cv2

        bgr = cv2.imdecode(np.fromfile(item["path"], np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("undecodable image")
        return hf_models.score_images([cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)], model, rev, cache, True)[0]
    if modality == "audio":
        from app.agents.audio_agent import load_audio

        x, _, _ = load_audio(item["path"], max_seconds=60)
        return float(np.mean([s for _, s in hf_models.score_audio(x, 16000, model, rev, cache, True)]))
    return float(np.mean([s for _, s in hf_models.score_text(item["text"], model, rev, cache, True)]))


def run(modality: str, split: str, models: list[tuple[str, str]], prefetch: bool) -> dict:
    s = get_settings()
    cache = str(s.hf_cache_dir)
    items = items_for(modality, split)
    res = {}
    for model, short in models:
        rev = full_revision(model, short) if prefetch else short
        if prefetch:
            hf_models.load(model, rev, modality, cache, offline=False)
        t0 = time.time()
        rows = []
        for it in items:
            try:
                sc = score_item(modality, it, model, rev, cache)
            except Exception as e:  # noqa: BLE001 - unreadable sample: record, do not guess
                rows.append({"id": it.get("file") or it.get("source"), "label": it["label"], "error": f"{type(e).__name__}"})
                continue
            rows.append({"id": it.get("file") or it.get("source"), "label": it["label"], "score": round(sc, 4)})
        ok = [r for r in rows if "score" in r]
        pos = [r["score"] for r in ok if r["label"] == "fake"]
        neg = [r["score"] for r in ok if r["label"] == "real"]
        flagged = [(r["label"] == "fake", r["score"] >= LIKELY) for r in ok]
        decided = [r for r in ok if r["score"] >= LIKELY or r["score"] <= NOIND]
        npv_n = [r for r in ok if r["score"] <= NOIND]
        npv_k = sum(r["label"] == "real" for r in npv_n)
        prec = metrics(flagged)
        res[model] = {"revision": rev, "n": len(ok), "errors": len(rows) - len(ok), "roc_auc": auc(pos, neg),
                      "mean_fake": round(float(np.mean(pos)), 4) if pos else None, "mean_real": round(float(np.mean(neg)), 4) if neg else None,
                      "rule_likely_ai": prec, "coverage": round(len(decided) / len(ok), 4) if ok else None,
                      "no_indication_npv": {"k": npv_k, "n": len(npv_n), "value": round(npv_k / len(npv_n), 4) if npv_n else None, "ci95": wilson(npv_k, len(npv_n))},
                      "seconds": round(time.time() - t0, 1), "rows": rows}
        print(f"{model[:58]:58} n={len(ok):3} err={len(rows) - len(ok)} AUC={res[model]['roc_auc']} prec={prec['precision']} rec={prec['recall']} "
              f"FPR={prec['fpr']} cov={res[model]['coverage']} ({res[model]['seconds']}s)", flush=True)
    return res


def write_md(modality: str, out: dict) -> None:
    (model, r), = out["results"].items()
    m = r["rule_likely_ai"]
    f = lambda x: "–" if x is None else f"{x:.3f}"  # noqa: E731
    L = [f"# AI {modality} detector – measured held-out validation", "",
         f"Generated {out['generated_at']} by `python -m evaluation.eval_modality --modality {modality} --stage heldout`.", "",
         f"**Model:** `{model}` @ `{r['revision'][:12]}`  ", f"**Held-out data:** {out['dataset']} – {r['n']} samples never used to choose the model.", "",
         "| ROC-AUC | Precision ('likely AI') | Recall | F1 | FPR | FNR | 'No indication' correct | Coverage |", "|---|---|---|---|---|---|---|---|",
         f"| {r['roc_auc']} | {f(m['precision'])} | {f(m['recall'])} {m['recall_ci95'] or ''} | {f(m['f1'])} | {f(m['fpr'])} {m['fpr_ci95'] or ''} | {f(m['fnr'])} | "
         f"{r['no_indication_npv']['k']}/{r['no_indication_npv']['n']} | {f(r['coverage'])} |", "",
         f"Rule fixed before testing: 'likely AI' if score ≥ {LIKELY}, 'no indication' if ≤ {NOIND}. Scores are uncalibrated model outputs.",
         f"Decision-grade if ROC-AUC ≥ 0.75: **{'yes' if (r['roc_auc'] or 0) >= 0.75 else 'no'}**."]
    (HERE / f"{modality.upper()}_RESULTS.md").write_text("\n".join(L) + "\n", "utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modality", choices=["audio", "image", "text"], required=True)
    ap.add_argument("--stage", choices=["screen", "heldout"], required=True)
    ap.add_argument("--model", help="model id for --stage heldout")
    ap.add_argument("--prefetch", action="store_true", help="download weights (online) before scoring")
    a = ap.parse_args()
    cands = CANDIDATES[a.modality]
    if a.stage == "heldout":
        cands = [c for c in cands if c[0] == a.model]
        assert cands, "unknown --model"
    res = run(a.modality, a.stage, cands, a.prefetch)
    man = json.loads((DATA / f"eval_{a.modality}" / "manifest.json").read_text("utf-8"))
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "modality": a.modality, "stage": a.stage,
           "dataset": man.get("dataset") or man.get("heldout_dataset"), "rule": {"likely_ai": LIKELY, "no_indication": NOIND}, "results": res}
    name = f"{a.modality}_screening.json" if a.stage == "screen" else f"{a.modality}_results.json"
    (HERE / name).write_text(json.dumps(out, indent=1), "utf-8")
    if a.stage == "heldout":
        write_md(a.modality, out)


if __name__ == "__main__":
    main()

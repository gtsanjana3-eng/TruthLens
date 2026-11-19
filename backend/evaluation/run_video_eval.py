"""Validate the learned video detectors on a labelled, held-out set of real vs deepfake videos.

Runs the *same* agent the application uses (app.agents.ml_agent.analyze_video) on every video in
data/eval_videos/manifest.json and writes measured metrics to evaluation/VIDEO_RESULTS.md and
evaluation/video_results.json. Nothing is tuned on this data: thresholds are the fixed a-priori ones.

Usage:  python -m evaluation.run_video_eval
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from app.agents import ml_agent
from app.config import get_settings

HERE = Path(__file__).parent
DATA = HERE.parent / "data" / "eval_videos"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)


def auc(pos: list[float], neg: list[float]) -> float | None:
    """ROC-AUC via the Mann-Whitney statistic (ties count half)."""
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return round(wins / (len(pos) * len(neg)), 4)


def metrics(rows: list[tuple[bool, bool]]) -> dict:
    tp = sum(y and p for y, p in rows)
    tn = sum((not y) and (not p) for y, p in rows)
    fp = sum((not y) and p for y, p in rows)
    fn = sum(y and (not p) for y, p in rows)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    f1 = 2 * prec * rec / (prec + rec) if prec and rec else 0.0 if prec is not None and rec is not None else None
    return {"n": len(rows), "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "accuracy": round((tp + tn) / len(rows), 4) if rows else None,
            "precision": None if prec is None else round(prec, 4), "recall": None if rec is None else round(rec, 4),
            "f1": None if f1 is None else round(f1, 4),
            "fpr": round(fp / (fp + tn), 4) if fp + tn else None, "fnr": round(fn / (fn + tp), 4) if fn + tp else None,
            "recall_ci95": wilson(tp, tp + fn), "fpr_ci95": wilson(fp, fp + tn)}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["all", "heldout"], default="heldout",
                    help="heldout = the 80 videos never used for model selection (evaluation/video_split.json)")
    args = ap.parse_args()
    s = get_settings()
    manifest = json.loads((DATA / "manifest.json").read_text())
    if args.split == "heldout":
        keep = set(json.loads((HERE / "video_split.json").read_text())["heldout"])
        manifest["items"] = [i for i in manifest["items"] if i["file"] in keep]
    cfg = ml_agent.config_from_settings(s)
    cfg["offline"] = True
    cfg["require_validation"] = False  # scoring must not depend on the validation it is producing
    # Score without the decision-grade policy (it is derived from these very results and applied afterwards).
    ml_agent.VALIDATION_FILE = HERE / "__no_validation_during_scoring__.json"
    cache = DATA / "results"
    cache.mkdir(exist_ok=True)
    per_video = []
    t_start = time.time()
    for k, item in enumerate(manifest["items"], 1):
        mkey = "-".join(r[:8] for r in (cfg["face_a"][1], cfg["face_b"][1], cfg["frame"][1]))
        cf = cache / (Path(item["file"]).stem + f"@{ml_agent.VERSION}-{mkey}.json")
        # v2.0.0 rows remain valid where the v2.1 change (faces => never 'rendered') cannot alter anything:
        # same models, same frame sampling, same face detection; only previously 'rendered' rows are re-run.
        if cf.exists():
            row = json.loads(cf.read_text())
        else:
            t0 = time.time()
            r = ml_agent.analyze_video(item["path"], "eval", cfg, None)
            a = r.get("artifacts", {})
            row = {"file": item["file"], "label": item["label"], "status": r["status"], "verdict": a.get("verdict"),
                   "faces": a.get("faces_found"), "frames": a.get("frames_analysed"), "rendered": (a.get("content") or {}).get("rendered"),
                   "detectors": {d["key"]: {"status": d["status"], "mean": (d.get("summary") or {}).get("mean"), "verdict": (d.get("summary") or {}).get("verdict")}
                                 for d in a.get("detectors", [])},
                   "seconds": round(time.time() - t0, 2)}
            cf.write_text(json.dumps(row))
        per_video.append(row)
        print(f"{k:4d}/{len(manifest['items'])} {row['label']:4} {row['verdict']:16} faces {row['faces']}/{row['frames']} "
              + " ".join(f"{key}={v['mean']}" for key, v in row["detectors"].items()), flush=True)

    out: dict = {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(), "dataset": manifest["repo"], "dataset_revision": manifest["revision"],
                          "selection_seed": manifest["seed"], "videos": len(per_video), "split": args.split, "agent_version": ml_agent.VERSION,
                          "models": {"face_a": cfg["face_a"], "face_b": cfg["face_b"], "frame": cfg["frame"]}, "frames_per_video": cfg["n_frames"],
                          "thresholds": {"likely_ai": ml_agent.LIKELY_FAKE, "no_indication": ml_agent.LIKELY_REAL}, "wall_time_s": round(time.time() - t_start, 1),
                          "mean_seconds_per_video": round(sum(r["seconds"] for r in per_video) / len(per_video), 2)},
           "detectors": {}, "combined": {}}
    for key in ("face_a", "face_b", "frame"):
        appl = [r for r in per_video if r["detectors"].get(key, {}).get("status") == "completed"]
        pos = [r["detectors"][key]["mean"] for r in appl if r["label"] == "fake"]
        neg = [r["detectors"][key]["mean"] for r in appl if r["label"] == "real"]
        out["detectors"][key] = {
            "model": cfg[key][0] if cfg.get(key) else None, "applicable_videos": len(appl), "roc_auc": auc(pos, neg),
            "mean_score_fake": round(sum(pos) / len(pos), 4) if pos else None, "mean_score_real": round(sum(neg) / len(neg), 4) if neg else None,
            "rule_verdict_likely_ai": metrics([(r["label"] == "fake", r["detectors"][key]["verdict"] == "likely_ai") for r in appl]),
            "rule_mean_ge_0_5": metrics([(r["label"] == "fake", r["detectors"][key]["mean"] >= 0.5) for r in appl]),
        }
    grade = {k: (d["roc_auc"] or 0) >= ml_agent.DECISION_GRADE_AUC for k, d in out["detectors"].items()}
    for r in per_video:
        dets = [{"status": v["status"], "summary": {"verdict": v["verdict"]}, "validation": {"decision_grade": grade[k]}}
                for k, v in r["detectors"].items() if v["status"] == "completed"]
        r["verdict_policy_v2_1"] = ml_agent.combine(dets)
    out["decision_grade_auc"] = ml_agent.DECISION_GRADE_AUC
    out["decision_grade"] = grade
    out["policy_v2_1_counts"] = {lab: {} for lab in ("real", "fake")}
    for r in per_video:
        c = out["policy_v2_1_counts"][r["label"]]
        c[r["verdict_policy_v2_1"]] = c.get(r["verdict_policy_v2_1"], 0) + 1
    pdec = [r for r in per_video if r["verdict_policy_v2_1"] in ("likely_ai", "no_indication")]
    out["app_headline"] = {
        "coverage": round(len(pdec) / len(per_video), 4),
        "on_decided_only": metrics([(r["label"] == "fake", r["verdict_policy_v2_1"] == "likely_ai") for r in pdec]),
        "abstentions_as_not_flagged": metrics([(r["label"] == "fake", r["verdict_policy_v2_1"] == "likely_ai") for r in per_video]),
        "no_indication_given_to_fakes": sum(1 for r in per_video if r["label"] == "fake" and r["verdict_policy_v2_1"] == "no_indication"),
    }
    decided = [r for r in per_video if r["verdict"] in ("likely_ai", "no_indication")]
    out["combined"] = {
        "verdict_counts": {lab: {v: sum(1 for r in per_video if r["label"] == lab and r["verdict"] == v)
                                 for v in ("likely_ai", "no_indication", "inconclusive", "models_disagree", "not_applicable")} for lab in ("real", "fake")},
        "coverage": round(len(decided) / len(per_video), 4),
        "on_decided_only": metrics([(r["label"] == "fake", r["verdict"] == "likely_ai") for r in decided]),
        "abstentions_as_not_flagged": metrics([(r["label"] == "fake", r["verdict"] == "likely_ai") for r in per_video]),
    }
    (HERE / "video_results.json").write_text(json.dumps({**out, "per_video": per_video}, indent=1), "utf-8")
    write_md(out)
    print(json.dumps({"detectors": {k: {kk: v[kk] for kk in ("roc_auc", "applicable_videos")} for k, v in out["detectors"].items()}, "combined": out["combined"]}, indent=1))


def _fmt(m: dict) -> str:
    def f(x):
        return "–" if x is None else f"{x:.3f}"
    return (f"| {m['n']} | {m['tp']}/{m['fp']}/{m['tn']}/{m['fn']} | {f(m['precision'])} | {f(m['recall'])} {m['recall_ci95'] or ''} | {f(m['f1'])} | "
            f"{f(m['fpr'])} {m['fpr_ci95'] or ''} | {f(m['fnr'])} |")


def write_md(out: dict) -> None:
    m = out["meta"]
    names = {"face_a": "Face A", "face_b": "Face B (independent)", "frame": "Full-frame synthetic"}
    L = ["# Learned video detector – measured validation", "",
         f"Generated {m['generated_at']} by `python -m evaluation.run_video_eval` (agent v{m['agent_version']}, {m['frames_per_video']} frames/video, "
         f"{m['mean_seconds_per_video']} s/video on CPU).", "",
         f"**Data:** {m['videos']} labelled videos from `{m['dataset']}` @ `{m['dataset_revision'][:10]}` (Google/Jigsaw DeepFakeDetection clips), "
         f"fixed random selection (seed {m['selection_seed']}); split: **{m.get('split')}** (held-out = never used to choose models). The models' training sets are not documented by their authors, so overlap with these "
         "clips **cannot be ruled out**; no model or threshold was fitted on this data.", "",
         f"**Fixed thresholds (set before testing):** likely-AI if mean ≥ {m['thresholds']['likely_ai']} and ≥60% of inputs ≥0.5; no-indication if mean ≤ {m['thresholds']['no_indication']}.", "",
         "## Per detector", "", "| Detector | Model | Videos applicable | ROC-AUC | Mean score fake / real |", "|---|---|---|---|---|"]
    names = {"face_a": "Face A", "face_b": "Face B (independent)", "frame": "Full-frame synthetic"}
    for k, d in out["detectors"].items():
        L.append(f"| {names[k]} | `{d['model']}` | {d['applicable_videos']} | {d['roc_auc']} | {d['mean_score_fake']} / {d['mean_score_real']} |")
    L += ["", "Operating-point metrics (fake = positive). TP/FP/TN/FN; 95% Wilson intervals in brackets.", "",
          "| Detector & rule | N | TP/FP/TN/FN | Precision | Recall | F1 | FPR | FNR |", "|---|---|---|---|---|---|---|---|"]
    for k, d in out["detectors"].items():
        L.append(f"| {names[k]} – verdict 'likely AI' " + _fmt(d["rule_verdict_likely_ai"]))
        L.append(f"| {names[k]} – mean ≥ 0.5 " + _fmt(d["rule_mean_ge_0_5"]))
    c = out["combined"]
    a = out["app_headline"]
    L += ["", "## Headline shown in the app (decision-grade policy)", "",
          "Only decision-grade detectors (measured AUC ≥ " + str(out["decision_grade_auc"]) + ") may produce 'likely AI' / 'no indication'.", "",
          "| True label | likely AI | no indication | inconclusive | models disagree | no validated detector |", "|---|---|---|---|---|---|"]
    for lab, cnt in out["policy_v2_1_counts"].items():
        L.append(f"| {lab} | " + " | ".join(str(cnt.get(v, 0)) for v in ("likely_ai", "no_indication", "inconclusive", "models_disagree", "no_validated_detector")) + " |")
    L += ["", f"Coverage (definite answer): **{a['coverage'] * 100:.1f}%**. 'No indication' was given to **{a['no_indication_given_to_fakes']}** fake video(s) – "
          "'no indication' is not proof of authenticity.", "",
          "| Scope | N | TP/FP/TN/FN | Precision | Recall | F1 | FPR | FNR |", "|---|---|---|---|---|---|---|---|",
          "| Decided videos only " + _fmt(a["on_decided_only"]), "| All videos (abstentions = not flagged) " + _fmt(a["abstentions_as_not_flagged"]), ""]
    L += ["", "## For comparison: headline without the decision-grade policy", "", f"Coverage (videos receiving a definite 'likely AI' or 'no indication'): **{c['coverage'] * 100:.1f}%**", "",
          "| True label | likely AI | no indication | inconclusive | models disagree | not applicable |", "|---|---|---|---|---|---|"]
    for lab, vc in c["verdict_counts"].items():
        L.append(f"| {lab} | " + " | ".join(str(vc[v]) for v in ("likely_ai", "no_indication", "inconclusive", "models_disagree", "not_applicable")) + " |")
    L += ["", "| Scope | N | TP/FP/TN/FN | Precision | Recall | F1 | FPR | FNR |", "|---|---|---|---|---|---|---|---|",
          "| Decided videos only " + _fmt(c["on_decided_only"]), "| All videos (abstentions = not flagged) " + _fmt(c["abstentions_as_not_flagged"]), "",
          "## Decision-grade policy (v2.1)", "",
          f"A detector may decide the headline only if its measured ROC-AUC here is ≥ {out['decision_grade_auc']} (fixed before re-testing). "
          f"Decision-grade: {', '.join(f'{names[k]}={v}' for k, v in out['decision_grade'].items())}.", "",
          "Headline under this policy: " + "; ".join(f"{lab}: {cnt}" for lab, cnt in out["policy_v2_1_counts"].items()), "",
          "These are measured results on one dataset (face-swap deepfakes of paid actors). They do not transfer to other manipulation types "
          "(fully AI-generated video, lip-sync, rendered graphics) or other compression levels."]
    (HERE / "VIDEO_RESULTS.md").write_text("\n".join(L) + "\n", "utf-8")


if __name__ == "__main__":
    main()

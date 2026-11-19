"""Evidence Review Agent and Report Generation Agent.

Review: combines agent outputs per evidence item into a *concern level* with explicit
uncertainty – it never outputs "fake" or "genuine". Disagreements are listed verbatim.
Report generation: assembles the structured report body used for the PDF and viewer."""
from __future__ import annotations

from typing import Any

from .base import SEVERITY_ORDER, AgentOutput, Timer

REVIEW_NAME = "Evidence Review Agent"
REPORT_NAME = "Report Generation Agent"
VERSION = "1.1.0"

LEVELS = ["no notable indicators", "low concern", "moderate concern", "elevated concern"]


def assess_evidence(label: str, outputs: list[dict], cross_findings: list[dict]) -> dict[str, Any]:
    completed = [o for o in outputs if o["status"] == "completed"]
    inds = [i for o in outputs for i in o["indicators"]]
    max_sev = max((SEVERITY_ORDER[i["severity"]] for i in inds), default=0)
    flagging = sorted({o["agent_name"] for o in completed if any(SEVERITY_ORDER[i["severity"]] >= 1 for i in o["indicators"])})
    clean = sorted({o["agent_name"] for o in completed if not o["indicators"]})
    strengthen = [f for f in cross_findings if f["effect"] == "strengthens"]
    scores = [o["suspicion_score"] for o in completed if o.get("suspicion_score") is not None]
    peak = max(scores, default=None)

    # Rule-based level: requires corroboration from >1 agent or cross-modal support to escalate.
    level = 0
    if max_sev >= 1:
        level = 1
    if max_sev >= 2 and (len(flagging) >= 2 or strengthen):
        level = 2
    if max_sev >= 3 or (len(flagging) >= 2 and len(strengthen) >= 2):
        level = 3

    unavailable = [f"{o['agent_name']}: {m['name']}" for o in outputs for m in o.get("model_checks", []) if m["status"] == "unavailable"]
    failed = [f"{o['agent_name']}: {'; '.join(o['errors']) or 'failed'}" for o in outputs if o["status"] == "failed"]
    disagreements = []
    if flagging and clean:
        disagreements.append(f"{', '.join(flagging)} flagged indicators while {', '.join(clean)} found none.")
    for f in cross_findings:
        if f["finding_type"] == "agent_disagreement":
            disagreements.append(f["description"])

    ml = next((o for o in outputs if o.get("agent_name") == "AI model"), None)
    ai_detection = None
    if ml:
        a = ml.get("artifacts", {})
        det = a.get("detector") or {}
        ai_detection = {"status": ml["status"], "verdict": a.get("verdict"), "verdict_text": a.get("verdict_text") or ml.get("interpretation"),
                        "mean_p_fake": (det.get("summary") or {}).get("mean"), "model": det.get("model"), "decision_grade": det.get("decision_grade"),
                        "confidence": a.get("confidence")}
        if a.get("verdict") == "likely_ai" and len(flagging) == 1 and flagging[0] == ml["agent_name"]:
            disagreements.append("Only the learned classifier indicates AI generation; classical forensic agents found no corroborating artefacts.")

    conf = [o["confidence"] for o in completed if o.get("confidence") is not None]
    signal_quality = round(sum(conf) / len(conf), 2) if conf else None
    uncertainty = "high" if (not completed or failed or unavailable or (signal_quality or 0) < 0.45 or disagreements) else "moderate"
    return {
        "label": label,
        "concern_level": LEVELS[level],
        "concern_rank": level,
        "indicator_count": len(inds),
        "max_severity": [k for k, v in SEVERITY_ORDER.items() if v == max_sev][0],
        "agents_flagging": flagging,
        "agents_clean": clean,
        "peak_heuristic_score": peak,
        "signal_quality": signal_quality,
        "uncertainty": uncertainty,
        "disagreements": disagreements,
        "unavailable_analyses": unavailable,
        "failed_analyses": failed,
        "ai_detection": ai_detection,
        "statement": (f"{LEVELS[level].capitalize()}: {len(inds)} indicator(s) from {len(flagging)} agent(s). "
                      "This is a triage indicator for human review, not a determination of authenticity."),
    }


def review(per_evidence: dict[str, dict], cross_out: dict, findings: list[dict]) -> dict:
    out = AgentOutput(REVIEW_NAME, VERSION)
    with Timer() as t:
        assessments = {}
        for eid, info in per_evidence.items():
            related = [f for f in findings if eid in f["evidence_ids"]]
            assessments[eid] = assess_evidence(info["label"], info["outputs"], related)
        ranks = [a["concern_rank"] for a in assessments.values()]
        overall = LEVELS[max(ranks, default=0)]
        all_disagreements = [d for a in assessments.values() for d in a["disagreements"]]
        out.observe("Evidence items reviewed", len(assessments))
        out.observe("Highest concern level", overall)
        out.observe("Agent disagreements", len(all_disagreements))
        out.interpretation = (f"Overall triage: {overall}. " + (f"{len(all_disagreements)} disagreement(s) between agents are preserved for the reviewer."
                                                                 if all_disagreements else "No inter-agent disagreements detected."))
        out.limitations += ["Concern levels are rule-based triage categories derived from uncalibrated heuristics.",
                            "No single score or agent determines authenticity; a qualified human reviewer must decide."]
        out.artifacts = {"assessments": assessments, "overall": overall, "disagreements": all_disagreements,
                         "cross_modal_summary": cross_out.get("interpretation", "")}
        out.confidence = None
    out.duration_ms = t.ms
    return out.to_dict()


def build_report_outline(case: dict, assessments: dict, indicators: list[dict], findings: list[dict]) -> dict:
    out = AgentOutput(REPORT_NAME, VERSION)
    with Timer() as t:
        checks: list[str] = []
        for i in indicators:
            for c in i.get("recommended_checks", []):
                if c not in checks:
                    checks.append(c)
        out.artifacts = {
            "case": case,
            "assessments": assessments,
            "flagged_locations": [{"evidence": i.get("evidence_label"), "type": i["indicator_type"], "location": i["location"], "severity": i["severity"]}
                                  for i in indicators if i["severity"] != "info"],
            "recommended_human_checks": checks[:15],
            "finding_count": len(findings),
        }
        out.observe("Indicators included", len(indicators))
        out.observe("Recommended checks", len(checks[:15]))
        out.interpretation = "Structured report outline assembled from stored agent results."
    out.duration_ms = t.ms
    return out.to_dict()

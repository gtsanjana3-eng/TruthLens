"""Professional forensic PDF report generation (ReportLab)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#0B1B33")
CYAN = colors.HexColor("#0E7490")
GOLD = colors.HexColor("#B7892B")
GREY = colors.HexColor("#5B6573")
LIGHT = colors.HexColor("#EEF2F7")

DISCLAIMER = ("This report presents automated forensic indicators to support human review. Scores are uncalibrated heuristic "
              "indicators, not probabilities or verdicts. No conclusion about authenticity should be drawn from any single "
              "indicator or agent. Analyses marked 'unavailable' were not performed and must not be read as clean results.")


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("TLTitle", parent=ss["Title"], textColor=NAVY, fontSize=22, leading=26))
    ss.add(ParagraphStyle("TLH1", parent=ss["Heading1"], textColor=NAVY, fontSize=15, spaceBefore=10, spaceAfter=6))
    ss.add(ParagraphStyle("TLH2", parent=ss["Heading2"], textColor=CYAN, fontSize=12, spaceBefore=8, spaceAfter=4))
    ss.add(ParagraphStyle("TLBody", parent=ss["BodyText"], fontSize=9, leading=12))
    ss.add(ParagraphStyle("TLSmall", parent=ss["BodyText"], fontSize=7.5, leading=9.5, textColor=GREY))
    ss.add(ParagraphStyle("TLCell", parent=ss["BodyText"], fontSize=7.5, leading=9.5))
    ss.add(ParagraphStyle("TLNote", parent=ss["BodyText"], fontSize=8.5, leading=11, backColor=LIGHT, borderPadding=6, textColor=NAVY))
    ss.add(ParagraphStyle("TLCenter", parent=ss["BodyText"], alignment=TA_CENTER, fontSize=8, textColor=GREY))
    return ss


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text if text is not None else "—")), style)


def _table(rows: list[list], widths: list[float], ss) -> Table:
    header = [Paragraph(f"<font color='white'><b>{escape(str(c))}</b></font>", ss["TLCell"]) for c in rows[0]]
    data = [header] + [[_p(c, ss["TLCell"]) if not isinstance(c, (Paragraph, Image)) else c for c in r] for r in rows[1:]]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DE")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, LIGHT]), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def fmt_location(loc: dict) -> str:
    if not loc:
        return "—"
    if "t_start" in loc:
        s = f"{loc['t_start']:.2f}s–{loc.get('t_end', loc['t_start']):.2f}s"
        if "frame" in loc:
            s += f" (frame {loc['frame']})"
        return s
    if "x" in loc:
        return f"region x={loc['x']}, y={loc['y']}, {loc['w']}×{loc['h']}px"
    if "frames" in loc:
        return f"frames {', '.join(map(str, loc['frames'][:6]))}"
    if "field" in loc:
        return f"metadata field: {loc['field']}"
    return ", ".join(f"{k}={v}" for k, v in list(loc.items())[:3])


def build_pdf(dest: Path, ctx: dict) -> None:
    """ctx keys: case, generated_by, evidence[], job, agent_results[], indicators[], findings[], history[], audit[], images{eid: path}"""
    ss = _styles()
    case = ctx["case"]
    gen_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, A4[1] - 12 * mm, A4[0], 12 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(15 * mm, A4[1] - 8 * mm, "TRUTHLENS AI · FORENSIC ANALYSIS REPORT")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(A4[0] - 15 * mm, A4[1] - 8 * mm, f"Case {case['reference']}")
        canvas.setFillColor(GREY)
        canvas.drawString(15 * mm, 8 * mm, f"Generated {gen_time} · Indicators require human review")
        canvas.drawRightString(A4[0] - 15 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(dest), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=18 * mm, bottomMargin=16 * mm,
                            title=f"TruthLens forensic report {case['reference']}", author="TruthLens AI")
    W = A4[0] - 30 * mm
    el: list = []
    el += [Spacer(1, 8 * mm), _p("Forensic Analysis Report", ss["TLTitle"]), _p(case["title"], ss["TLH2"])]
    meta = [["Field", "Value"], ["Case reference", case["reference"]], ["Status", case["status"]], ["Priority", case["priority"]],
            ["Generated", gen_time], ["Generated by", ctx["generated_by"]],
            ["Analysis job", f"{ctx['job']['id']} ({ctx['job']['status']}, finished {ctx['job'].get('finished_at') or '—'})" if ctx.get("job") else "No analysis run"]]
    el += [_table(meta, [45 * mm, W - 45 * mm], ss), Spacer(1, 4 * mm), Paragraph(f"<b>Important:</b> {escape(DISCLAIMER)}", ss["TLNote"])]
    if case.get("description"):
        el += [_p("Case description", ss["TLH2"]), _p(case["description"], ss["TLBody"])]
    if case.get("context_statement"):
        el += [_p("Claimed context (as supplied)", ss["TLH2"]), _p(case["context_statement"], ss["TLBody"])]

    # Summary
    summary = (ctx.get("job") or {}).get("summary") or {}
    el += [_p("1. Summary of findings", ss["TLH1"])]
    if summary:
        el.append(_p(f"Overall triage level: {summary.get('overall', '—')}. {summary.get('indicator_count', 0)} indicator(s) and "
                     f"{summary.get('finding_count', 0)} cross-modal finding(s). {summary.get('cross_modal', '')}", ss["TLBody"]))
        rows = [["Evidence", "Concern level", "AI model result", "Confidence (measured)", "Agents flagging", "Uncertainty"]]
        for a in summary.get("assessments", {}).values():
            ai = a.get("ai_detection") or {}
            conf = ai.get("confidence") or {}
            rows.append([a["label"], a["concern_level"], ai.get("verdict_text") or "–",
                         f"{conf['level']} ({conf['value'] * 100:.0f}%)" if conf.get("value") is not None else "not stated",
                         ", ".join(a["agents_flagging"]) or "none", a["uncertainty"]])
        el.append(_table(rows, [14 * mm, 24 * mm, 46 * mm, 26 * mm, W - 130 * mm, 20 * mm], ss))
        confs = [(a["label"], (a.get("ai_detection") or {}).get("confidence") or {}) for a in summary.get("assessments", {}).values()]
        for label, c in confs:
            if c.get("basis"):
                el.append(_p(f"{label} – confidence basis: {c['basis']}", ss["TLSmall"]))
        if summary.get("disagreements"):
            el += [_p("Agent disagreements (preserved, not resolved automatically)", ss["TLH2"])]
            el += [_p(f"• {d}", ss["TLBody"]) for d in summary["disagreements"]]
    else:
        el.append(_p("No completed analysis is available for this case.", ss["TLBody"]))

    # Evidence & integrity
    el += [_p("2. Evidence register and integrity", ss["TLH1"])]
    rows = [["Ref", "File", "Type", "Size", "SHA-256", "Uploaded", "Integrity"]]
    for e in ctx["evidence"]:
        rows.append([e["label"], e["original_filename"], e["media_type"], f"{e['size_bytes'] / 1024:.1f} KB", e["sha256"], e["created_at"][:19],
                     e.get("integrity", "not re-verified")])
    el.append(_table(rows, [10 * mm, 34 * mm, 13 * mm, 16 * mm, 62 * mm, 26 * mm, W - 161 * mm], ss))

    # Indicators
    el += [_p("3. Manipulation indicators and flagged locations", ss["TLH1"])]
    if ctx["indicators"]:
        rows = [["Evidence", "Category", "Indicator", "Severity", "Location", "Description", "Agent"]]
        for i in ctx["indicators"]:
            rows.append([i["evidence_label"], i["category"], i["indicator_type"].replace("_", " "), i["severity"].upper(), fmt_location(i["location"]),
                         i["description"], i["agent_name"]])
        el.append(_table(rows, [13 * mm, 15 * mm, 25 * mm, 15 * mm, 28 * mm, W - 131 * mm, 35 * mm], ss))
    else:
        el.append(_p("No indicators were raised. This does not establish authenticity.", ss["TLBody"]))

    for eid, img_path in (ctx.get("images") or {}).items():
        if Path(img_path).exists():
            label = next((e["label"] for e in ctx["evidence"] if e["id"] == eid), eid)
            im = Image(str(img_path))
            ratio = im.imageHeight / float(im.imageWidth)
            im.drawWidth, im.drawHeight = 110 * mm, 110 * mm * ratio
            el.append(KeepTogether([_p(f"Suspicion heatmap – {label}", ss["TLH2"]), im,
                                    _p("Built only from measured block-level ELA/noise outliers and copy-move matches; not a model probability map.", ss["TLSmall"])]))

    # Cross-modal
    el += [_p("4. Cross-modal correlation", ss["TLH1"])]
    if ctx["findings"]:
        rows = [["Finding", "Effect on concern", "Description", "Confidence"]]
        for f in ctx["findings"]:
            rows.append([f["finding_type"].replace("_", " "), f["effect"], f["description"], f"{f['confidence']:.2f}" if f.get("confidence") is not None else "—"])
        el.append(_table(rows, [32 * mm, 24 * mm, W - 76 * mm, 20 * mm], ss))
    else:
        el.append(_p("No cross-modal findings.", ss["TLBody"]))

    # Alternatives & checks
    el += [_p("5. Alternative explanations and recommended human checks", ss["TLH1"])]
    seen = set()
    for i in ctx["indicators"]:
        if i["indicator_type"] in seen:
            continue
        seen.add(i["indicator_type"])
        el.append(_p(i["indicator_type"].replace("_", " ").capitalize(), ss["TLH2"]))
        el += [_p(f"Alternative: {a}", ss["TLBody"]) for a in i["alternative_explanations"]]
        el += [_p(f"Check: {c}", ss["TLBody"]) for c in i["recommended_checks"]]
    if not seen:
        el.append(_p("Not applicable – no indicators raised.", ss["TLBody"]))

    # Agents
    el += [PageBreak(), _p("6. Agent results, confidence and limitations", ss["TLH1"])]
    for r in ctx["agent_results"]:
        block = [_p(f"{r['agent_name']} v{r['agent_version']} – {r['status']} ({r.get('evidence_label') or 'case-level'})", ss["TLH2"]),
                 _p(f"Heuristic score: {r['suspicion_score'] if r['suspicion_score'] is not None else 'n/a'} (uncalibrated) · "
                    f"signal quality: {r['confidence'] if r['confidence'] is not None else 'n/a'} · run at {r['created_at'][:19]} · {r['duration_ms']} ms", ss["TLSmall"]),
                 _p(f"Interpretation: {r['interpretation']}", ss["TLBody"])]
        obs = [o for o in r["observations"] if not isinstance(o.get("value"), (dict, list))][:10]
        if obs:
            block.append(_p("Observed: " + "; ".join(f"{o['label']}: {o['value']}{(' ' + o['unit']) if o.get('unit') else ''}" for o in obs), ss["TLSmall"]))
        for lim in r["limitations"][:6]:
            block.append(_p(f"Limitation: {lim}", ss["TLSmall"]))
        for err in r["errors"]:
            block.append(_p(f"Error: {err}", ss["TLSmall"]))
        el.append(KeepTogether(block))

    # History
    el += [_p("7. Analysis history and audit trail", ss["TLH1"])]
    rows = [["Job", "Status", "Started", "Finished"]] + [[h["id"], h["status"], h.get("started_at") or "—", h.get("finished_at") or "—"] for h in ctx["history"]]
    el.append(_table(rows, [55 * mm, 25 * mm, 50 * mm, W - 130 * mm], ss))
    if ctx["audit"]:
        el.append(Spacer(1, 3 * mm))
        rows = [["Time", "Action", "Detail", "Entry hash"]] + [[a["created_at"][:19], a["action"], str(a["detail"])[:90], a["entry_hash"][:16] + "…"] for a in ctx["audit"][-40:]]
        el.append(_table(rows, [30 * mm, 35 * mm, W - 95 * mm, 30 * mm], ss))
    el += [Spacer(1, 6 * mm), _p("End of report. The SHA-256 of this PDF is recorded in the TruthLens database for verification.", ss["TLCenter"])]
    doc.build(el, onFirstPage=on_page, onLaterPages=on_page)

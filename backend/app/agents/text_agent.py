"""Text & Context Analysis Agent.

Parses captions, statements and transcripts (plain text, SRT, WebVTT) into structured
claims (dates, times, durations, locations, devices, time-of-day, speaker turns) used by
the Cross-Modal Correlation Agent, and checks the text for internal contradictions.
Text content is treated strictly as data – it is never executed or followed as instructions."""
from __future__ import annotations

import re
from datetime import date, datetime

from .base import AgentOutput, Indicator, ModelCheck, Signal, Timer, aggregate_signals, clamp01
from .explanations import enrich

NAME = "Text Analysis Agent"
VERSION = "1.1.1"

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
TS = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
CUE = re.compile(TS + r"\s*-->\s*" + TS)
DEVICES = ["iphone", "samsung", "pixel", "galaxy", "huawei", "xiaomi", "oneplus", "canon", "nikon", "sony", "gopro", "dji", "fujifilm", "panasonic", "olympus", "dashcam", "cctv", "bodycam", "webcam", "drone"]
NIGHT = ["at night", "midnight", "after dark", "nighttime", "night-time", "late night", "in the dark"]
DAY = ["in daylight", "broad daylight", "at noon", "midday", "in the morning", "sunny afternoon", "daytime"]
SENSATIONAL = ["shocking", "you won't believe", "breaking", "exposed", "leaked", "share before", "they don't want you", "100% proof", "urgent", "must watch"]
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30}


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_cues(text: str) -> list[dict]:
    cues = []
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n"))
    for b in blocks:
        m = CUE.search(b)
        if not m:
            continue
        g = m.groups()
        body = " ".join(line.strip() for line in b[m.end():].strip().split("\n") if line.strip())
        cues.append({"start": _secs(*g[:4]), "end": _secs(*g[4:]), "text": re.sub(r"<[^>]+>", "", body)[:500]})
    return cues


def extract_dates(text: str) -> list[dict]:
    found = []
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        found.append((m, (int(m[1]), int(m[2]), int(m[3]))))
    for m in re.finditer(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")[a-z]*,?\s+(\d{4})\b", text, re.I):
        found.append((m, (int(m[3]), MONTHS[m[2].lower()[:3]], int(m[1]))))
    for m in re.finditer(r"\b(" + "|".join(MONTHS) + r")[a-z]*\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b", text, re.I):
        found.append((m, (int(m[3]), MONTHS[m[1].lower()[:3]], int(m[2]))))
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        found.append((m, (y, b, a) if a > 12 else (y, a, b)))  # ambiguous DD/MM vs MM/DD handled below
    out, seen = [], set()
    for m, (y, mo, d) in found:
        try:
            dt = date(y, mo, d)
        except ValueError:
            continue
        if dt.isoformat() in seen:
            continue
        seen.add(dt.isoformat())
        out.append({"date": dt.isoformat(), "text": m.group(0), "pos": m.start(), "ambiguous": "/" in m.group(0) and int(m.group(0).split("/")[0]) <= 12 and int(m.group(0).split("/")[1]) <= 12})
    return out


def extract_durations(text: str) -> list[dict]:
    out = []
    for m in re.finditer(r"\b(\d+(?:\.\d+)?|" + "|".join(NUMBER_WORDS) + r")[- ](second|sec|minute|min|hour)s?\b(?:\s+(?:long|video|clip|recording|audio|footage))", text, re.I):
        n = float(NUMBER_WORDS.get(m[1].lower(), m[1]) if m[1].lower() in NUMBER_WORDS else m[1])
        unit = m[2].lower()
        out.append({"seconds": n * (3600 if unit.startswith("hour") else 60 if unit.startswith("min") else 1), "text": m.group(0)})
    return out


def extract_claims(text: str) -> dict:
    low = text.lower()
    times = [m.group(0) for m in re.finditer(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\s?(?:am|pm)?\b|\b(?:1[0-2]|[1-9])\s?(?:am|pm)\b", low)]
    locs = list(dict.fromkeys(m.group(1) for m in re.finditer(r"\b(?:in|at|near|from)\s+((?:[A-Z][a-zA-Z]+)(?:\s+[A-Z][a-zA-Z]+){0,2})", text)))
    months = {"January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"}
    locs = [l for l in locs if l.split()[0] not in months][:10]
    return {
        "dates": extract_dates(text),
        "times": list(dict.fromkeys(times))[:10],
        "durations": extract_durations(text),
        "locations": locs,
        "devices": [d for d in DEVICES if re.search(r"\b" + d + r"\b", low)],
        "time_of_day": "night" if any(k in low for k in NIGHT) else "day" if any(k in low for k in DAY) else None,
        "speakers": list(dict.fromkeys(m.group(1) for m in re.finditer(r"^\s*([A-Z][\w .'-]{1,30}):\s", text, re.M)))[:10],
        "sensational_terms": [k for k in SENSATIONAL if k in low],
        "negated_media_claims": bool(re.search(
            r"\b(not|never|no|nothing|isn't|wasn't|hasn't|isnt|wasnt|hasnt)\b[\w\s']{0,25}?\b(edited|altered|manipulated|photoshopped|doctored|tampered|faked|ai[- ]generated)\b", low)),
    }


def analyze(text: str, evidence_id: str | None, source: str = "evidence") -> dict:
    out = AgentOutput(NAME, VERSION, evidence_id=evidence_id)
    with Timer() as t:
        text = text[:200_000]
        cues = parse_cues(text)
        plain = " ".join(c["text"] for c in cues) if cues else text
        claims = extract_claims(plain)
        words = re.findall(r"[A-Za-z']+", plain.lower())
        out.observe("Source", source)
        out.observe("Format", "timed transcript (SRT/VTT)" if cues else "plain text")
        out.observe("Word count", len(words))
        if cues:
            out.observe("Transcript cues", len(cues))
            out.observe("Transcript span", f"{cues[0]['start']:.2f}s – {cues[-1]['end']:.2f}s")
        for key in ("dates", "times", "durations", "locations", "devices", "time_of_day", "speakers"):
            if claims[key]:
                val = claims[key]
                out.observe(f"Claimed {key.replace('_', ' ')}", [v.get("date", v.get("text")) if isinstance(v, dict) else v for v in val] if isinstance(val, list) else val)

        # Internal consistency
        dates = claims["dates"]
        if len(dates) >= 2:
            ds = sorted(datetime.fromisoformat(d["date"]) for d in dates)
            span = (ds[-1] - ds[0]).days
            if span > 1:
                out.indicators.append(enrich(Indicator("text", "text_internal_conflict", "low",
                    f"Text references {len(dates)} different dates spanning {span} days: {', '.join(d['text'] for d in dates[:4])}",
                    location={"source": source, "dates": [d["date"] for d in dates]}, score=clamp01(span / 30))))
                out.signals.append(Signal("Conflicting dates in text", clamp01(span / 30), 0.5, "suspicious"))
        if any(d["ambiguous"] for d in dates):
            out.limitations.append("Some dates are ambiguous (DD/MM vs MM/DD); interpreted as MM/DD.")
        if cues:
            overlaps = sum(1 for a, b in zip(cues, cues[1:]) if b["start"] < a["end"] - 0.05)
            backwards = sum(1 for c in cues if c["end"] < c["start"])
            if overlaps or backwards:
                out.indicators.append(enrich(Indicator("text", "transcript_alignment", "info",
                    f"Transcript timing irregularities: {overlaps} overlapping and {backwards} reversed cue(s)", location={"source": source}, score=0.2)))
        if claims["sensational_terms"]:
            out.indicators.append(enrich(Indicator("text", "sensational_language", "info",
                f"Persuasive/sensational wording: {', '.join(claims['sensational_terms'])}", location={"source": source}, score=0.2)))
            out.signals.append(Signal("Sensational framing", 0.2, 0.2, "suspicious", "Context risk, not media manipulation"))
        if len(words) > 50:
            ttr = len(set(words)) / len(words)
            out.observe("Lexical diversity (type/token)", round(ttr, 3))

        out.model_checks.append(ModelCheck("Large-language-model claim verification", "unavailable",
                                           "No external LLM is configured; claims are extracted with deterministic rules only."))
        out.suspicion_score = aggregate_signals(out.signals)
        out.confidence = 0.6
        out.artifacts = {"claims": claims, "cues": cues[:2000], "source": source}
        out.limitations += ["Rule-based claim extraction may miss paraphrased or implicit claims.",
                            "Location names are extracted but cannot be geocoded offline.",
                            "Text plausibility is not fact-checked against external sources."]
        out.interpretation = ("Extracted claims for cross-checking against media; "
                              + (f"{len(out.indicators)} textual observation(s) noted." if out.indicators else "no internal contradictions detected."))
    out.duration_ms = t.ms
    return out.to_dict()

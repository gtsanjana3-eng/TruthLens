"""Shared structures for forensic agents.

Every agent returns an AgentOutput. Key rules enforced here:
* `observations` are measured facts; `interpretation` is the agent's reading of them.
* `suspicion_score` is an uncalibrated heuristic indicator in [0, 1], never a probability
  that media is fake. `None` means the agent could not produce a score.
* Unavailable models/analyses are reported with status "unavailable" – never faked.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITY_ORDER = {"info": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class Indicator:
    category: str  # region | frame | segment | metadata | text | sync
    indicator_type: str
    severity: str
    description: str
    location: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    alternative_explanations: list[str] = field(default_factory=list)
    recommended_checks: list[str] = field(default_factory=list)


@dataclass
class Signal:
    name: str
    value: float | str | None
    weight: float  # contribution weight used in the heuristic aggregate
    direction: str  # "suspicious" | "neutral" | "reassuring"
    note: str = ""


@dataclass
class ModelCheck:
    name: str
    status: str  # completed | unavailable
    detail: str


@dataclass
class AgentOutput:
    agent_name: str
    agent_version: str
    status: str = "completed"
    evidence_id: str | None = None
    suspicion_score: float | None = None
    confidence: float | None = None
    observations: list[dict[str, Any]] = field(default_factory=list)
    interpretation: str = ""
    signals: list[Signal] = field(default_factory=list)
    indicators: list[Indicator] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    model_checks: list[ModelCheck] = field(default_factory=list)
    duration_ms: int = 0

    def observe(self, label: str, value: Any, unit: str | None = None) -> None:
        self.observations.append({"label": label, "value": value, **({"unit": unit} if unit else {})})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AgentOutput":
        d = dict(d)
        d["signals"] = [Signal(**s) for s in d.get("signals", [])]
        d["indicators"] = [Indicator(**i) for i in d.get("indicators", [])]
        d["model_checks"] = [ModelCheck(**m) for m in d.get("model_checks", [])]
        return cls(**d)


def aggregate_signals(signals: list[Signal]) -> float | None:
    """Weighted mean of numeric suspicious-direction signal strengths (each already in [0,1]).

    Reassuring signals pull the score down. This is a transparent heuristic, not a
    trained/calibrated classifier, and is labelled as such everywhere it is shown."""
    num = den = 0.0
    for s in signals:
        if not isinstance(s.value, (int, float)) or s.weight <= 0:
            continue
        v = max(0.0, min(1.0, float(s.value)))
        if s.direction == "reassuring":
            v = 0.0
        elif s.direction == "neutral":
            continue
        num += s.weight * v
        den += s.weight
    if den == 0:
        return None
    return round(num / den, 4)


def clamp01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def robust_z(values, eps: float = 1e-9):
    """Robust z-scores using median / MAD."""
    import numpy as np

    v = np.asarray(values, dtype=np.float64)
    if v.size == 0:
        return v
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826
    if mad < eps:
        std = v.std()
        mad = std if std > eps else eps
    return (v - med) / mad


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.perf_counter() - self.t0) * 1000)

"""Verification of factual claims against computed metrics and source events.

A claim is ``verified`` only when every check passes. Anything else is ``unsupported``
with explicit reasons, so callers can suppress or flag it instead of presenting it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import HEURISTIC, Metric
from .schema import Match

VERIFIED = "verified"
UNSUPPORTED = "unsupported"
VALUE_TOLERANCE = 5e-4  # ratios are stored to 4 dp, xG totals to 3 dp
HEURISTIC_MARKERS = ("heuristic", "synthetic")


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    metric_id: str | None = None
    value: float | int | None = None
    cited_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimCheck:
    claim: Claim
    status: str
    reasons: tuple[str, ...]

    @property
    def verified(self) -> bool:
        return self.status == VERIFIED


def verify_claim(claim: Claim, metrics: dict[str, Metric], match: Match) -> ClaimCheck:
    reasons: list[str] = []
    event_ids = {e.event_id for e in match.events}
    if claim.metric_id is None and not claim.cited_event_ids:
        reasons.append("claim cites no metric and no events")
    missing = [i for i in claim.cited_event_ids if i not in event_ids]
    if missing:
        reasons.append(f"cited events not in match: {missing}")

    if claim.metric_id is not None:
        metric = metrics.get(claim.metric_id)
        if metric is None:
            reasons.append(f"unknown metric {claim.metric_id!r}")
        else:
            if metric.value is None:
                reasons.append(f"metric {metric.metric_id} is undefined for this match")
            elif claim.value is None:
                reasons.append("claim cites a metric but states no value")
            elif abs(float(claim.value) - float(metric.value)) > VALUE_TOLERANCE:
                reasons.append(f"claimed {claim.value} but {metric.metric_id} = {metric.value}")
            sources = set(metric.source_event_ids)
            outside = [i for i in claim.cited_event_ids if i in event_ids and i not in sources]
            if outside:
                reasons.append(f"cited events are not sources of {metric.metric_id}: {outside}")
            text = claim.text.lower()
            if metric.kind == HEURISTIC and not any(m in text for m in HEURISTIC_MARKERS):
                reasons.append("heuristic metric must be described as heuristic/synthetic")

    return ClaimCheck(claim, UNSUPPORTED if reasons else VERIFIED, tuple(reasons))

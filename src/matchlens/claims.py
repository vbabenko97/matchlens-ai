"""Verification of factual claims against computed metrics and source events.

Design: a ``Claim`` is *structured* (metric ID, value, cited event IDs) and deliberately
has no free-text field. Verification checks the structure; wording is then rendered
from fixed templates using only verified data (``ClaimCheck.text``). Free prose — for
example LLM output — is never verified: ``assess_prose`` always returns ``unverified``,
because we cannot machine-check the meaning of arbitrary text. UIs must show only
rendered statements as facts and label any prose as unverified wording.
"""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import HEURISTIC, Metric
from .schema import Event, Match, minute_of

VERIFIED = "verified"
UNSUPPORTED = "unsupported"
UNVERIFIED = "unverified"
VALUE_TOLERANCE = 5e-4  # ratios are stored to 4 dp, xG totals to 3 dp
HEURISTIC_NOTE = "synthetic heuristic, not empirically calibrated"


@dataclass(frozen=True, slots=True)
class Claim:
    metric_id: str | None = None
    value: float | int | None = None
    cited_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ClaimCheck:
    claim: Claim
    status: str
    reasons: tuple[str, ...]
    text: str | None  # template-rendered statement; only set when verified

    @property
    def verified(self) -> bool:
        return self.status == VERIFIED


@dataclass(frozen=True, slots=True)
class ProseAssessment:
    text: str
    status: str
    reason: str


def assess_prose(text: str) -> ProseAssessment:
    """Free text is never treated as verified, whether it happens to be true or false."""
    return ProseAssessment(
        text=text,
        status=UNVERIFIED,
        reason="free text is not machine-verified; only template-rendered claims are facts",
    )


def format_value(metric: Metric) -> str:
    if metric.value is None:
        return "undefined"
    if metric.unit == "ratio":
        return f"{metric.value * 100:.1f}%"
    if metric.unit == "xg":
        return f"{metric.value:.2f}"
    return str(metric.value)


def render_metric(metric: Metric, match: Match) -> str:
    team = match.meta.team(metric.team).name
    text = f"{team} — {metric.label}: {format_value(metric)}"
    span = []
    if metric.window is not None:
        lo, hi = metric.window
        span.append(f"between {int(lo // 60)}' and {int(hi // 60)}'")
    if metric.as_of_event_id is not None:
        clock = next(e.clock_s for e in match.events if e.event_id == metric.as_of_event_id)
        span.append(f"as of {minute_of(clock)}'")
    if span:
        text += f" ({', '.join(span)})"
    if metric.kind == HEURISTIC:
        text += f" [{HEURISTIC_NOTE}]"
    return text


def player_name(match: Match, side: str | None, player_id: str | None) -> str:
    if side is None or player_id is None:
        return "unknown player"
    return next((p.name for p in match.meta.team(side).players if p.player_id == player_id), "?")


def render_event(event: Event, match: Match) -> str:
    who = ""
    if event.team is not None:
        name = player_name(match, event.team, event.player_id)
        who = f" {match.meta.team(event.team).name}, {name}:"
    what = event.type.replace("_", " ")
    if event.subtype and event.subtype != "open_play":
        what = event.subtype.replace("_", " ")
    if event.outcome:
        what += f" ({event.outcome.replace('_', ' ')})"
    return f"{minute_of(event.clock_s)}'{who} {what}"


def verify_claim(claim: Claim, metrics: dict[str, Metric], match: Match) -> ClaimCheck:
    reasons: list[str] = []
    by_id = {e.event_id: e for e in match.events}
    if claim.metric_id is None and not claim.cited_event_ids:
        reasons.append("claim cites no metric and no events")
    missing = [i for i in claim.cited_event_ids if i not in by_id]
    if missing:
        reasons.append(f"cited events not in match: {missing}")

    metric = metrics.get(claim.metric_id) if claim.metric_id is not None else None
    if claim.metric_id is not None:
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
            outside = [i for i in claim.cited_event_ids if i in by_id and i not in sources]
            if outside:
                reasons.append(f"cited events are not sources of {metric.metric_id}: {outside}")
    elif claim.value is not None:
        reasons.append("a value needs a metric to be checked against")

    if reasons:
        return ClaimCheck(claim, UNSUPPORTED, tuple(reasons), None)
    parts = [render_metric(metric, match)] if metric is not None else []
    parts += [render_event(by_id[i], match) for i in claim.cited_event_ids]
    refs = ([f"metric {metric.metric_id}"] if metric is not None else []) + list(
        claim.cited_event_ids
    )
    text = "; ".join(parts) + f" [evidence: {', '.join(refs)}]"
    return ClaimCheck(claim, VERIFIED, (), text)

"""Bounded claim agent: an AI drafts structured claims, a deterministic validator decides.

For one analyst card, a proposer drafts up to ``MAX_CLAIMS`` structured claims (a metric ID
with a value and/or cited event IDs; never free text) about the card's moment.
``FoundryAgentProposer`` does this with a Microsoft Foundry / Azure OpenAI deployment
(Responses API v1) that may call three read-only tools, ``list_metrics``, ``get_metric`` and
``get_events``. The tools only see the match as of the card's publication (its last
evidence event), so the agent cannot look ahead.

``review_card`` checks every claim with ``verify_claim`` against that same state. Rejected
claims go back to the proposer once (``MAX_RETRIES``) with the validator's reasons; anything
still rejected is dropped. When a claim names a real metric but gets the value or sources
wrong, the computed value is published instead as a correction. Published facts are always
rendered from templates: the model chooses what to claim, never the wording of a fact.

Claim sources are labelled everywhere: the Foundry agent (live, or replayed from a recorded
trace), the offline deterministic proposer used without Azure, and injected test claims,
which are deliberately wrong and only exercise the validator.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from .cards import CardSet, Fact, InsightCard
from .claims import VERIFIED, Claim, ClaimCheck, player_name, verify_claim
from .metrics import DEFAULT_WINDOW_S, FACT, Metric, as_of_id, compute_metrics
from .narrative import (
    AzureOpenAIConfig,
    ResponseFormatError,
    Transport,
    extract_output_text,
    post_responses,
)
from .schema import Event, Match, minute_of

FOUNDRY = "foundry_agent"
RECORDED = "recorded_foundry_agent"
OFFLINE = "offline_proposer"
INJECTED = "injected_test_claim"
REJECTED = "rejected"
MAX_CLAIMS = 4
MAX_RETRIES = 1
MAX_MODEL_CALLS = 6  # per attempt, tool rounds included
MAX_TOOL_CALLS = 16  # per attempt
MAX_EVENTS_PER_CALL = 25
MAX_OUTPUT_TOKENS = 1000
TRACE_SCHEMA = "matchlens.agent-trace/v1"
OFFLINE_LABEL = "Offline deterministic proposer — no AI; drafts the card's own metric claims."
INJECTED_LABEL = "Injected test claim — deliberately wrong, to show the validator rejecting it."


class AgentError(RuntimeError):
    """The agent did not produce a usable answer within its limits (fixed messages only)."""


@dataclass(frozen=True)
class ReviewContext:
    """Everything a reviewer may know about a card: the match up to its publication."""

    card: InsightCard
    match: Match  # events up to and including the card's last evidence event
    metrics: dict[str, Metric]  # computed as of that event
    later_event_ids: frozenset[str]  # events after publication (never shown to the agent)

    @property
    def as_of_event_id(self) -> str:
        return self.card.evidence_event_ids[-1]


def review_context(match: Match, card: InsightCard) -> ReviewContext:
    last = next(e for e in match.events if e.event_id == card.evidence_event_ids[-1])
    so_far = Match(match.meta, match.events[: last.seq])
    later = frozenset(e.event_id for e in match.events[last.seq :])
    metrics = compute_metrics(so_far, DEFAULT_WINDOW_S, as_of_event_id=last.event_id)
    return ReviewContext(card, so_far, metrics, later)


# --- Read-only agent tools (all bound to the match as of publication) ------------------


def _event_record(ctx: ReviewContext, e: Event) -> dict[str, Any]:
    return {
        "event_id": e.event_id,
        "minute": minute_of(e.clock_s),
        "clock_s": e.clock_s,
        "type": e.type,
        "subtype": e.subtype,
        "team": ctx.match.meta.team(e.team).name if e.team else None,
        "player": player_name(ctx.match, e.team, e.player_id) if e.player_id else None,
        "outcome": e.outcome,
        "possession_id": e.possession_id,
        "score": [e.home_score, e.away_score],
    }


def list_metrics(ctx: ReviewContext) -> list[dict[str, Any]]:
    return [
        {"metric_id": m.metric_id, "label": m.label, "team": m.team, "window": m.window}
        for m in ctx.metrics.values()
    ]


def get_metric(ctx: ReviewContext, metric_id: str) -> dict[str, Any]:
    m = ctx.metrics.get(metric_id)
    if m is None:
        return {"error": f"unknown metric {metric_id!r}; call list_metrics for valid IDs"}
    keys = ("metric_id", "label", "team", "value", "unit", "kind", "definition", "window")
    record = m.to_record()
    return {**{k: record[k] for k in keys}, "source_event_ids": list(m.source_event_ids)}


def get_events(ctx: ReviewContext, event_ids: list[str]) -> list[dict[str, Any]]:
    by_id = {e.event_id: e for e in ctx.match.events}
    return [
        _event_record(ctx, by_id[i]) if i in by_id else {"event_id": i, "error": "not available"}
        for i in event_ids[:MAX_EVENTS_PER_CALL]
    ]


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOL_SPECS = [
    {
        "type": "function",
        "name": "list_metrics",
        "description": "List the metric IDs available when the card is published.",
        "parameters": _object({}),
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_metric",
        "description": "Value, definition and source event IDs of one metric.",
        "parameters": _object({"metric_id": {"type": "string"}}),
        "strict": True,
    },
    {
        "type": "function",
        "name": "get_events",
        "description": f"Details of up to {MAX_EVENTS_PER_CALL} events that have happened.",
        "parameters": _object({"event_ids": {"type": "array", "items": {"type": "string"}}}),
        "strict": True,
    },
]

CLAIMS_FORMAT = {
    "type": "json_schema",
    "name": "claims",
    "strict": True,
    "schema": _object(
        {
            "claims": {
                "type": "array",
                "items": _object(
                    {
                        "metric_id": {"type": ["string", "null"]},
                        "value": {"type": ["number", "null"]},
                        "cited_event_ids": {"type": "array", "items": {"type": "string"}},
                    }
                ),
            }
        }
    ),
}


def run_tool(ctx: ReviewContext, name: str, arguments: str) -> Any:
    try:
        args = json.loads(arguments)
    except json.JSONDecodeError:
        return {"error": "arguments are not valid JSON"}
    if not isinstance(args, dict):
        return {"error": "arguments must be a JSON object"}
    if name == "list_metrics":
        return list_metrics(ctx)
    if name == "get_metric" and isinstance(args.get("metric_id"), str):
        return get_metric(ctx, args["metric_id"])
    ids = args.get("event_ids")
    if name == "get_events" and isinstance(ids, list) and all(isinstance(i, str) for i in ids):
        return get_events(ctx, ids)
    return {"error": f"unknown tool or bad arguments for {name!r}"}


# --- Verdicts --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimVerdict:
    source: str  # FOUNDRY, RECORDED, OFFLINE or INJECTED
    attempt: int  # 1 or 2 for proposer claims, 0 for injected test claims
    claim: Claim
    status: str  # VERIFIED or REJECTED
    reasons: tuple[str, ...] = ()
    fact: Fact | None = None  # the rendered fact, when verified
    correction: Fact | None = None  # rendered from the computed metric, when rejected

    @property
    def verified(self) -> bool:
        return self.status == VERIFIED


def _fact(check: ClaimCheck, ctx: ReviewContext) -> Fact:
    assert check.text is not None
    metric = ctx.metrics.get(check.claim.metric_id) if check.claim.metric_id else None
    return Fact(
        check.text,
        metric.kind if metric else FACT,
        check.claim.cited_event_ids,
        (metric.metric_id,) if metric else (),
    )


def check_claim(ctx: ReviewContext, claim: Claim, source: str, attempt: int) -> ClaimVerdict:
    check = verify_claim(claim, ctx.metrics, ctx.match)
    if check.verified:
        return ClaimVerdict(source, attempt, claim, VERIFIED, fact=_fact(check, ctx))
    reasons = list(check.reasons)
    later = [i for i in claim.cited_event_ids if i in ctx.later_event_ids]
    if later:
        reasons.append(f"cited events happen after the card is published: {later}")
    correction = None
    metric = ctx.metrics.get(claim.metric_id) if claim.metric_id else None
    if metric is not None and metric.value is not None:
        sources = set(metric.source_event_ids)
        cited = tuple(i for i in claim.cited_event_ids if i in sources)
        fixed = verify_claim(Claim(metric.metric_id, metric.value, cited), ctx.metrics, ctx.match)
        correction = _fact(fixed, ctx) if fixed.verified else None
    return ClaimVerdict(source, attempt, claim, REJECTED, tuple(reasons), correction=correction)


def claim_record(claim: Claim) -> dict[str, Any]:
    return {
        "metric_id": claim.metric_id,
        "value": claim.value,
        "cited_event_ids": list(claim.cited_event_ids),
    }


# --- Proposers -------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    claims: tuple[Claim, ...]
    calls: tuple[dict[str, Any], ...] = ()  # one per model call: IDs, usage, tool calls
    dropped: int = 0  # claims beyond MAX_CLAIMS, ignored


class ClaimProposer(Protocol):
    name: str
    label: str

    def propose(self, ctx: ReviewContext, feedback: Sequence[ClaimVerdict] = ()) -> Proposal: ...


class OfflineProposer:
    """Deterministic fallback: claims the card's own window metrics at their computed values."""

    name = OFFLINE
    label = OFFLINE_LABEL

    def propose(self, ctx: ReviewContext, feedback: Sequence[ClaimVerdict] = ()) -> Proposal:
        ids = [m for f in ctx.card.facts for m in f.metric_ids]
        return Proposal(tuple(Claim(m, ctx.metrics[m].value) for m in ids[:MAX_CLAIMS]))


def parse_claims(text: str) -> tuple[tuple[Claim, ...], int]:
    """Parse the agent's final JSON answer; returns ``(claims, dropped)``."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        raise ResponseFormatError("final answer is not JSON") from None
    items = doc.get("claims") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        raise ResponseFormatError("final answer has no claims array")
    claims = []
    for item in items:
        if not isinstance(item, dict):
            raise ResponseFormatError("claim is not an object")
        metric_id, value, cited = (item.get(k) for k in ("metric_id", "value", "cited_event_ids"))
        if (
            (metric_id is not None and not isinstance(metric_id, str))
            or (
                value is not None
                and (isinstance(value, bool) or not isinstance(value, int | float))
            )
            or not isinstance(cited, list)
            or not all(isinstance(i, str) for i in cited)
        ):
            raise ResponseFormatError("claim has the wrong shape")
        claims.append(Claim(metric_id, value, tuple(cited)))
    return tuple(claims[:MAX_CLAIMS]), max(0, len(claims) - MAX_CLAIMS)


AGENT_INSTRUCTIONS = (
    "You are a claim-drafting agent for a football broadcast editor. The match is synthetic: "
    "teams, players and events are fictional. For the moment described, propose up to "
    f"{MAX_CLAIMS} factual claims an analyst graphic should show. Each claim is structured: "
    "a metric_id with its exact value, and/or cited_event_ids. Use the tools to find metric "
    "IDs, values and events; only what has happened when the card is published is available. "
    "Never invent IDs or values. A deterministic validator checks every claim and drops the "
    "ones it rejects. Reply only with JSON matching the schema."
)


def agent_brief(ctx: ReviewContext, feedback: Sequence[ClaimVerdict] = ()) -> str:
    card, meta = ctx.card, ctx.match.meta
    last = ctx.match.events[-1]
    lines = [
        f"Match: {meta.home.name} (home) vs {meta.away.name} (away).",
        f"Moment: {card.title} (kind {card.kind}, team {card.team}).",
        f"The card is published in minute {minute_of(card.publish_clock_s)}, right after "
        f"event {ctx.as_of_event_id}; the score then is {last.home_score}-{last.away_score}.",
        f"Evidence events: {', '.join(card.evidence_event_ids)}.",
    ]
    if feedback:
        lines.append("Your previous claims and the validator's verdicts:")
        for v in feedback:
            verdict = "verified" if v.verified else "rejected: " + "; ".join(v.reasons)
            lines.append(f"- {json.dumps(claim_record(v.claim))} -> {verdict}")
        lines.append("Check the rejected claims with the tools and return the full corrected list.")
    return "\n".join(lines)


def _call_record(response: dict, calls: list[dict]) -> dict[str, Any]:
    if not isinstance(response.get("id"), str) or not isinstance(response.get("model"), str):
        raise ResponseFormatError("response id or model missing")
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return {
        "response_id": response["id"],
        "model": response["model"],
        "status": response["status"],
        "usage": {k: usage.get(k) for k in ("input_tokens", "output_tokens")},
        "tool_calls": [{"name": c["name"], "arguments": c["arguments"][:500]} for c in calls],
    }


class FoundryAgentProposer:
    """Responses API (v1) agent loop with read-only, publication-bound tools."""

    name = FOUNDRY

    def __init__(
        self, config: AzureOpenAIConfig, transport: Transport | None = None, timeout_s=30.0
    ):
        self.config = config
        self.transport = transport
        self.timeout_s = timeout_s
        self.label = (
            f"Microsoft Foundry agent (deployment {config.deployment}, Responses API v1), run "
            "when this output was built; every claim is checked by the deterministic validator."
        )

    def propose(self, ctx: ReviewContext, feedback: Sequence[ClaimVerdict] = ()) -> Proposal:
        items: list[dict[str, Any]] = [
            {"role": "system", "content": AGENT_INSTRUCTIONS},
            {"role": "user", "content": agent_brief(ctx, feedback)},
        ]
        records: list[dict[str, Any]] = []
        tool_calls = 0
        for _ in range(MAX_MODEL_CALLS):
            body = {
                "model": self.config.deployment,
                "input": items,
                "tools": TOOL_SPECS,
                "text": {"format": CLAIMS_FORMAT},
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "store": False,  # no server-side retention; the full input is resent instead
            }
            response = post_responses(self.config, body, self.transport, self.timeout_s)
            if response.get("status") != "completed":
                raise ResponseFormatError("response status is not 'completed'")
            output = response.get("output")
            if not isinstance(output, list):
                raise ResponseFormatError("response has no output array")
            calls = [i for i in output if isinstance(i, dict) and i.get("type") == "function_call"]
            if not all(isinstance(c.get(k), str) for c in calls for k in _CALL_KEYS):
                raise ResponseFormatError("function call is missing name, call_id or arguments")
            records.append(_call_record(response, calls))
            if not calls:
                claims, dropped = parse_claims(extract_output_text(response))
                return Proposal(claims, tuple(records), dropped)
            tool_calls += len(calls)
            if tool_calls > MAX_TOOL_CALLS:
                raise AgentError("tool call limit exceeded")
            for c in calls:
                # Echo the call without its item id (nothing is stored server-side).
                items.append({"type": "function_call", **{k: c[k] for k in _CALL_KEYS}})
                result = run_tool(ctx, c["name"], c["arguments"])
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": c["call_id"],
                        "output": json.dumps(result),
                    }
                )
        raise AgentError("no final answer within the model call limit")


_CALL_KEYS = ("call_id", "name", "arguments")


class RecordedProposer:
    """Replays the claims of a recorded live Foundry run; the verdicts are recomputed."""

    name = RECORDED

    def __init__(self, trace: dict[str, Any], source: str):
        if trace.get("schema") != TRACE_SCHEMA:
            raise ValueError(f"not a {TRACE_SCHEMA} trace")
        self.match_id = trace["match_id"]
        self._attempts = {r["card_id"]: r["attempts"] for r in trace["reviews"]}
        self.label = (
            f"Recorded Microsoft Foundry agent run ({trace['recorded_at_utc']}, deployment "
            f"{trace['deployment']}), replayed from {source} — not a live call. The claims "
            "are re-checked by the validator now."
        )

    def propose(self, ctx: ReviewContext, feedback: Sequence[ClaimVerdict] = ()) -> Proposal:
        if ctx.match.meta.match_id != self.match_id:
            raise ValueError("trace was recorded for another match")
        rec = self._attempts[ctx.card.card_id][1 if feedback else 0]
        claims = tuple(
            Claim(c["metric_id"], c["value"], tuple(c["cited_event_ids"])) for c in rec["claims"]
        )
        return Proposal(claims, tuple(rec["calls"]), rec.get("dropped", 0))


# --- Review ----------------------------------------------------------------------------


@dataclass(frozen=True)
class ClaimReview:
    card_id: str
    as_of_event_id: str
    proposer: str
    proposer_label: str
    attempts: tuple[Proposal, ...]
    verdicts: tuple[ClaimVerdict, ...]  # every attempt, then injected test claims
    published: tuple[Fact, ...]  # verified final claims and corrections, deduplicated
    fallback_reason: str | None = None
    notes: tuple[str, ...] = field(default=())
    metrics: tuple[Metric, ...] = ()  # cited by verdict facts and corrections, by metric_id


def injected_score_claim(ctx: ReviewContext) -> Claim | None:
    """A deliberately wrong goal count for the scoring team (goal cards only)."""
    if ctx.card.kind != "goal":
        return None
    metric = ctx.metrics[as_of_id(f"{ctx.card.team}.goals", ctx.as_of_event_id)]
    assert isinstance(metric.value, int)
    return Claim(metric.metric_id, metric.value + 1, (ctx.as_of_event_id,))


def review_card(
    match: Match,
    card: InsightCard,
    proposer: ClaimProposer,
    inject_test_claim: bool = False,
    strict: bool = False,
) -> ClaimReview:
    """Propose, verify, retry once with the reasons, then publish only what verifies.

    Without ``strict``, a proposer failure falls back to ``OfflineProposer`` (labelled, with
    the exception type as reason); with ``strict`` it is raised, so a live recording never
    contains fallback output.
    """
    ctx = review_context(match, card)
    fallback = None
    try:
        first = proposer.propose(ctx)
    except Exception as exc:  # network, HTTP, format, limits: never leak details such as keys
        if strict:
            raise
        fallback = f"{proposer.name} unavailable ({type(exc).__name__})"
        proposer = OfflineProposer()
        first = proposer.propose(ctx)
    attempts = [first]
    final = [check_claim(ctx, c, proposer.name, 1) for c in first.claims]
    verdicts = list(final)
    notes = []
    if first.dropped:
        notes.append(f"{first.dropped} claim(s) over the limit of {MAX_CLAIMS} ignored")
    if MAX_RETRIES and not all(v.verified for v in final):
        try:
            second = proposer.propose(ctx, final)
        except Exception as exc:
            if strict:
                raise
            notes.append(f"retry unavailable ({type(exc).__name__}); rejected claims dropped")
        else:
            attempts.append(second)
            final = [check_claim(ctx, c, proposer.name, 2) for c in second.claims]
            verdicts += final
    injected = injected_score_claim(ctx) if inject_test_claim else None
    tests = [check_claim(ctx, injected, INJECTED, 0)] if injected is not None else []
    verdicts += tests
    published: dict[str, Fact] = {}
    for v in final + tests:
        fact = v.fact if v.verified else v.correction
        if fact is not None:
            published.setdefault(fact.text, fact)
    cited = {m for v in verdicts for f in (v.fact, v.correction) if f for m in f.metric_ids}
    return ClaimReview(
        card_id=card.card_id,
        as_of_event_id=ctx.as_of_event_id,
        proposer=proposer.name,
        proposer_label=proposer.label,
        attempts=tuple(attempts),
        verdicts=tuple(verdicts),
        published=tuple(published.values()),
        fallback_reason=fallback,
        notes=tuple(notes),
        metrics=tuple(ctx.metrics[m] for m in sorted(cited)),
    )


def review_cards(
    match: Match, card_set: CardSet, proposer: ClaimProposer, strict: bool = False
) -> tuple[ClaimReview, ...]:
    """Review every card; the injected test claim goes to the first goal card only."""
    first_goal = next((c.card_id for c in card_set.cards if c.kind == "goal"), None)
    return tuple(
        review_card(match, c, proposer, c.card_id == first_goal, strict) for c in card_set.cards
    )


def review_record(review: ClaimReview) -> dict[str, Any]:
    """JSON record of a review; its metrics are listed separately (the bundle's ``metrics``)."""
    record = asdict(review)
    record["metric_ids"] = [m["metric_id"] for m in record.pop("metrics")]
    return record


def build_trace(
    match: Match, reviews: Sequence[ClaimReview], config: AzureOpenAIConfig
) -> dict[str, Any]:
    """Redacted evidence of a live run: claims and call metadata, never the API key."""
    return {
        "schema": TRACE_SCHEMA,
        "recorded_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": "responses-v1",
        "endpoint_host": urlparse(config.endpoint).hostname,
        "deployment": config.deployment,
        "store": False,
        "match_id": match.meta.match_id,
        "seed": match.meta.seed,
        "reviews": [
            {
                "card_id": r.card_id,
                "attempts": [
                    {
                        "claims": [claim_record(c) for c in a.claims],
                        "calls": list(a.calls),
                        "dropped": a.dropped,
                    }
                    for a in r.attempts
                ],
                "verdicts": [
                    {"claim": claim_record(v.claim), "source": v.source, "status": v.status}
                    for v in r.verdicts
                ],
            }
            for r in reviews
        ],
        "note": (
            "Claims proposed by a live Microsoft Foundry deployment. Verdicts are recomputed "
            "deterministically on replay; claim values are model output, not evidence."
        ),
    }


def live_review(
    match: Match,
    card_set: CardSet,
    config: AzureOpenAIConfig,
    transport: Transport | None = None,
    timeout_s: float = 30.0,
) -> tuple[tuple[ClaimReview, ...], dict[str, Any]]:
    """Strict live run (no fallback) and its redacted trace, for recording evidence."""
    proposer = FoundryAgentProposer(config, transport, timeout_s)
    reviews = review_cards(match, card_set, proposer, strict=True)
    return reviews, build_trace(match, reviews, config)


def format_review(review: ClaimReview) -> str:
    lines = [f"Claim check {review.card_id} (as of {review.as_of_event_id})"]
    lines.append(f"  Proposer: {review.proposer_label}")
    if review.fallback_reason:
        lines.append(f"  Note: {review.fallback_reason}")
    for a, attempt in enumerate(review.attempts, 1):
        for call in attempt.calls:
            tools = ", ".join(t["name"] for t in call["tool_calls"]) or "final answer"
            lines.append(
                f"  attempt {a}: response {call['response_id']} ({call['model']}): {tools}"
            )
    for v in review.verdicts:
        where = "injected test claim" if v.source == INJECTED else f"attempt {v.attempt}"
        mark = "✓" if v.verified else "✗"
        lines.append(f"  {mark} [{where}] {json.dumps(claim_record(v.claim))}")
        lines += [f"      reason: {r}" for r in v.reasons]
        if v.correction:
            lines.append(f"      published instead: {v.correction.text}")
    lines += [f"  Note: {n}" for n in review.notes]
    return "\n".join(lines)

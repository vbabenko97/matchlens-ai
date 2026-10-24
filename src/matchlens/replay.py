"""Replay bundle for the browser UI: everything precomputed and verified in Python.

The browser only displays this JSON; it never decides what is verified.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

from .agent import (
    FOUNDRY,
    INJECTED,
    RECORDED,
    ClaimProposer,
    ClaimReview,
    OfflineProposer,
    review_cards,
    review_record,
)
from .cards import build_cards
from .moments import detect_goals
from .narrative import ANALYST, MODES, NarrativeProvider, OfflineTemplateProvider
from .overlays import build_overlay_document
from .schema import AWAY, HOME, Match, minute_of
from .story import full_time_story

BUNDLE_SCHEMA = "matchlens.replay/v1"
DEFAULT_DEMO_SEED = 1
LEAD_IN_S = 15.0  # match seconds of build-up shown before a highlighted move starts


STAT_NAMES = ("goals", "shots", "xg_heuristic")


def stat_steps(match: Match) -> list[dict[str, Any]]:
    """Running goals, shots and xG heuristic per team, one step per shot, in match order.

    Step ``i`` holds the totals up to and including the shot with ``seq``; the panel shows
    the last step at or before the current event, so it changes on the same event as the
    scoreboard. Uses the same selections as ``matchlens.metrics``.
    """
    totals = {f"{side}.{name}": 0.0 for side in (HOME, AWAY) for name in STAT_NAMES}
    steps: list[dict[str, Any]] = []
    for e in match.events:
        if e.type != "shot" or e.team is None:
            continue
        totals[f"{e.team}.goals"] += 1.0 if e.outcome == "goal" else 0.0
        totals[f"{e.team}.shots"] += 1.0
        totals[f"{e.team}.xg_heuristic"] += e.xg_heuristic or 0.0
        steps.append({"seq": e.seq, **{k: round(v, 3) for k, v in totals.items()}})
    return steps


def highlights(match: Match, analyst_reviews: Sequence[ClaimReview]) -> dict[str, Any]:
    """Replay shortcuts for the demo: where the match turned, and a card whose claims show
    the validator at work.

    These are chapter marks chosen with hindsight (from the full-time story) to save a
    viewer from watching 90 minutes; they never decide what a card says or when it appears.
    """
    out: dict[str, Any] = {}
    end = match.events[-1] if match.events else None
    if end is not None and end.type == "period_end" and end.period == 2:
        by_id = {e.event_id: e for e in match.events}
        lead_in = {g.anchor_event_id: g.start_clock_s for g in detect_goals(match)}
        story = {ln.kind: ln for ln in full_time_story(match, end.event_id)}
        goals = [e for e in match.events if e.type == "shot" and e.outcome == "goal"]
        if "ft_comeback" in story:
            line = story["ft_comeback"]
            run = [by_id[i] for i in line.event_ids if by_id[i].team == line.team]
            label = (
                f"Watch the comeback ({minute_of(run[0].clock_s)}'–{minute_of(run[-1].clock_s)}')"
            )
        elif "winner" in story:
            run = [by_id[i] for i in story["winner"].event_ids if by_id[i].type == "shot"][-1:]
            label = f"Watch the winning goal ({minute_of(run[0].clock_s)}')"
        else:
            run = goals[-1:]
            label = f"Watch the last goal ({minute_of(run[0].clock_s)}')" if run else ""
        if run:
            start = max(0.0, lead_in[run[0].event_id] - LEAD_IN_S)
            out["turning_point"] = {"start_s": start, "label": label}
    checked = [r for r in analyst_reviews if r.proposer in (FOUNDRY, RECORDED)]
    fixed = next(
        (r for r in checked if len(r.attempts) > 1 and any(not v.verified for v in r.verdicts)),
        None,
    )
    injected = next(
        (r for r in analyst_reviews if any(v.source == INJECTED for v in r.verdicts)), None
    )
    review = fixed or injected
    if review is not None:
        card = next(e for e in match.events if e.event_id == review.as_of_event_id)
        out["ai_check"] = {
            "card_id": review.card_id,
            "publish_s": card.clock_s,
            "label": f"See the AI get fact-checked ({minute_of(card.clock_s)}')",
            "self_correction": review is fixed,
        }
    return out


def build_bundle(
    match: Match,
    provider: NarrativeProvider | None = None,
    limit: int | None = None,
    proposer: ClaimProposer | None = None,
) -> dict:
    """``proposer`` drafts the claims checked for each analyst card (default: offline)."""
    provider = provider or OfflineTemplateProvider()
    card_sets = [build_cards(match, mode, provider, limit) for mode in MODES]
    metrics = {m.metric_id: m for cs in card_sets for m in cs.metrics}
    analyst = next(cs for cs in card_sets if cs.mode == ANALYST)
    reviews = review_cards(match, analyst, proposer or OfflineProposer())
    metrics.update((m.metric_id, m) for r in reviews for m in r.metrics)
    meta = match.meta
    return {
        "schema": BUNDLE_SCHEMA,
        "meta": asdict(meta),
        "final_score": list(match.final_score),
        "duration_s": 2 * meta.period_length_s,
        "provider": provider.name,
        "events": [{**e.to_record(), "minute": minute_of(e.clock_s)} for e in match.events],
        "cards": {cs.mode: [asdict(c) for c in cs.cards] for cs in card_sets},
        "suppressed": {cs.mode: [list(s) for s in cs.suppressed] for cs in card_sets},
        "metrics": {m: metrics[m].to_record() for m in sorted(metrics)},
        "reviews": {r.card_id: review_record(r) for r in reviews},
        "stat_steps": stat_steps(match),
        "overlays": build_overlay_document(match, card_sets),
        "highlights": highlights(match, reviews),
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

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
from .generator import generate_match
from .moments import detect_goals
from .narrative import ANALYST, CASUAL, MODES, NarrativeProvider, OfflineTemplateProvider
from .overlays import build_overlay_document
from .passing import passing_record
from .personalize import Personalizer, personalize_cards, personalized_record
from .players import player_records
from .recap import build_recap
from .schema import AWAY, HOME, Match, minute_of
from .story import full_time_story
from .validate import assert_valid

BUNDLE_SCHEMA = "matchlens.replay/v1"
DEFAULT_DEMO_SEED = 1
# More matches offered in the public demo's match picker: untuned seeds from the checked range
# 100-129, picked for different stories (comeback, high-scoring draw, rout, a 1-0 with little
# to say). They use the offline proposer; only the demo seed replays the recorded Foundry run.
DEMO_EXTRA_SEEDS = (121, 106, 103, 123, 124)
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
        minute = minute_of(card.clock_s)
        by_ai = review.proposer in (FOUNDRY, RECORDED)
        out["ai_check"] = {
            "card_id": review.card_id,
            "publish_s": card.clock_s,
            # Without an AI run, only the deliberately wrong test claim is there to reject.
            "label": f"See the AI get fact-checked ({minute}')"
            if by_ai
            else f"See the validator reject a wrong claim ({minute}')",
            "self_correction": review is fixed,
        }
    return out


def build_bundle(
    match: Match,
    provider: NarrativeProvider | None = None,
    limit: int | None = None,
    proposer: ClaimProposer | None = None,
    personalizer: Personalizer | None = None,
) -> dict:
    """``proposer`` drafts the claims checked for each analyst card (default: offline).

    ``personalizer`` (a recorded run in practice) adds the checked language and club variants
    of the casual cards as ``personalized``; without it the bundle has none.
    """
    provider = provider or OfflineTemplateProvider()
    card_sets = [build_cards(match, mode, provider, limit) for mode in MODES]
    metrics = {m.metric_id: m for cs in card_sets for m in cs.metrics}
    analyst = next(cs for cs in card_sets if cs.mode == ANALYST)
    casual = next(cs for cs in card_sets if cs.mode == CASUAL)
    reviews = review_cards(match, analyst, proposer or OfflineProposer())
    metrics.update((m.metric_id, m) for r in reviews for m in r.metrics)
    meta = match.meta
    personalized = None
    if personalizer is not None:
        results = personalize_cards(match, casual, personalizer)
        personalized = personalized_record(results, personalizer.label)
    doc = {
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
        "players": player_records(match, {cs.mode: cs.cards for cs in card_sets}),
        "passing": passing_record(match),
    }
    if personalized is not None:
        doc["personalized"] = personalized
    recap = build_recap(match, casual.cards)
    if recap is not None:
        doc["recap"] = recap
    return doc


def demo_extra_bundles() -> list[dict]:
    """Bundles for ``DEMO_EXTRA_SEEDS``, fully offline: no Azure call at build time, and never
    the recorded run (it belongs to the demo seed)."""
    return [
        build_bundle(assert_valid(generate_match(seed)), proposer=OfflineProposer())
        for seed in DEMO_EXTRA_SEEDS
    ]


def match_entry(bundle: dict[str, Any], file: str, overlays: str) -> dict[str, Any]:
    """One row of ``data/matches.json``: what the match picker shows and where to load it."""
    meta = bundle["meta"]
    full_time = [c for c in bundle["cards"]["casual"] if c["kind"] == "full_time"]
    return {
        "seed": meta["seed"],
        "match_id": meta["match_id"],
        "home": meta["home"]["name"],
        "away": meta["away"]["name"],
        "final_score": bundle["final_score"],
        "headline": full_time[0]["title"] if full_time else "",
        "cards": len(bundle["cards"]["casual"]),
        "recorded_foundry_run": any(
            r["proposer"] == RECORDED for r in bundle.get("reviews", {}).values()
        ),
        "file": file,
        "overlays": overlays,
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

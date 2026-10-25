"""Match recap: the full-time story and the goals as one text an editor can copy.

The recap adds no wording of its own. It points at lines already on the casual cards: the
full-time card's title (headline) and its "why" lines (summary), and the scorer line of each
goal card. So every line is a verified fact that keeps its event and metric IDs, and the
recorded language and club variants of those cards apply to it unchanged (each line names
its card and line index, as ``matchlens.personalize`` does).

Causality: the recap exists only once the full-time card does and is published with it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .cards import WHAT, WHY, InsightCard
from .metrics import FACT
from .narrative import CASUAL
from .schema import Match

RECAP_SCHEMA = "matchlens.recap/v1"
TITLE_INDEX = -1  # a card's title, as in matchlens.personalize


def _line(card: InsightCard, index: int) -> dict[str, Any]:
    if index == TITLE_INDEX:
        text, kind, events, metrics = card.title, FACT, card.evidence_event_ids, ()
    else:
        f = card.facts[index]
        text, kind, events, metrics = f.text, f.kind, f.event_ids, f.metric_ids
    return {
        "card_id": card.card_id,
        "index": index,
        "text": text,
        "kind": kind,
        "event_ids": list(events),
        "metric_ids": list(metrics),
    }


def build_recap(match: Match, cards: Sequence[InsightCard]) -> dict[str, Any] | None:
    """The recap of a match from its casual cards, or None without a full-time card.

    The goal list is left empty unless every goal of the match has its scorer line (a
    suppressed goal card would otherwise drop a goal silently).
    """
    if any(c.mode != CASUAL for c in cards):
        raise ValueError("the recap is built from casual cards only")
    end = next((c for c in cards if c.kind == "full_time"), None)
    if end is None:
        return None
    goals = {e.event_id for e in match.events if e.type == "shot" and e.outcome == "goal"}
    scorers = [
        _line(c, i)
        for c in cards
        if c.kind == "goal"
        for i, f in enumerate(c.facts)
        if f.role == WHAT and len(f.event_ids) == 1 and f.event_ids[0] in goals
    ]
    complete = sorted(s["event_ids"][0] for s in scorers) == sorted(goals)
    return {
        "schema": RECAP_SCHEMA,
        "match_id": match.meta.match_id,
        "seed": match.meta.seed,
        "publish_clock_s": end.publish_clock_s,
        "headline": _line(end, TITLE_INDEX),
        "summary": [_line(end, i) for i, f in enumerate(end.facts) if f.role == WHY],
        "goals": scorers if complete else [],
    }


def synthetic_note(recap: dict[str, Any]) -> str:
    return (
        f"Synthetic match {recap['match_id']} (seed {recap['seed']}) between fictional teams. "
        "Every line is checked against the match events; synthetic xG is a heuristic, "
        "not calibrated."
    )


def format_recap(recap: dict[str, Any]) -> str:
    """Plain text for copying: headline, summary paragraph, goals, provenance.

    The replay UI's "Copy recap" produces the same text for the English source.
    """
    parts = [recap["headline"]["text"], " ".join(ln["text"] for ln in recap["summary"])]
    if recap["goals"]:
        parts.append("\n".join(["Goals", *(f"- {ln['text']}" for ln in recap["goals"])]))
    parts.append(synthetic_note(recap))
    return "\n\n".join(parts) + "\n"

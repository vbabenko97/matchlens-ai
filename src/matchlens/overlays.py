"""Timed, machine-readable overlay cues built from verified insight cards.

Schema ``matchlens.overlays/v1``: one cue per card and audience mode, timed in match
seconds. A cue starts when its card is published (the moment's last evidence event), never
before the events it cites; ``anchor_s`` is the moment's anchor event. ``facts`` are the
verified, template-rendered statements with their evidence; ``narrative`` is wording only
and keeps its status/label (offline template or unverified AI wording) so downstream
graphics never present it as a verified fact.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .cards import CardSet
from .schema import Match

OVERLAY_SCHEMA = "matchlens.overlays/v1"


def build_overlay_document(match: Match, card_sets: list[CardSet]) -> dict[str, Any]:
    meta = match.meta
    cues = []
    for card_set in card_sets:
        for card in card_set.cards:
            cues.append(
                {
                    "cue_id": card.card_id,
                    "mode": card.mode,
                    "kind": card.kind,
                    "team": card.team,
                    "start_s": card.publish_clock_s,
                    "end_s": card.end_clock_s,
                    "anchor_s": card.anchor_clock_s,
                    "lead_in_s": card.start_clock_s,
                    "title": card.title,
                    "facts": [asdict(f) for f in card.facts],
                    "evidence_event_ids": list(card.evidence_event_ids),
                    "narrative": asdict(card.narrative),
                }
            )
    cues.sort(key=lambda c: (c["start_s"], c["mode"], c["cue_id"]))
    return {
        "schema": OVERLAY_SCHEMA,
        "match_id": meta.match_id,
        "seed": meta.seed,
        "synthetic": True,
        "time_unit": "match_seconds",
        "teams": {meta.home.side: meta.home.name, meta.away.side: meta.away.name},
        "final_score": list(match.final_score),
        "notes": [
            "All teams, players and events are synthetic.",
            "facts are verified and rendered from structured data; narrative is wording only.",
            "xG values are a synthetic heuristic, not empirically calibrated.",
        ],
        "cues": cues,
    }

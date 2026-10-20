"""Replay bundle for the browser UI: everything precomputed and verified in Python.

The browser only displays this JSON; it never decides what is verified.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from .cards import build_cards
from .metrics import compute_metrics
from .narrative import MODES, NarrativeProvider, OfflineTemplateProvider
from .overlays import build_overlay_document
from .schema import AWAY, HOME, Match, minute_of

BUNDLE_SCHEMA = "matchlens.replay/v1"
DEFAULT_DEMO_SEED = 1


def minute_series(match: Match) -> dict[str, list[float]]:
    """Cumulative goals, shots and xG heuristic per team at the end of each minute.

    Index ``m`` covers events with ``clock_s < 60 * m`` (index 0 is kick-off); the last
    entry covers the whole match. Uses the same selections as ``matchlens.metrics``.
    """
    total_min = (2 * match.meta.period_length_s + 59) // 60
    series: dict[str, list[float]] = {
        f"{side}.{name}": [0.0] * (total_min + 1)
        for side in (HOME, AWAY)
        for name in ("goals", "shots", "xg_heuristic")
    }
    for e in match.events:
        if e.type != "shot" or e.team is None:
            continue
        first = min(int(e.clock_s // 60) + 1, total_min)
        increments = {
            "goals": 1.0 if e.outcome == "goal" else 0.0,
            "shots": 1.0,
            "xg_heuristic": e.xg_heuristic or 0.0,
        }
        for name, inc in increments.items():
            values = series[f"{e.team}.{name}"]
            for m in range(first, total_min + 1):
                values[m] += inc
    return {k: [round(v, 3) for v in vals] for k, vals in series.items()}


def build_bundle(match: Match, provider: NarrativeProvider | None = None, limit: int = 4) -> dict:
    provider = provider or OfflineTemplateProvider()
    card_sets = [build_cards(match, mode, provider, limit) for mode in MODES]
    metrics = compute_metrics(match)
    cited = sorted({m for cs in card_sets for c in cs.cards for f in c.facts for m in f.metric_ids})
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
        "metrics": {m: metrics[m].to_record() for m in cited},
        "series": minute_series(match),
        "overlays": build_overlay_document(match, card_sets),
    }


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

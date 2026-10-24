"""Player focus: per-player statistics, each one traceable to the events it counts.

Every player stat is a running value with one step per contributing event, so the replay can
show it as it stood at any moment without counting later events: step ``[seq, value, ids]``
holds the value after the event with ``seq`` and the event IDs that step adds as evidence.
``kind`` separates facts counted from the simulated events from the synthetic xG heuristic,
exactly as in ``matchlens.metrics``; the selections match the team metrics, so a team's
players add up to the team total. No AI is involved.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .metrics import FACT, HEURISTIC
from .moments import detect_goals
from .schema import BALL_EVENTS, SIDES, Event, Match

PLAYERS_SCHEMA = "matchlens.players/v1"
BALL_WINS = frozenset({"tackle", "interception", "recovery"})


@dataclass(frozen=True, slots=True)
class StatDef:
    name: str
    label: str
    kind: str
    unit: str  # "count", "ratio" or "xg"
    definition: str
    select: Callable[[Event], bool]


_COUNTS = (
    StatDef(
        "goals",
        "Goals",
        FACT,
        "count",
        "The player's shots with outcome 'goal'.",
        lambda e: e.type == "shot" and e.outcome == "goal",
    ),
    StatDef(
        "shots", "Shots", FACT, "count", "All of the player's shots.", lambda e: e.type == "shot"
    ),
    StatDef(
        "shots_on_target",
        "Shots on target",
        FACT,
        "count",
        "The player's shots with outcome 'goal' or 'saved'.",
        lambda e: e.type == "shot" and e.outcome in ("goal", "saved"),
    ),
    StatDef(
        "passes",
        "Passes attempted",
        FACT,
        "count",
        "All of the player's passes, including restarts.",
        lambda e: e.type == "pass",
    ),
    StatDef(
        "passes_completed",
        "Passes completed",
        FACT,
        "count",
        "The player's passes with outcome 'complete'.",
        lambda e: e.type == "pass" and e.outcome == "complete",
    ),
    StatDef(
        "ball_wins",
        "Ball wins",
        FACT,
        "count",
        "The player's tackles, interceptions and recoveries.",
        lambda e: e.type in BALL_WINS,
    ),
    StatDef(
        "pressures",
        "Pressures applied",
        FACT,
        "count",
        "The player's pressure events on the opponent in possession.",
        lambda e: e.type == "pressure",
    ),
    StatDef(
        "passes_lost",
        "Passes lost",
        FACT,
        "count",
        "The player's passes intercepted or played out.",
        lambda e: e.type == "pass" and e.outcome in ("intercepted", "out"),
    ),
)
_COMPLETION = StatDef(
    "pass_completion",
    "Pass completion rate",
    FACT,
    "ratio",
    "Completed passes / attempted passes so far; undefined before the first pass.",
    lambda e: e.type == "pass",
)
_XG = StatDef(
    "xg_heuristic",
    "Synthetic xG (heuristic)",
    HEURISTIC,
    "xg",
    "Sum of the uncalibrated distance/angle shot heuristic over the player's shots.",
    lambda e: e.type == "shot",
)
_INVOLVED = StatDef(
    "goal_involvements",
    "Goal moves involved in",
    FACT,
    "count",
    "Goals by the player's team whose scoring possession, up to the shot, includes a ball "
    "event by the player. Evidence: the player's events in the move and the goal.",
    lambda e: False,  # computed from the goal moments, not per event
)
STATS = (*_COUNTS, _COMPLETION, _XG, _INVOLVED)


def stat_id(player_id: str, name: str) -> str:
    return f"player.{player_id}.{name}"


def _stat(player_id: str, d: StatDef, steps: list[list[Any]]) -> dict[str, Any]:
    return {
        "stat_id": stat_id(player_id, d.name),
        "name": d.name,
        "label": d.label,
        "kind": d.kind,
        "unit": d.unit,
        "definition": d.definition,
        "steps": steps,
    }


def _player_stats(match: Match, player_id: str, goal_moves: Sequence[tuple[Event, list[str]]]):
    own = [e for e in match.events if e.player_id == player_id]
    out = []
    for d in _COUNTS:
        chosen = [e for e in own if d.select(e)]
        out.append(
            _stat(player_id, d, [[e.seq, i + 1, [e.event_id]] for i, e in enumerate(chosen)])
        )
    steps, done = [], 0
    passes = [e for e in own if _COMPLETION.select(e)]
    for i, e in enumerate(passes):
        done += e.outcome == "complete"
        steps.append([e.seq, round(done / (i + 1), 4), [e.event_id]])
    out.append(_stat(player_id, _COMPLETION, steps))
    steps, total = [], 0.0
    for e in own:
        if _XG.select(e):
            total += e.xg_heuristic or 0.0
            steps.append([e.seq, round(total, 3), [e.event_id]])
    out.append(_stat(player_id, _XG, steps))
    steps = []
    for goal, ids in goal_moves:
        steps.append([goal.seq, len(steps) + 1, [*ids, goal.event_id]])
    out.append(_stat(player_id, _INVOLVED, steps))
    return out, own


def _goal_moves(match: Match) -> list[tuple[Event, dict[str, list[str]]]]:
    """Each goal with, per player of the scoring team, their ball events in the move."""
    by_id = {e.event_id: e for e in match.events}
    out = []
    for moment in detect_goals(match):
        goal = by_id[moment.anchor_event_id]
        per: dict[str, list[str]] = {}
        for eid in moment.event_ids:
            e = by_id[eid]
            if (
                e.team == goal.team
                and e.type in BALL_EVENTS
                and e.player_id
                and eid != goal.event_id
            ):
                per.setdefault(e.player_id, []).append(eid)
        if goal.player_id:
            per.setdefault(goal.player_id, [])  # the scorer is involved, the goal is the evidence
        out.append((goal, per))
    return out


def player_records(match: Match, cards_by_mode: dict[str, Sequence[Any]]) -> dict[str, Any]:
    """Every player of both teams with running stats and the cards that cite their events.

    ``cards_by_mode`` maps a mode to its ``InsightCard`` list; a card features a player when
    its evidence includes one of the player's events.
    """
    moves = _goal_moves(match)
    players = []
    for side in SIDES:
        team = match.meta.team(side)
        for p in team.players:
            own_moves = [
                (goal, per[p.player_id])
                for goal, per in moves
                if goal.team == side and p.player_id in per
            ]
            stats, own = _player_stats(match, p.player_id, own_moves)
            own_ids = {e.event_id for e in own}
            featured = {
                mode: [c.card_id for c in cards if own_ids & set(c.evidence_event_ids)]
                for mode, cards in cards_by_mode.items()
            }
            players.append(
                {
                    "player_id": p.player_id,
                    "name": p.name,
                    "number": p.number,
                    "position": p.position,
                    "team": side,
                    "events": len(own),
                    "stats": stats,
                    "featured_cards": featured,
                }
            )
    return {"schema": PLAYERS_SCHEMA, "players": players}


def value_at(stat: dict[str, Any], seq: int) -> Any:
    """The stat as it stood after the event with ``seq`` (0 or None before its first step)."""
    value: Any = None if stat["unit"] == "ratio" else 0
    for s, v, _ in stat["steps"]:
        if s > seq:
            break
        value = v
    return value

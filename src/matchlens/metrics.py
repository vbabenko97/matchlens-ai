"""Deterministic, event-grounded match metrics.

Every ``Metric`` records the exact events it was computed from (``source_event_ids``) and,
for ratios, the subset that forms the numerator. ``kind`` separates facts counted from the
simulated events from synthetic heuristics (the xG heuristic), which are never calibrated.
``compute_metrics(..., as_of_event_id=...)`` gives the metrics as they stood when that event
happened, so a card shown at that time never counts events that have not happened yet.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from typing import Any

from .schema import AWAY, BALL_EVENTS, HOME, SIDES, Event, Match

FACT = "fact"
HEURISTIC = "heuristic"
DEFAULT_WINDOW_S = 15 * 60


@dataclass(frozen=True, slots=True)
class Metric:
    metric_id: str
    label: str
    team: str
    value: float | int | None  # None: undefined (e.g. a ratio with zero attempts)
    unit: str  # "count", "ratio" or "xg"
    kind: str  # FACT or HEURISTIC
    definition: str
    source_event_ids: tuple[str, ...]
    numerator_event_ids: tuple[str, ...] = ()
    window: tuple[float, float] | None = None  # [start, end) in match seconds
    as_of_event_id: str | None = None  # set: only events up to and including this one count

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _other(side: str) -> str:
    return AWAY if side == HOME else HOME


Selector = Callable[[Event, str], bool]

# name -> (label, definition, selector(event, team))
_COUNTS: dict[str, tuple[str, str, Selector]] = {
    "passes": (
        "Passes attempted",
        "All pass events by the team, including restarts.",
        lambda e, t: e.type == "pass" and e.team == t,
    ),
    "passes_completed": (
        "Passes completed",
        "Pass events by the team with outcome 'complete'.",
        lambda e, t: e.type == "pass" and e.team == t and e.outcome == "complete",
    ),
    "shots": (
        "Shots",
        "All shot events by the team.",
        lambda e, t: e.type == "shot" and e.team == t,
    ),
    "shots_on_target": (
        "Shots on target",
        "Shot events by the team with outcome 'goal' or 'saved'.",
        lambda e, t: e.type == "shot" and e.team == t and e.outcome in ("goal", "saved"),
    ),
    "goals": (
        "Goals",
        "Shot events by the team with outcome 'goal'.",
        lambda e, t: e.type == "shot" and e.team == t and e.outcome == "goal",
    ),
    "pressures": (
        "Pressures applied",
        "Pressure events by the team on the opponent in possession.",
        lambda e, t: e.type == "pressure" and e.team == t,
    ),
    "turnovers": (
        "Turnovers",
        "Team passes intercepted or played out, plus opponent tackles winning the ball.",
        lambda e, t: (
            (e.type == "pass" and e.team == t and e.outcome in ("intercepted", "out"))
            or (e.type == "tackle" and e.team == _other(t))
        ),
    ),
}
_WINDOW_COUNTS = ("passes", "shots", "pressures", "turnovers")


def _ids(events: Iterable[Event]) -> tuple[str, ...]:
    return tuple(e.event_id for e in events)


def _count(name: str, team: str, events: tuple[Event, ...], window=None, suffix="") -> Metric:
    label, definition, select = _COUNTS[name]
    chosen = [e for e in events if select(e, team)]
    return Metric(
        metric_id=f"{team}.{name}{suffix}",
        label=label,
        team=team,
        value=len(chosen),
        unit="count",
        kind=FACT,
        definition=definition,
        source_event_ids=_ids(chosen),
        window=window,
    )


def _xg(team: str, events: tuple[Event, ...], window=None, suffix="") -> Metric:
    shots = [e for e in events if e.type == "shot" and e.team == team]
    total = round(sum(e.xg_heuristic or 0.0 for e in shots), 3)
    return Metric(
        metric_id=f"{team}.xg_heuristic{suffix}",
        label="Synthetic xG (heuristic)",
        team=team,
        value=total,
        unit="xg",
        kind=HEURISTIC,
        definition="Sum of the uncalibrated distance/angle shot heuristic over the team's shots.",
        source_event_ids=_ids(shots),
        window=window,
    )


def _completion(team: str, events: tuple[Event, ...]) -> Metric:
    attempted = [e for e in events if _COUNTS["passes"][2](e, team)]
    completed = [e for e in attempted if e.outcome == "complete"]
    return Metric(
        metric_id=f"{team}.pass_completion",
        label="Pass completion rate",
        team=team,
        value=round(len(completed) / len(attempted), 4) if attempted else None,
        unit="ratio",
        kind=FACT,
        definition="Completed passes / attempted passes; undefined with no attempts.",
        source_event_ids=_ids(attempted),
        numerator_event_ids=_ids(completed),
    )


def _possessions(team: str, events: tuple[Event, ...]) -> Metric:
    firsts: dict[int, Event] = {}
    for e in events:
        if e.type in BALL_EVENTS and e.possession_id is not None:
            firsts.setdefault(e.possession_id, e)
    chosen = [e for e in firsts.values() if e.team == team]
    return Metric(
        metric_id=f"{team}.possessions",
        label="Possessions",
        team=team,
        value=len(chosen),
        unit="count",
        kind=FACT,
        definition="Possession chains owned by the team; sources are each chain's first event.",
        source_event_ids=_ids(chosen),
    )


def window_bounds(match: Match, window_s: int) -> list[tuple[float, float]]:
    if window_s <= 0:
        raise ValueError("window_s must be positive")
    total = 2 * match.meta.period_length_s
    starts = range(0, total, window_s)
    return [(float(s), float(min(s + window_s, total))) for s in starts]


def _in_window(e: Event, lo: float, hi: float, last: bool) -> bool:
    return lo <= e.clock_s < hi or (last and e.clock_s == hi)


def as_of_id(metric_id: str, event_id: str) -> str:
    """ID of ``metric_id`` counted only up to and including ``event_id``."""
    return f"{metric_id}:asof:{event_id}"


def compute_metrics(
    match: Match, window_s: int = DEFAULT_WINDOW_S, as_of_event_id: str | None = None
) -> dict[str, Metric]:
    """All match metrics keyed by ``metric_id`` in a stable order.

    With ``as_of_event_id``, only the events up to and including that event in match order
    are counted: the metrics known when it happened. Windows that start after it are left
    out, and every ID gets the suffix ``:asof:<event_id>`` so it never collides with the
    full-match metric of the same name.
    """
    events = match.events
    cutoff: Event | None = None
    if as_of_event_id is not None:
        idx = next((i for i, e in enumerate(events) if e.event_id == as_of_event_id), None)
        if idx is None:
            raise ValueError(f"unknown event {as_of_event_id!r}")
        cutoff, events = events[idx], events[: idx + 1]
    out: list[Metric] = []
    for team in SIDES:
        out.extend(_count(name, team, events) for name in _COUNTS)
        out.append(_completion(team, events))
        out.append(_xg(team, events))
        out.append(_possessions(team, events))
    bounds = window_bounds(match, window_s)
    for i, (lo, hi) in enumerate(bounds):
        if cutoff is not None and lo > cutoff.clock_s:
            break  # this window has not started yet
        in_win = tuple(e for e in events if _in_window(e, lo, hi, i == len(bounds) - 1))
        suffix = f"@{int(lo)}-{int(hi)}"
        for team in SIDES:
            out.extend(
                _count(name, team, in_win, window=(lo, hi), suffix=suffix)
                for name in _WINDOW_COUNTS
            )
            out.append(_xg(team, in_win, window=(lo, hi), suffix=suffix))
    if as_of_event_id is not None:
        out = [
            replace(
                m, metric_id=as_of_id(m.metric_id, as_of_event_id), as_of_event_id=as_of_event_id
            )
            for m in out
        ]
    return {m.metric_id: m for m in out}

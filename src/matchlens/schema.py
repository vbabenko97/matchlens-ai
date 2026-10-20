"""Match event schema.

Geometry: absolute pitch frame in metres, origin at one corner flag.
x in [0, PITCH_LENGTH] runs goal-to-goal, y in [0, PITCH_WIDTH] touchline-to-touchline.
The home team always attacks toward x = PITCH_LENGTH and the away team toward x = 0
(the half-time side switch is deliberately not modelled). Coordinates are rounded to 0.1 m.

Clock: ``clock_s`` is elapsed match seconds. Period 1 covers [0, period_length_s],
period 2 covers [period_length_s, 2 * period_length_s]. Stoppage time is not modelled,
so the clock is globally nondecreasing.

Possession: a possession is a maximal run of events in which one team controls the ball.
A new possession starts when the controlling team changes or at a restart (kick-off,
goal kick). Off-ball events (pressure) carry the current possession ID but the
pressing team's ``team``.

Scores: ``home_score`` / ``away_score`` are the score *after* the event.
``xg_heuristic`` is a synthetic, uncalibrated heuristic, not an empirical xG model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

SCHEMA_VERSION = "1"
PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
BALL_CONTINUITY_TOLERANCE_M = 1.0

HOME = "home"
AWAY = "away"
SIDES = (HOME, AWAY)

# Event types and their allowed outcomes.
EVENT_OUTCOMES: dict[str, frozenset[str | None]] = {
    "period_start": frozenset({None}),
    "period_end": frozenset({None}),
    "pass": frozenset({"complete", "intercepted", "out"}),
    "shot": frozenset({"goal", "saved", "off_target", "blocked"}),
    "pressure": frozenset({None}),
    "tackle": frozenset({"won"}),
    "interception": frozenset({"won"}),
    "recovery": frozenset({"won"}),
    "clearance": frozenset({None}),
}
# Events that touch the ball and therefore have start/end locations and a player.
BALL_EVENTS = frozenset({"pass", "shot", "tackle", "interception", "recovery", "clearance"})
# Off-ball events by the team *not* in possession.
OFF_BALL_EVENTS = frozenset({"pressure"})
PERIOD_EVENTS = frozenset({"period_start", "period_end"})
PASS_SUBTYPES = frozenset({"open_play", "kick_off", "goal_kick", "throw_in"})
RESTART_SUBTYPES = frozenset({"kick_off", "goal_kick", "throw_in"})


@dataclass(frozen=True, slots=True)
class Player:
    player_id: str
    name: str
    number: int
    position: str  # GK, DF, MF, FW


@dataclass(frozen=True, slots=True)
class Team:
    side: str
    team_id: str
    name: str
    players: tuple[Player, ...]


@dataclass(frozen=True, slots=True)
class MatchMeta:
    match_id: str
    seed: int
    period_length_s: int
    home: Team
    away: Team
    schema_version: str = SCHEMA_VERSION
    generator: str = "matchlens.generator"

    def team(self, side: str) -> Team:
        return self.home if side == HOME else self.away

    def player_ids(self, side: str) -> set[str]:
        return {p.player_id for p in self.team(side).players}

    def to_record(self) -> dict[str, Any]:
        return {"record": "match", **asdict(self)}


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    seq: int
    period: int
    clock_s: float
    type: str
    team: str | None
    player_id: str | None
    possession_id: int | None
    outcome: str | None
    home_score: int
    away_score: int
    start: tuple[float, float] | None = None
    end: tuple[float, float] | None = None
    subtype: str | None = None
    xg_heuristic: float | None = None
    related_event_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        rec = asdict(self)
        rec["start"] = list(self.start) if self.start is not None else None
        rec["end"] = list(self.end) if self.end is not None else None
        return {"record": "event", **rec}


@dataclass(frozen=True, slots=True)
class Match:
    meta: MatchMeta
    events: tuple[Event, ...]

    @property
    def final_score(self) -> tuple[int, int]:
        if not self.events:
            return (0, 0)
        last = self.events[-1]
        return (last.home_score, last.away_score)


def minute_of(clock_s: float) -> int:
    """Broadcast-style match minute: 0-59 s is the 1st minute."""
    return int(clock_s // 60) + 1

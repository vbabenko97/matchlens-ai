"""Semantic invariant checks for a parsed ``Match``.

``validate_match`` returns a list of human-readable issues (empty means valid).
The rules implement the conventions documented in ``matchlens.schema``.
"""

from __future__ import annotations

import math

from .schema import (
    AWAY,
    BALL_CONTINUITY_TOLERANCE_M,
    BALL_EVENTS,
    EVENT_OUTCOMES,
    HOME,
    OFF_BALL_EVENTS,
    PASS_SUBTYPES,
    PERIOD_EVENTS,
    PITCH_LENGTH,
    PITCH_WIDTH,
    RESTART_SUBTYPES,
    SIDES,
    Event,
    Match,
    MatchMeta,
)

# Ball out of play -> restart the opponent must take next.
_RESTART_AFTER = {"out": "throw_in", "off_target": "goal_kick"}


class MatchValidationError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        shown = "; ".join(issues[:5])
        more = f" (+{len(issues) - 5} more)" if len(issues) > 5 else ""
        super().__init__(f"{len(issues)} invariant violation(s): {shown}{more}")


def _other(side: str) -> str:
    return AWAY if side == HOME else HOME


def _on_pitch(p: tuple[float, float]) -> bool:
    return 0.0 <= p[0] <= PITCH_LENGTH and 0.0 <= p[1] <= PITCH_WIDTH


def _is_restart(e: Event) -> bool:
    return e.type == "pass" and e.subtype in RESTART_SUBTYPES


def _check_meta(match: Match, issues: list[str]) -> None:
    meta = match.meta
    if meta.home.side != HOME or meta.away.side != AWAY:
        issues.append("meta: team sides must be 'home' and 'away'")
    home_ids, away_ids = meta.player_ids(HOME), meta.player_ids(AWAY)
    if len(home_ids | away_ids) != len(meta.home.players) + len(meta.away.players):
        issues.append("meta: player IDs must be unique across both squads")
    if meta.period_length_s <= 0:
        issues.append("meta: period_length_s must be positive")


def _check_event_shape(match: Match, e: Event, where: str, issues: list[str]) -> None:
    if e.type not in EVENT_OUTCOMES:
        issues.append(f"{where}: unknown event type {e.type!r}")
        return
    if e.outcome not in EVENT_OUTCOMES[e.type]:
        issues.append(f"{where}: outcome {e.outcome!r} not allowed for {e.type}")
    if e.type == "pass":
        if e.subtype not in PASS_SUBTYPES:
            issues.append(f"{where}: pass subtype {e.subtype!r} invalid")
    elif e.subtype is not None:
        issues.append(f"{where}: subtype only allowed on passes")
    if e.type == "shot":
        if e.xg_heuristic is None or not 0.0 <= e.xg_heuristic <= 1.0:
            issues.append(f"{where}: shot needs xg_heuristic in [0, 1]")
    elif e.xg_heuristic is not None:
        issues.append(f"{where}: xg_heuristic only allowed on shots")

    if e.type in PERIOD_EVENTS:
        if any(v is not None for v in (e.team, e.player_id, e.possession_id, e.start, e.end)):
            issues.append(f"{where}: period marker must not carry team/player/possession/location")
        return

    if e.team not in SIDES:
        issues.append(f"{where}: team must be one of {SIDES}")
        return
    if e.player_id not in match.meta.player_ids(e.team):
        issues.append(f"{where}: player {e.player_id!r} not in {e.team} squad")
    if e.possession_id is None:
        issues.append(f"{where}: possession_id required")
    if e.start is None or not _on_pitch(e.start):
        issues.append(f"{where}: start location missing or off pitch")
    if e.type in BALL_EVENTS and (e.end is None or not _on_pitch(e.end)):
        issues.append(f"{where}: end location missing or off pitch")
    if e.type in OFF_BALL_EVENTS and e.end is not None:
        issues.append(f"{where}: off-ball event must not have an end location")


class EventValidator:
    """The checks of ``validate_match``, one event at a time, in match order.

    ``check`` sees only the events passed so far, so a live feed can be validated as each
    event arrives; ``finish`` adds the checks that need the whole match (both periods
    closed). ``validate_match`` is exactly this, run over a complete match.
    """

    def __init__(self, meta: MatchMeta):
        self._match = Match(meta, ())  # squads for the shape checks; no events needed
        self._plen = meta.period_length_s
        self._seen_ids: set[str] = set()
        self._period_open: int | None = None
        self._periods_closed: list[int] = []
        self._prev_clock = -math.inf
        self._score = {HOME: 0, AWAY: 0}
        self._poss_id: int | None = None  # current possession
        self._poss_team: str | None = None
        self._max_poss_id = 0
        self._prev_ball: Event | None = None  # previous ball event in the open period
        self._expect_kick_off_by: str | None = None
        self._count = 0

    def check(self, e: Event) -> list[str]:
        """Issues with ``e``, given every event checked before it (empty means valid)."""
        issues: list[str] = []
        idx, self._count = self._count, self._count + 1
        where = f"event {e.event_id} (seq {e.seq})"
        if e.seq != idx + 1:
            issues.append(f"{where}: seq must be {idx + 1}")
        if e.related_event_id is not None and e.related_event_id not in self._seen_ids:
            issues.append(f"{where}: related_event_id must reference an earlier event")
        if e.event_id in self._seen_ids:
            issues.append(f"{where}: duplicate event_id")
        self._seen_ids.add(e.event_id)

        # Clock and period structure.
        if e.clock_s < self._prev_clock:
            issues.append(f"{where}: clock went backwards ({e.clock_s} < {self._prev_clock})")
        self._prev_clock = max(self._prev_clock, e.clock_s)
        if e.period not in (1, 2):
            issues.append(f"{where}: period must be 1 or 2")
        else:
            lo, hi = (e.period - 1) * self._plen, e.period * self._plen
            if not lo <= e.clock_s <= hi:
                issues.append(f"{where}: clock {e.clock_s} outside period {e.period} [{lo}, {hi}]")
        if e.type == "period_start":
            expected = len(self._periods_closed) + 1
            if self._period_open is not None or e.period != expected:
                issues.append(f"{where}: unexpected period_start for period {e.period}")
            self._period_open = e.period
            self._prev_ball = None
            self._expect_kick_off_by = None
        elif e.type == "period_end":
            if self._period_open != e.period:
                issues.append(f"{where}: period_end without matching period_start")
            self._periods_closed.append(e.period)
            self._period_open = None
        elif self._period_open != e.period:
            issues.append(f"{where}: event outside an open period {e.period}")

        _check_event_shape(self._match, e, where, issues)

        # Score: changes only on a goal, by exactly one for the scoring team.
        score = self._score
        if e.type == "shot" and e.outcome == "goal" and e.team in SIDES:
            score[e.team] += 1
        if (e.home_score, e.away_score) != (score[HOME], score[AWAY]):
            issues.append(
                f"{where}: score {e.home_score}-{e.away_score} inconsistent with goals "
                f"{score[HOME]}-{score[AWAY]}"
            )

        if e.type in PERIOD_EVENTS or e.team not in SIDES:
            return issues

        # Possession chains.
        if e.type in OFF_BALL_EVENTS:
            if e.possession_id != self._poss_id:
                issues.append(
                    f"{where}: off-ball event must carry current possession {self._poss_id}"
                )
            elif e.team == self._poss_team:
                issues.append(f"{where}: pressure must come from the team out of possession")
            return issues

        prev_ball = self._prev_ball
        starts_new = prev_ball is None or e.team != self._poss_team or _is_restart(e)
        if starts_new:
            if e.possession_id != self._max_poss_id + 1:
                issues.append(
                    f"{where}: new possession must have id {self._max_poss_id + 1}, "
                    f"got {e.possession_id}"
                )
        elif e.possession_id != self._poss_id:
            issues.append(f"{where}: possession {e.possession_id} != current {self._poss_id}")
        if e.possession_id is not None:
            self._max_poss_id = max(self._max_poss_id, e.possession_id)
        self._poss_id, self._poss_team = e.possession_id, e.team

        # Restart sequencing and ball continuity.
        if prev_ball is None:
            if not (e.type == "pass" and e.subtype == "kick_off"):
                issues.append(f"{where}: first ball event of a period must be a kick-off")
        elif self._expect_kick_off_by is not None:
            by = self._expect_kick_off_by
            if not (e.type == "pass" and e.subtype == "kick_off" and e.team == by):
                issues.append(f"{where}: goal must be followed by a {by} kick-off")
        elif prev_ball.outcome in _RESTART_AFTER:
            needed = _RESTART_AFTER[prev_ball.outcome]
            if not (e.type == "pass" and e.subtype == needed and e.team != prev_ball.team):
                issues.append(
                    f"{where}: {prev_ball.outcome} must be followed by an opposing {needed}"
                )
        elif _is_restart(e):
            issues.append(f"{where}: restart without the ball going out of play")
        elif prev_ball.end is not None and e.start is not None:
            gap = math.dist(prev_ball.end, e.start)
            if gap > BALL_CONTINUITY_TOLERANCE_M:
                issues.append(
                    f"{where}: ball starts {gap:.1f} m from previous end ({prev_ball.event_id})"
                )
        scored = e.type == "shot" and e.outcome == "goal"
        self._expect_kick_off_by = _other(e.team) if scored else None
        self._prev_ball = e
        return issues

    def finish(self) -> list[str]:
        """Issues that need the whole match: both periods opened and closed."""
        if self._periods_closed != [1, 2] or self._period_open is not None:
            return [f"match must contain closed periods [1, 2], got {self._periods_closed}"]
        return []


def validate_match(match: Match) -> list[str]:
    issues: list[str] = []
    _check_meta(match, issues)
    if not match.events:
        return [*issues, "match has no events"]
    validator = EventValidator(match.meta)
    for e in match.events:
        issues += validator.check(e)
    return issues + validator.finish()


def assert_valid(match: Match) -> Match:
    issues = validate_match(match)
    if issues:
        raise MatchValidationError(issues)
    return match

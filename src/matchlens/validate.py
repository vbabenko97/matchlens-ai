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
    if len(home_ids) + len(away_ids) != len(meta.home.players) + len(meta.away.players):
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


def validate_match(match: Match) -> list[str]:
    issues: list[str] = []
    _check_meta(match, issues)
    events = match.events
    if not events:
        return [*issues, "match has no events"]

    plen = match.meta.period_length_s
    seen_ids: set[str] = set()
    period_open: int | None = None
    periods_closed: list[int] = []
    prev_clock = -math.inf
    score = {HOME: 0, AWAY: 0}
    poss_id: int | None = None  # current possession
    poss_team: str | None = None
    max_poss_id = 0
    prev_ball: Event | None = None  # previous ball event in the open period
    expect_kick_off_by: str | None = None

    for idx, e in enumerate(events):
        where = f"event {e.event_id} (seq {e.seq})"
        if e.seq != idx + 1:
            issues.append(f"{where}: seq must be {idx + 1}")
        if e.event_id in seen_ids:
            issues.append(f"{where}: duplicate event_id")
        seen_ids.add(e.event_id)
        if e.related_event_id is not None and e.related_event_id not in seen_ids - {e.event_id}:
            issues.append(f"{where}: related_event_id must reference an earlier event")

        # Clock and period structure.
        if e.clock_s < prev_clock:
            issues.append(f"{where}: clock went backwards ({e.clock_s} < {prev_clock})")
        prev_clock = max(prev_clock, e.clock_s)
        if e.period not in (1, 2):
            issues.append(f"{where}: period must be 1 or 2")
        else:
            lo, hi = (e.period - 1) * plen, e.period * plen
            if not lo <= e.clock_s <= hi:
                issues.append(f"{where}: clock {e.clock_s} outside period {e.period} [{lo}, {hi}]")
        if e.type == "period_start":
            expected = len(periods_closed) + 1
            if period_open is not None or e.period != expected:
                issues.append(f"{where}: unexpected period_start for period {e.period}")
            period_open = e.period
            prev_ball = None
            expect_kick_off_by = None
        elif e.type == "period_end":
            if period_open != e.period:
                issues.append(f"{where}: period_end without matching period_start")
            periods_closed.append(e.period)
            period_open = None
        elif period_open != e.period:
            issues.append(f"{where}: event outside an open period {e.period}")

        _check_event_shape(match, e, where, issues)

        # Score: changes only on a goal, by exactly one for the scoring team.
        if e.type == "shot" and e.outcome == "goal" and e.team in SIDES:
            score[e.team] += 1
        if (e.home_score, e.away_score) != (score[HOME], score[AWAY]):
            issues.append(
                f"{where}: score {e.home_score}-{e.away_score} inconsistent with goals "
                f"{score[HOME]}-{score[AWAY]}"
            )

        if e.type in PERIOD_EVENTS or e.team not in SIDES:
            continue

        # Possession chains.
        if e.type in OFF_BALL_EVENTS:
            if e.possession_id != poss_id:
                issues.append(f"{where}: off-ball event must carry current possession {poss_id}")
            elif e.team == poss_team:
                issues.append(f"{where}: pressure must come from the team out of possession")
            continue

        starts_new = prev_ball is None or e.team != poss_team or _is_restart(e)
        if starts_new:
            if e.possession_id != max_poss_id + 1:
                issues.append(
                    f"{where}: new possession must have id {max_poss_id + 1}, got {e.possession_id}"
                )
        elif e.possession_id != poss_id:
            issues.append(f"{where}: possession {e.possession_id} != current {poss_id}")
        if e.possession_id is not None:
            max_poss_id = max(max_poss_id, e.possession_id)
        poss_id, poss_team = e.possession_id, e.team

        # Restart sequencing and ball continuity.
        if prev_ball is None:
            if not (e.type == "pass" and e.subtype == "kick_off"):
                issues.append(f"{where}: first ball event of a period must be a kick-off")
        elif expect_kick_off_by is not None:
            if not (e.type == "pass" and e.subtype == "kick_off" and e.team == expect_kick_off_by):
                issues.append(f"{where}: goal must be followed by a {expect_kick_off_by} kick-off")
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
        expect_kick_off_by = _other(e.team) if e.type == "shot" and e.outcome == "goal" else None
        prev_ball = e

    if periods_closed != [1, 2] or period_open is not None:
        issues.append(f"match must contain closed periods [1, 2], got {periods_closed}")
    return issues


def assert_valid(match: Match) -> Match:
    issues = validate_match(match)
    if issues:
        raise MatchValidationError(issues)
    return match

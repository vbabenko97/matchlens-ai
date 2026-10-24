"""Deterministic key-moment detectors.

Each ``KeyMoment`` is derived only from simulated events and lists them as evidence.
Some detectors need events after the anchor (the shot after a press win, the save after a
missed chance), so a moment is fully known only at ``known_clock_s``, the time of its last
evidence event; anything shown during a replay must wait until then.
Detectors:
- ``goal``: a goal with its build-up, i.e. the scoring possession chain up to the shot.
- ``big_chance_missed``: a non-goal shot whose synthetic xG heuristic >= ``BIG_CHANCE_XG``.
- ``press_win``: a tackle/interception by a team that pressed the same opponent
  possession within ``PRESS_WINDOW_S`` seconds before winning the ball, and whose won
  possession produced a shot within ``PRESS_SHOT_WINDOW_S`` seconds.
- ``full_time``: the final whistle (the period-2 ``period_end``), with every goal as evidence.

Selection (``select_key_moments``) is causal: whether a moment gets a card depends only on
the moment itself, never on what happens later, so a live feed could apply the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .claims import HEURISTIC_NOTE, player_name
from .schema import AWAY, BALL_EVENTS, HOME, Event, Match, minute_of

BIG_CHANCE_XG = 0.2
PRESS_WINDOW_S = 8.0
PRESS_SHOT_WINDOW_S = 20.0
_KIND_PRIORITY = {"goal": 0, "big_chance_missed": 1, "press_win": 2, "full_time": 3}
CARD_KINDS = ("goal", "big_chance_missed", "full_time")  # every one of these gets a card
DRAW = "draw"  # the ``team`` of a full-time moment without a winner


@dataclass(frozen=True, slots=True)
class KeyMoment:
    moment_id: str
    kind: str
    team: str  # the side the moment is about; for ``full_time`` the winner or ``DRAW``
    anchor_event_id: str
    clock_s: float  # anchor event time
    start_clock_s: float  # earliest evidence event time
    known_clock_s: float  # last evidence event time: the moment cannot be known before it
    event_ids: tuple[str, ...]  # evidence, in match order
    details: dict[str, Any]


def _moment(kind: str, anchor: Event, evidence: list[Event], **details: Any) -> KeyMoment:
    evidence = sorted(evidence, key=lambda e: e.seq)
    assert anchor.team is not None
    return KeyMoment(
        moment_id=f"{kind}:{anchor.event_id}",
        kind=kind,
        team=anchor.team,
        anchor_event_id=anchor.event_id,
        clock_s=anchor.clock_s,
        start_clock_s=evidence[0].clock_s,
        known_clock_s=evidence[-1].clock_s,
        event_ids=tuple(e.event_id for e in evidence),
        details=details,
    )


def detect_goals(match: Match) -> list[KeyMoment]:
    out = []
    for shot in match.events:
        if shot.type != "shot" or shot.outcome != "goal":
            continue
        chain = [
            e
            for e in match.events
            if e.possession_id == shot.possession_id
            and e.seq <= shot.seq
            and (e.type in BALL_EVENTS or e.type == "pressure")
        ]
        first = next(e for e in chain if e.type in BALL_EVENTS)
        out.append(
            _moment(
                "goal",
                shot,
                chain,
                player_id=shot.player_id,
                score=(shot.home_score, shot.away_score),
                build_up_passes=sum(e.type == "pass" and e.seq < shot.seq for e in chain),
                chain_start_event_id=first.event_id,
                chain_start=first.subtype if first.type == "pass" else first.type,
                xg_heuristic=shot.xg_heuristic,
            )
        )
    return out


def detect_big_chances_missed(match: Match, min_xg: float = BIG_CHANCE_XG) -> list[KeyMoment]:
    out = []
    events = match.events
    for i, shot in enumerate(events):
        if shot.type != "shot" or shot.outcome == "goal" or (shot.xg_heuristic or 0) < min_xg:
            continue
        evidence = [shot]
        if i + 1 < len(events) and events[i + 1].related_event_id == shot.event_id:
            evidence.append(events[i + 1])  # save, goal kick or clearance answering the shot
        out.append(
            _moment(
                "big_chance_missed",
                shot,
                evidence,
                player_id=shot.player_id,
                outcome=shot.outcome,
                xg_heuristic=shot.xg_heuristic,
            )
        )
    return out


def detect_press_wins(
    match: Match, window_s: float = PRESS_WINDOW_S, shot_window_s: float = PRESS_SHOT_WINDOW_S
) -> list[KeyMoment]:
    out = []
    events = match.events
    last_ball: Event | None = None
    for i, win in enumerate(events):
        if win.type in ("tackle", "interception") and last_ball is not None:
            presses = [
                e
                for e in events[:i]
                if e.type == "pressure"
                and e.team == win.team
                and e.possession_id == last_ball.possession_id
                and win.clock_s - e.clock_s <= window_s
            ]
            shot = next(
                (
                    e
                    for e in events[i + 1 :]
                    if e.type == "shot"
                    and e.possession_id == win.possession_id
                    and e.clock_s - win.clock_s <= shot_window_s
                ),
                None,
            )
            if presses and shot is not None:
                press = presses[-1]
                evidence = [*events[press.seq - 1 : i + 1], shot]
                out.append(
                    _moment(
                        "press_win",
                        win,
                        evidence,
                        player_id=win.player_id,
                        pressure_event_id=press.event_id,
                        win_type=win.type,
                        seconds_to_win=round(win.clock_s - press.clock_s, 1),
                        shot_event_id=shot.event_id,
                        xg_heuristic=shot.xg_heuristic,
                    )
                )
        if win.type in BALL_EVENTS:
            last_ball = win
    return out


def detect_full_time(match: Match) -> list[KeyMoment]:
    """The final whistle, if the match has one; evidence is every goal and the whistle."""
    end = match.events[-1] if match.events else None
    if end is None or end.type != "period_end" or end.period != 2:
        return []
    goals = [e for e in match.events if e.type == "shot" and e.outcome == "goal"]
    h, a = end.home_score, end.away_score
    return [
        KeyMoment(
            moment_id=f"full_time:{end.event_id}",
            kind="full_time",
            team=HOME if h > a else AWAY if a > h else DRAW,
            anchor_event_id=end.event_id,
            clock_s=end.clock_s,
            start_clock_s=end.clock_s,  # a summary: its replay lead-in is the whistle itself
            known_clock_s=end.clock_s,
            event_ids=(*(g.event_id for g in goals), end.event_id),
            details={"score": (h, a), "goal_event_ids": tuple(g.event_id for g in goals)},
        )
    ]


def detect_moments(match: Match) -> list[KeyMoment]:
    """All detected moments in match order (ties broken by kind priority)."""
    moments = (
        detect_goals(match)
        + detect_big_chances_missed(match)
        + detect_press_wins(match)
        + detect_full_time(match)
    )
    seq = {e.event_id: e.seq for e in match.events}
    return sorted(moments, key=lambda m: (seq[m.anchor_event_id], _KIND_PRIORITY[m.kind]))


def select_key_moments(moments: list[KeyMoment], limit: int | None = None) -> list[KeyMoment]:
    """The moments that get a card, in match order: every goal, every missed big chance and
    the final whistle (``CARD_KINDS``); ``limit`` keeps only the first ``limit`` of them.

    Each moment is chosen on its own kind, never by ranking it against later moments, so the
    choice never uses events after the moment (unlike a "top N of the match" pick).
    """
    chosen = sorted(
        (m for m in moments if m.kind in CARD_KINDS),
        key=lambda m: (m.clock_s, _KIND_PRIORITY[m.kind], m.anchor_event_id),
    )
    return chosen if limit is None else chosen[:limit]


def describe_moment(moment: KeyMoment, match: Match) -> str:
    """Template-rendered, evidence-citing description built only from structured fields."""
    d = moment.details
    team = match.meta.team(moment.team).name
    who = player_name(match, moment.team, d.get("player_id"))
    minute = minute_of(moment.clock_s)
    if moment.kind == "goal":
        h, a = d["score"]
        start = d["chain_start"].replace("_", " ")
        text = (
            f"{minute}' GOAL — {who} scores for {team} ({h}-{a}). Move began with a {start} "
            f"and included {d['build_up_passes']} pass(es); shot quality "
            f"{d['xg_heuristic']:.2f} xG [{HEURISTIC_NOTE}]."
        )
    elif moment.kind == "big_chance_missed":
        text = (
            f"{minute}' Big chance — {who} ({team}) shot {d['outcome'].replace('_', ' ')} "
            f"from a {d['xg_heuristic']:.2f} xG position [{HEURISTIC_NOTE}]."
        )
    elif moment.kind == "full_time":
        h, a = d["score"]
        text = f"Full time: {match.meta.home.name} {h}-{a} {match.meta.away.name}."
    else:
        text = (
            f"{minute}' Press win — {team} pressed and {who} won the ball by "
            f"{d['win_type']} {d['seconds_to_win']:.1f} s later, leading to a shot of "
            f"{d['xg_heuristic']:.2f} xG [{HEURISTIC_NOTE}]."
        )
    return f"{text} [evidence: {', '.join(moment.event_ids)}]"

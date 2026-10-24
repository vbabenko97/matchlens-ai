"""Why a moment matters: match-story lines built from the score history so far.

A ``StoryLine`` is structured like a claim: a kind, a team and the events it cites. Its text
is rendered from those cited events only (scores after each event, minutes, players), so
``check_line`` can re-render it from the cited events and test the kind's condition on them
without trusting the code that chose them. Lines that need the whole score history ("for
the first time", "the lead changed hands twice") cite every goal so far; ``check_line``
then also checks that no goal is missing in between (each cited goal adds exactly one).

Causality: ``goal_story``, ``chance_story`` and ``full_time_story`` look only at events up
to the moment's own event (``_goals_upto``), so a line never depends on later events.

Kinds (``team`` is the side the line is about):
- goal situation (exactly one per goal): ``opener``, ``equaliser``, ``comeback`` (takes the
  lead after trailing), ``back_in_front`` (takes the lead again, never trailed),
  ``extends_lead``, ``pulls_back``;
- goal extras: ``redemption`` (the scorer missed a big chance earlier), ``brace``;
- missed big chance: ``stakes`` (what a goal would have meant);
- full time: ``ft_comeback`` or ``ft_result``, ``winner`` (the goal that won it),
  ``lead_changes``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .claims import player_name
from .moments import BIG_CHANCE_XG
from .schema import AWAY, HOME, Event, Match, minute_of

SITUATIONS = ("opener", "equaliser", "comeback", "back_in_front", "extends_lead", "pulls_back")
_MISSED = {"saved": "saved", "blocked": "blocked", "off_target": "go wide"}
_ORDINAL = {2: "second", 3: "third", 4: "fourth", 5: "fifth"}
_TIMES = {1: "once", 2: "twice"}


@dataclass(frozen=True, slots=True)
class StoryLine:
    kind: str
    team: str
    event_ids: tuple[str, ...]
    text: str


def _other(side: str) -> str:
    return AWAY if side == HOME else HOME


def _margin(e: Event, side: str) -> int:
    """Goals ahead (negative: behind) for ``side`` after event ``e``."""
    return e.home_score - e.away_score if side == HOME else e.away_score - e.home_score


def _total(e: Event) -> int:
    return e.home_score + e.away_score


def _score(e: Event) -> str:
    return f"{e.home_score}-{e.away_score}"


def _is_goal(e: Event) -> bool:
    return e.type == "shot" and e.outcome == "goal"


def _possessive(name: str) -> str:
    return f"{name}'" if name.endswith("s") else f"{name}'s"


def _minutes(events: Sequence[Event]) -> str:
    ms = [str(minute_of(e.clock_s)) for e in events]
    return ms[0] if len(ms) == 1 else f"{', '.join(ms[:-1])} and {ms[-1]}"


def _goals_upto(match: Match, event_id: str) -> list[Event]:
    """Goals up to and including ``event_id``: everything known at that moment."""
    by_id = {e.event_id: e for e in match.events}
    seq = by_id[event_id].seq
    return [e for e in match.events if e.seq <= seq and _is_goal(e)]


def _deepest_in_last_spell(goals: Sequence[Event], side: str) -> int | None:
    """Index of the goal that left ``side`` furthest behind in its latest spell of trailing
    (latest such goal on ties), or None if ``side`` never trailed."""
    last = next((i for i in range(len(goals) - 1, -1, -1) if _margin(goals[i], side) < 0), None)
    if last is None:
        return None
    first = last
    while first > 0 and _margin(goals[first - 1], side) < 0:
        first -= 1
    spell = range(first, last + 1)
    return max(spell, key=lambda i: (-_margin(goals[i], side), i))


# --- Rendering: text from the cited events only ----------------------------------------


def render(kind: str, team: str, ev: Sequence[Event], match: Match) -> str:
    """The line's text, from its kind, team and cited events (in match order)."""
    name = match.meta.team(team).name
    other = match.meta.team(_other(team)).name
    last = ev[-1]
    if kind == "opener":
        return f"First goal of the match: {name} lead {_score(last)}."
    if kind == "equaliser":
        n = minute_of(last.clock_s) - minute_of(ev[0].clock_s)
        when = "in the same minute" if n == 0 else f"{n} minute{'s' if n > 1 else ''} after"
        return f"{name} level it at {_score(last)}, {when} going behind."
    if kind == "comeback":
        mine = [e for e in ev[1:] if e.team == team]
        return (
            f"{name} were {_score(ev[0])} down in minute {minute_of(ev[0].clock_s)}. "
            f"Goals in minutes {_minutes(mine)} turned it around."
        )
    if kind == "back_in_front":
        return f"{name} are back in front, {_score(last)}."
    if kind == "extends_lead":
        return f"{name} now lead by {_margin(last, team)} goals, {_score(last)}."
    if kind == "pulls_back":
        return f"{name} pull one back: {_score(last)}, {other} still lead."
    if kind == "redemption":
        who = player_name(match, team, last.player_id)
        what = _MISSED[ev[0].outcome or ""]
        return (
            f"{who} saw a big chance {what} in minute {minute_of(ev[0].clock_s)}; "
            "this time it went in."
        )
    if kind == "brace":
        who = player_name(match, team, last.player_id)
        n = sum(e.player_id == last.player_id and e.team == team for e in ev)
        return f"{_possessive(who)} {_ORDINAL.get(n, f'{n}th')} goal of the match."
    if kind == "stakes":
        m = _margin(last, team)
        if m == 0:
            effect = f"put {name} ahead"
        elif m == -1:
            effect = "levelled it"
        elif m == 1:
            effect = f"doubled {_possessive(name)} lead"
        else:
            h, a = last.home_score + (team == HOME), last.away_score + (team == AWAY)
            effect = f"made it {h}-{a}"
        return f"It stays {_score(last)}; a goal would have {effect}."
    if kind == "ft_comeback":
        return (
            f"{name} were {_score(ev[0])} down in minute {minute_of(ev[0].clock_s)} "
            f"and won {_score(last)}."
        )
    if kind == "ft_result":
        if team in (HOME, AWAY):
            return f"{name} win {_score(last)}."
        return f"It ends level, {_score(last)}."
    if kind == "winner":
        goal = next(e for e in ev if _is_goal(e) and e.team == team)
        who = player_name(match, team, goal.player_id)
        text = f"{who} scored the winner in minute {minute_of(goal.clock_s)}"
        miss = ev[0] if ev[0].type == "shot" and not _is_goal(ev[0]) else None
        if miss is not None:
            text += (
                f", after seeing a big chance {_MISSED[miss.outcome or '']} "
                f"in minute {minute_of(miss.clock_s)}"
            )
        return text + "."
    if kind == "lead_changes":
        n = _lead_changes([e for e in ev if _is_goal(e)])
        return f"The lead changed hands {_TIMES.get(n, f'{n} times')}."
    raise ValueError(f"unknown story kind {kind!r}")


def _lead_changes(goals: Sequence[Event]) -> int:
    leader, changes = None, 0
    for g in goals:
        m = g.home_score - g.away_score
        now = HOME if m > 0 else AWAY if m < 0 else None
        if now is not None:
            changes += leader is not None and now != leader
            leader = now
    return changes


def _line(kind: str, team: str, ev: Sequence[Event], match: Match) -> StoryLine:
    return StoryLine(kind, team, tuple(e.event_id for e in ev), render(kind, team, ev, match))


# --- Deriving the lines of a moment (causal) -------------------------------------------


def goal_story(match: Match, goal_id: str) -> tuple[StoryLine, ...]:
    """Situation line first, then extras, for the goal ``goal_id``."""
    goals = _goals_upto(match, goal_id)
    g = goals[-1]
    assert g.event_id == goal_id and g.team is not None
    x = g.team
    before = goals[:-1]
    m = _margin(g, x)
    if not before:
        lines = [_line("opener", x, [g], match)]
    elif m == 0:
        start = len(before) - 1  # the goal that put x behind in this spell of trailing
        while start > 0 and _margin(before[start - 1], x) < 0:
            start -= 1
        lines = [_line("equaliser", x, goals[start:], match)]
    elif m == 1:
        deepest = _deepest_in_last_spell(before, x)
        if deepest is not None:
            lines = [_line("comeback", x, goals[deepest:], match)]
        else:  # took the lead from level without ever trailing: led before
            lines = [_line("back_in_front", x, goals, match)]
    elif m > 1:
        lines = [_line("extends_lead", x, [g], match)]
    else:
        lines = [_line("pulls_back", x, [g], match)]
    miss = _last_big_miss(match, g)
    if miss is not None:
        lines.append(_line("redemption", x, [miss, g], match))
    if sum(e.team == x and e.player_id == g.player_id for e in goals) >= 2:
        lines.append(_line("brace", x, goals, match))
    return tuple(lines)


def _last_big_miss(match: Match, goal: Event) -> Event | None:
    misses = [
        e
        for e in match.events
        if e.seq < goal.seq
        and e.type == "shot"
        and not _is_goal(e)
        and e.team == goal.team
        and e.player_id == goal.player_id
        and (e.xg_heuristic or 0) >= BIG_CHANCE_XG
    ]
    return misses[-1] if misses else None


def chance_story(match: Match, shot_id: str) -> tuple[StoryLine, ...]:
    shot = next(e for e in match.events if e.event_id == shot_id)
    assert shot.team is not None
    return (_line("stakes", shot.team, [shot], match),)


def full_time_story(match: Match, end_id: str) -> tuple[StoryLine, ...]:
    """Result first (with the comeback, if the winner came from behind), then the winning
    goal and how often the lead changed hands."""
    goals = _goals_upto(match, end_id)
    end = next(e for e in match.events if e.event_id == end_id)
    m = end.home_score - end.away_score
    winner = HOME if m > 0 else AWAY if m < 0 else None
    lines = []
    deepest = _deepest_in_last_spell(goals, winner) if winner else None
    if winner and deepest is not None:
        lines.append(_line("ft_comeback", winner, [*goals[deepest:], end], match))
    else:
        lines.append(_line("ft_result", winner or "draw", [*goals, end], match))
    if winner:
        loser_final = end.away_score if winner == HOME else end.home_score
        mine = [g for g in goals if g.team == winner]
        decisive = mine[loser_final]  # the goal that took the winner past the loser's total
        miss = _last_big_miss(match, decisive)
        ev = [decisive, end] if miss is None else [miss, decisive, end]
        lines.append(_line("winner", winner, ev, match))
    if _lead_changes(goals) >= 1:
        lines.append(_line("lead_changes", winner or "draw", [*goals, end], match))
    return tuple(lines)


# --- Checking a line against its cited events ------------------------------------------


def _consecutive(goals: Sequence[Event], from_start: bool) -> bool:
    """Each cited goal adds exactly one to the total: no goal is missing in between."""
    if not goals or any(not _is_goal(g) for g in goals):
        return False
    totals = [_total(g) for g in goals]
    if from_start and totals[0] != 1:
        return False
    return all(b == a + 1 for a, b in zip(totals, totals[1:], strict=False))


def _ends_with(goals: Sequence[Event], end: Event) -> bool:
    """``end`` happened after the cited goals and no goal came in between."""
    return end.type == "period_end" and _total(end) == (_total(goals[-1]) if goals else 0)


def check_line(line: StoryLine, match: Match, as_of_event_id: str) -> list[str]:
    """Reasons the line is not supported by its cited events ([] when it is)."""
    by_id = {e.event_id: e for e in match.events}
    missing = [i for i in line.event_ids if i not in by_id]
    if missing or not line.event_ids:
        return [f"cited events not in match: {missing}"] if missing else ["cites no events"]
    ev = [by_id[i] for i in line.event_ids]
    if [e.seq for e in ev] != sorted(e.seq for e in ev):
        return ["cited events are not in match order"]
    if ev[-1].seq > by_id[as_of_event_id].seq:
        return [f"cites events after {as_of_event_id}"]
    try:
        text = render(line.kind, line.team, ev, match)
    except (ValueError, KeyError, StopIteration, IndexError):
        return [f"{line.kind} cannot be rendered from its cited events"]
    reasons = [] if text == line.text else ["text does not match its cited events"]
    if not _condition(line.kind, line.team, ev):
        reasons.append(f"cited events do not show a {line.kind}")
    return reasons


def _condition(kind: str, x: str, ev: Sequence[Event]) -> bool:
    last = ev[-1]
    goals = [e for e in ev if _is_goal(e)]
    if kind == "opener":
        return len(ev) == 1 and _is_goal(last) and last.team == x and _total(last) == 1
    if kind == "equaliser":
        return (
            _consecutive(ev, False)
            and last.team == x
            and _margin(last, x) == 0
            and all(_margin(e, x) < 0 for e in ev[:-1])
            and ev[0].team != x
            and _margin(ev[0], x) == -1  # it put x behind from level
        )
    if kind == "comeback":
        return (
            _consecutive(ev, False)
            and len(ev) >= 3
            and _margin(ev[0], x) < 0
            and ev[0].team != x
            and last.team == x
            and _margin(last, x) == 1
            and _margin(ev[-2], x) == 0
        )
    if kind == "back_in_front":
        return (
            _consecutive(ev, True)
            and last.team == x
            and _margin(last, x) == 1
            and len(ev) >= 2
            and _margin(ev[-2], x) == 0
            and all(_margin(e, x) >= 0 for e in ev)
            and any(_margin(e, x) > 0 for e in ev[:-1])
        )
    if kind == "extends_lead":
        return len(ev) == 1 and _is_goal(last) and last.team == x and _margin(last, x) >= 2
    if kind == "pulls_back":
        return len(ev) == 1 and _is_goal(last) and last.team == x and _margin(last, x) < 0
    if kind == "redemption":
        miss = ev[0]
        return (
            len(ev) == 2
            and miss.type == "shot"
            and miss.outcome in _MISSED
            and (miss.xg_heuristic or 0) >= BIG_CHANCE_XG
            and _is_goal(last)
            and miss.team == last.team == x
            and miss.player_id == last.player_id
        )
    if kind == "brace":
        n = sum(e.player_id == last.player_id and e.team == x for e in ev)
        return _consecutive(ev, True) and last.team == x and n >= 2
    if kind == "stakes":
        return (
            len(ev) == 1
            and last.type == "shot"
            and not _is_goal(last)
            and last.team == x
            and (last.xg_heuristic or 0) >= BIG_CHANCE_XG
        )
    if kind == "ft_comeback":
        end, gs = ev[-1], ev[:-1]
        return (
            _consecutive(gs, False)
            and _ends_with(gs, end)
            and gs[0].team != x
            and _margin(gs[0], x) < 0
            and _margin(end, x) > 0
        )
    if kind == "ft_result":
        end, gs = ev[-1], ev[:-1]
        m = end.home_score - end.away_score
        side = HOME if m > 0 else AWAY if m < 0 else "draw"
        complete = _consecutive(gs, True) if gs else True
        return complete and _ends_with(gs, end) and side == x
    if kind == "winner":
        end = ev[-1]
        decisive = ev[-2]
        loser = end.away_score if x == HOME else end.home_score
        mine = decisive.home_score if x == HOME else decisive.away_score
        ok = (
            end.type == "period_end"
            and _is_goal(decisive)
            and decisive.team == x
            and _margin(end, x) > 0
            and mine == loser + 1  # took x past the loser's final total: never caught again
        )
        if len(ev) == 3:
            miss = ev[0]
            ok = ok and (
                miss.type == "shot"
                and miss.outcome in _MISSED
                and miss.team == x
                and miss.player_id == decisive.player_id
                and (miss.xg_heuristic or 0) >= BIG_CHANCE_XG
            )
        return ok and len(ev) in (2, 3)
    if kind == "lead_changes":
        end = ev[-1]
        return _consecutive(goals, True) and _ends_with(goals, end) and _lead_changes(goals) >= 1
    return False

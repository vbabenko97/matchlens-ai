from dataclasses import replace

import pytest

from matchlens.claims import HEURISTIC_NOTE
from matchlens.generator import generate_match
from matchlens.moments import (
    CARD_KINDS,
    DRAW,
    describe_moment,
    detect_big_chances_missed,
    detect_full_time,
    detect_goals,
    detect_moments,
    detect_press_wins,
    select_key_moments,
)
from matchlens.schema import Match

from .hand_match import eid, hand_match


@pytest.fixture(scope="module")
def match():
    return hand_match()


def _ids(*seqs):
    return tuple(eid(s) for s in seqs)


def test_goal_with_build_up(match):
    [goal] = detect_goals(match)
    assert goal.moment_id == f"goal:{eid(14)}"
    assert (goal.team, goal.clock_s, goal.start_clock_s) == ("home", 50.0, 44.0)
    # Possession 5: tackle (12) -> pass (13) -> goal (14).
    assert goal.event_ids == _ids(12, 13, 14)
    assert goal.details["build_up_passes"] == 1
    assert goal.details["chain_start"] == "tackle"
    assert goal.details["score"] == (1, 0)
    assert goal.details["xg_heuristic"] == 0.084


def test_big_chance_missed(match):
    [chance] = detect_big_chances_missed(match)
    assert chance.anchor_event_id == eid(22)
    assert chance.event_ids == _ids(22, 23)  # shot + keeper recovery answering it
    assert chance.details == {"player_id": "NBR-11", "outcome": "saved", "xg_heuristic": 0.207}
    assert detect_big_chances_missed(match, min_xg=0.3) == []


def test_press_win(match):
    [press] = detect_press_wins(match)
    # Away presses (4) the home pass (5), intercepts it (6) two seconds later and shoots (8)
    # six seconds after winning the ball.
    assert press.team == "away" and press.anchor_event_id == eid(6)
    assert press.event_ids == _ids(4, 5, 6, 8)
    assert press.details["seconds_to_win"] == 2.0
    assert press.details["shot_event_id"] == eid(8)
    assert press.details["xg_heuristic"] == 0.001
    # The home tackle (12) had no preceding home pressure, so it is not a press win.
    assert detect_press_wins(match, window_s=1.0) == []  # pressure too early
    assert detect_press_wins(match, shot_window_s=5.0) == []  # shot too late


def test_detect_and_select(match):
    moments = detect_moments(match)
    assert [m.kind for m in moments] == ["press_win", "goal", "big_chance_missed", "full_time"]
    # Every goal, every missed big chance and the final whistle get a card, in match order.
    chosen = select_key_moments(moments)
    assert [m.kind for m in chosen] == ["goal", "big_chance_missed", "full_time"]
    assert select_key_moments(moments, limit=2) == chosen[:2]
    assert [m.kind for m in select_key_moments(moments, limit=1)] == ["goal"]


def test_full_time_moment(match):
    [ft] = detect_full_time(match)
    assert ft.moment_id == f"full_time:{eid(24)}" and ft.team == "home"
    assert ft.event_ids == _ids(14, 24)  # every goal, then the final whistle
    assert ft.clock_s == ft.start_clock_s == ft.known_clock_s == 120.0
    assert ft.details == {"score": (1, 0), "goal_event_ids": _ids(14)}
    cut = Match(match.meta, match.events[:-1])  # no final whistle yet
    assert detect_full_time(cut) == []
    level = Match(match.meta, tuple(replace(e, home_score=0) for e in match.events))
    assert detect_full_time(level)[0].team == DRAW


@pytest.mark.parametrize("seed", range(1, 21))
def test_selection_never_depends_on_later_events(seed):
    """Cut the match right after a moment is known: it is still chosen, and so is every
    earlier choice. A "top N of the match" pick would fail this."""
    m = generate_match(seed)
    chosen = select_key_moments(detect_moments(m))
    goals = [e.event_id for e in m.events if e.type == "shot" and e.outcome == "goal"]
    assert [c.anchor_event_id for c in chosen if c.kind == "goal"] == goals  # every goal
    seq = {e.event_id: e.seq for e in m.events}
    for c in chosen:
        cut = Match(m.meta, m.events[: seq[c.event_ids[-1]]])
        so_far = select_key_moments(detect_moments(cut))
        assert so_far == [x for x in chosen if seq[x.event_ids[-1]] <= len(cut.events)]


def test_descriptions_are_templated_and_cite_evidence(match):
    goal = detect_goals(match)[0]
    text = describe_moment(goal, match)
    assert text.startswith("1' GOAL — ") and "Northbridge Rovers (1-0)" in text
    assert HEURISTIC_NOTE in text
    assert text.endswith(f"[evidence: {eid(12)}, {eid(13)}, {eid(14)}]")
    for m in detect_moments(match):
        assert all(i in describe_moment(m, match) for i in m.event_ids)


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_generated_match_properties(seed):
    m = generate_match(seed)
    by_id = {e.event_id: e for e in m.events}
    moments = detect_moments(m)
    assert moments == detect_moments(generate_match(seed))  # deterministic
    assert len(detect_goals(m)) == sum(m.final_score)
    for mo in moments:
        evidence = [by_id[i] for i in mo.event_ids]
        assert mo.anchor_event_id in mo.event_ids
        assert [e.seq for e in evidence] == sorted(e.seq for e in evidence)
        if mo.kind == "full_time":  # a summary: shown from the whistle, cites every goal
            assert mo.start_clock_s == mo.clock_s == mo.known_clock_s == evidence[-1].clock_s
            assert len(evidence) == sum(m.final_score) + 1
        else:
            assert mo.start_clock_s == evidence[0].clock_s <= mo.clock_s
        assert mo.clock_s <= mo.known_clock_s == evidence[-1].clock_s
        if mo.kind == "goal":
            assert {e.possession_id for e in evidence} == {by_id[mo.anchor_event_id].possession_id}
        if mo.kind == "press_win":
            assert mo.details["seconds_to_win"] <= 8.0
    chosen = select_key_moments(moments)
    assert chosen == [mo for mo in moments if mo.kind in CARD_KINDS]
    assert chosen[-1].kind == "full_time"
    assert chosen == select_key_moments(list(reversed(moments)))

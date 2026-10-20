"""Deliberate corruptions of a valid match must be rejected."""

from dataclasses import replace

import pytest

from matchlens.generator import generate_match
from matchlens.schema import AWAY, HOME, Event, Match
from matchlens.validate import MatchValidationError, assert_valid, validate_match


@pytest.fixture(scope="module")
def match() -> Match:
    m = generate_match(11)
    assert validate_match(m) == []
    return m


def _swap(match: Match, idx: int, **changes) -> Match:
    events = list(match.events)
    events[idx] = replace(events[idx], **changes)
    return replace(match, events=tuple(events))


def _first(match: Match, pred) -> int:
    return next(i for i, e in enumerate(match.events) if pred(e))


def _other(side: str) -> str:
    return AWAY if side == HOME else HOME


def _assert_rejected(match: Match, fragment: str) -> None:
    issues = validate_match(match)
    assert any(fragment in issue for issue in issues), issues


def test_duplicate_event_id(match):
    _assert_rejected(_swap(match, 5, event_id=match.events[4].event_id), "duplicate event_id")


def test_sequence_gap(match):
    _assert_rejected(_swap(match, 10, seq=99), "seq must be")


def test_clock_goes_backwards(match):
    _assert_rejected(_swap(match, 50, clock_s=match.events[49].clock_s - 5), "clock went backwards")


def test_score_mismatch(match):
    _assert_rejected(_swap(match, 20, home_score=match.events[20].home_score + 1), "score")


def test_goal_removed_breaks_score_chain(match):
    idx = _first(match, lambda e: e.type == "shot" and e.outcome == "goal")
    _assert_rejected(_swap(match, idx, outcome="saved"), "inconsistent with goals")


def test_possession_team_flip(match):
    idx = _first(match, lambda e: e.type == "pass" and e.subtype == "open_play" and e.seq > 30)
    corrupted = _swap(match, idx, team=_other(match.events[idx].team))
    assert validate_match(corrupted)


def test_wrong_possession_id(match):
    idx = _first(match, lambda e: e.type == "pass" and e.subtype == "open_play" and e.seq > 30)
    _assert_rejected(
        _swap(match, idx, possession_id=match.events[idx].possession_id + 7), "possession"
    )


def test_pressure_from_possessing_team(match):
    idx = _first(match, lambda e: e.type == "pressure")
    ev = match.events[idx]
    players = match.meta.team(_other(ev.team)).players
    _assert_rejected(
        _swap(match, idx, team=_other(ev.team), player_id=players[3].player_id),
        "team out of possession",
    )


def test_ball_teleport(match):
    idx = _first(
        match,
        lambda e: e.type == "pass" and e.subtype == "open_play" and e.seq > 30,
    )
    ev: Event = match.events[idx]
    moved = (min(ev.start[0] + 20, 104.0), ev.start[1])
    _assert_rejected(_swap(match, idx, start=moved), "m from previous end")


def test_off_pitch_location(match):
    idx = _first(match, lambda e: e.type == "pass" and e.seq > 30)
    _assert_rejected(_swap(match, idx, end=(120.0, 34.0)), "off pitch")


def test_player_not_in_squad(match):
    idx = _first(match, lambda e: e.type == "pass")
    _assert_rejected(_swap(match, idx, player_id="XXX-99"), "not in")


def test_xg_on_non_shot(match):
    idx = _first(match, lambda e: e.type == "pass")
    _assert_rejected(_swap(match, idx, xg_heuristic=0.2), "xg_heuristic only allowed")


def test_unknown_type_and_bad_outcome(match):
    _assert_rejected(_swap(match, 3, type="teleport"), "unknown event type")
    idx = _first(match, lambda e: e.type == "shot")
    _assert_rejected(_swap(match, idx, outcome="complete"), "not allowed for shot")


def test_missing_kick_off_after_goal(match):
    idx = _first(match, lambda e: e.type == "shot" and e.outcome == "goal")
    _assert_rejected(_swap(match, idx + 1, subtype="open_play"), "kick-off")


def test_missing_period_end(match):
    _assert_rejected(replace(match, events=match.events[:-1]), "closed periods")


def test_assert_valid_raises(match):
    with pytest.raises(MatchValidationError):
        assert_valid(_swap(match, 5, event_id=match.events[4].event_id))
    assert assert_valid(match) is match


def test_related_event_must_be_earlier(match):
    _assert_rejected(_swap(match, 5, related_event_id=match.events[5].event_id), "related_event_id")
    _assert_rejected(_swap(match, 5, related_event_id=match.events[9].event_id), "related_event_id")

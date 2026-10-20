from collections import defaultdict

import pytest

from matchlens.generator import generate_match, xg_heuristic
from matchlens.io import dumps_match
from matchlens.schema import AWAY, BALL_EVENTS, HOME
from matchlens.validate import validate_match


def test_same_seed_is_byte_identical():
    assert dumps_match(generate_match(123)) == dumps_match(generate_match(123))


def test_different_seeds_differ():
    assert dumps_match(generate_match(1)) != dumps_match(generate_match(2))


@pytest.mark.parametrize("seed", range(20))
def test_generated_matches_satisfy_invariants(seed):
    assert validate_match(generate_match(seed)) == []


@pytest.mark.parametrize("period_s", [60, 300, 600])
def test_short_matches_are_valid(period_s):
    assert validate_match(generate_match(5, period_length_s=period_s)) == []


def test_full_match_shape(full_match):
    events = full_match.events
    assert events[0].type == "period_start" and events[-1].type == "period_end"
    assert events[-1].clock_s == 2 * 45 * 60
    assert len({e.event_id for e in events}) == len(events)
    assert all(a.clock_s <= b.clock_s for a, b in zip(events, events[1:], strict=False))
    goals = [e for e in events if e.type == "shot" and e.outcome == "goal"]
    home = sum(e.team == HOME for e in goals)
    assert full_match.final_score == (home, len(goals) - home)


def test_each_possession_belongs_to_one_team(full_match):
    owners: dict[int, set[str]] = defaultdict(set)
    for e in full_match.events:
        if e.type in BALL_EVENTS:
            owners[e.possession_id].add(e.team)
    assert owners and all(len(teams) == 1 for teams in owners.values())
    assert sorted(owners) == list(range(1, len(owners) + 1))


def test_xg_heuristic_only_on_shots_and_ranks_distance():
    m = generate_match(3)
    assert all((e.xg_heuristic is not None) == (e.type == "shot") for e in m.events)
    assert xg_heuristic(HOME, (99.0, 34.0)) > xg_heuristic(HOME, (80.0, 34.0))
    assert xg_heuristic(HOME, (99.0, 34.0)) == xg_heuristic(AWAY, (6.0, 34.0))
    assert xg_heuristic(HOME, (99.0, 34.0)) > xg_heuristic(HOME, (99.0, 10.0))

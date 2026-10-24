"""Player focus: per-player stats add up to the team metrics, are causal and cite their events."""

import pytest

from matchlens.cards import build_cards
from matchlens.generator import generate_match
from matchlens.metrics import HEURISTIC, compute_metrics
from matchlens.moments import detect_goals
from matchlens.narrative import MODES, OfflineTemplateProvider
from matchlens.players import PLAYERS_SCHEMA, STATS, player_records, stat_id, value_at
from matchlens.replay import build_bundle
from matchlens.schema import SIDES


@pytest.fixture(scope="module", params=[1, 106])
def match(request):
    return generate_match(request.param)


@pytest.fixture(scope="module")
def doc(match):
    cards = {m: build_cards(match, m, OfflineTemplateProvider()).cards for m in MODES}
    return player_records(match, cards)


def _final(player, name):
    stat = next(s for s in player["stats"] if s["name"] == name)
    return stat["steps"][-1][1] if stat["steps"] else (None if stat["unit"] == "ratio" else 0)


def test_every_player_of_both_teams_is_listed_once(match, doc):
    assert doc["schema"] == PLAYERS_SCHEMA
    ids = [p["player_id"] for p in doc["players"]]
    expected = [p.player_id for side in SIDES for p in match.meta.team(side).players]
    assert ids == expected
    for p in doc["players"]:
        assert [s["name"] for s in p["stats"]] == [d.name for d in STATS]
        assert all(s["stat_id"] == stat_id(p["player_id"], s["name"]) for s in p["stats"])


@pytest.mark.parametrize(
    "name", ["goals", "shots", "shots_on_target", "passes", "passes_completed", "pressures"]
)
def test_players_add_up_to_the_team_metric(match, doc, name):
    metrics = compute_metrics(match)
    for side in SIDES:
        team = [p for p in doc["players"] if p["team"] == side]
        assert sum(_final(p, name) for p in team) == metrics[f"{side}.{name}"].value


def test_xg_is_labelled_heuristic_and_adds_up(match, doc):
    metrics = compute_metrics(match)
    for side in SIDES:
        team = [p for p in doc["players"] if p["team"] == side]
        assert sum(_final(p, "xg_heuristic") for p in team) == pytest.approx(
            metrics[f"{side}.xg_heuristic"].value, abs=0.01
        )
    for p in doc["players"]:
        kinds = {s["name"]: s["kind"] for s in p["stats"]}
        heuristics = {k for k, v in kinds.items() if v == HEURISTIC}
        assert heuristics == {"xg_heuristic", "xpass", "completed_minus_xpass"}
        assert {v for k, v in kinds.items() if k not in heuristics} == {"fact"}


def test_steps_are_causal_and_cite_the_players_own_events(match, doc):
    by_id = {e.event_id: e for e in match.events}
    for p in doc["players"]:
        for s in p["stats"]:
            seqs = [step[0] for step in s["steps"]]
            assert seqs == sorted(seqs)
            for seq, _, ids in s["steps"]:
                assert ids and all(by_id[i].seq <= seq for i in ids)  # never a later event
                if s["name"] != "goal_involvements":
                    assert [by_id[i].player_id for i in ids] == [p["player_id"]]
                    assert by_id[ids[0]].seq == seq


def test_completion_and_value_at(match, doc):
    for p in doc["players"]:
        passes = _final(p, "passes")
        rate = _final(p, "pass_completion")
        if passes:
            assert rate == round(_final(p, "passes_completed") / passes, 4)
        else:
            assert rate is None
    p = max(doc["players"], key=lambda q: _final(q, "passes"))
    stat = next(s for s in p["stats"] if s["name"] == "passes")
    first, last = stat["steps"][0], stat["steps"][-1]
    assert value_at(stat, first[0] - 1) == 0 and value_at(stat, first[0]) == 1
    assert value_at(stat, last[0] + 10_000) == last[1]


def test_goal_involvements_follow_the_goal_moves(match, doc):
    by_id = {e.event_id: e for e in match.events}
    goals = detect_goals(match)
    for g in goals:
        shot = by_id[g.anchor_event_id]
        scorer = next(p for p in doc["players"] if p["player_id"] == shot.player_id)
        stat = next(s for s in scorer["stats"] if s["name"] == "goal_involvements")
        assert any(step[0] == shot.seq and shot.event_id in step[2] for step in stat["steps"])
    for p in doc["players"]:
        stat = next(s for s in p["stats"] if s["name"] == "goal_involvements")
        for seq, _, ids in stat["steps"]:
            goal = by_id[ids[-1]]
            assert goal.outcome == "goal" and goal.team == p["team"] and goal.seq == seq
            move = next(m for m in goals if m.anchor_event_id == goal.event_id)
            assert set(ids) <= set(move.event_ids)
    total = sum(_final(p, "goal_involvements") for p in doc["players"])
    assert total >= len(goals)  # every goal involves at least its scorer


def test_featured_cards_cite_the_players_events(match, doc):
    cards = {m: build_cards(match, m, OfflineTemplateProvider()).cards for m in MODES}
    for p in doc["players"]:
        own = {e.event_id for e in match.events if e.player_id == p["player_id"]}
        for mode, ids in p["featured_cards"].items():
            for c in cards[mode]:
                assert (c.card_id in ids) == bool(own & set(c.evidence_event_ids))
    scorers = {e.player_id for e in match.events if e.type == "shot" and e.outcome == "goal"}
    for p in doc["players"]:
        if p["player_id"] in scorers:
            assert any("goal" in c for c in p["featured_cards"]["casual"])


def test_the_bundle_carries_the_players_deterministically(match):
    a, b = build_bundle(match), build_bundle(match)
    assert a["players"] == b["players"] and len(a["players"]["players"]) == 22

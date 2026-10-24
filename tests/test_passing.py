"""Pass quality and tempo: facts from the synthetic events, xPass labelled as a heuristic."""

import math

import pytest

from matchlens.generator import generate_match
from matchlens.metrics import HEURISTIC, compute_metrics
from matchlens.passing import (
    LONG_M,
    PROGRESSIVE_M,
    SHORT_M,
    TEAM_STATS,
    band_of,
    pass_features,
    passing_record,
    xpass,
)
from matchlens.replay import build_bundle
from matchlens.schema import SIDES


@pytest.fixture(scope="module", params=[1, 121])
def match(request):
    return generate_match(request.param)


def test_features_cover_open_play_passes_only(match):
    feats = pass_features(match)
    open_play = [e for e in match.events if e.type == "pass" and e.subtype == "open_play"]
    assert [f["event_id"] for f in feats] == [e.event_id for e in open_play]
    by_id = {e.event_id: e for e in match.events}
    for f in feats:
        e = by_id[f["event_id"]]
        assert f["length_m"] == round(math.dist(e.start, e.end), 1)
        assert f["band"] == band_of(f["length_m"])
        assert f["progressive"] == (f["progress_m"] >= PROGRESSIVE_M)
        assert f["completed"] == (e.outcome == "complete")
        assert 0.0 < f["xpass"] < 1.0


def test_bands_and_progress_direction():
    assert band_of(SHORT_M - 0.1) == "short" and band_of(SHORT_M) == "medium"
    assert band_of(LONG_M) == "long"
    feats = pass_features(generate_match(1))
    for f in feats:  # the ball cannot gain more on the goal than it travelled
        assert abs(f["progress_m"]) <= f["length_m"] + 0.15
    assert any(f["progressive"] for f in feats) and not all(f["progressive"] for f in feats)


def test_pressed_passes_follow_an_opponent_pressure(match):
    by_seq = {e.seq: e for e in match.events}
    for f in pass_features(match):
        before = by_seq.get(f["seq"] - 1)
        if f["pressed"]:
            assert before.type == "pressure" and before.team != f["team"]
            assert f["pressure_event_id"] == before.event_id
        else:
            assert f["pressure_event_id"] is None


def test_team_steps_are_causal_and_add_up(match):
    rec = passing_record(match)
    names = [s["name"] for s in rec["stats"]]
    assert names == list(TEAM_STATS)
    feats = pass_features(match)
    metrics = compute_metrics(match)
    for side in SIDES:
        steps = rec["teams"][side]
        mine = [f for f in feats if f["team"] == side]
        assert [s[0] for s in steps] == [f["seq"] for f in mine]
        last = dict(zip(names, steps[-1][1:], strict=True))
        assert last["attempted"] == len(mine)
        assert last["completed"] == sum(f["completed"] for f in mine)
        # restarts excluded: never more than the team's full pass counts
        assert last["attempted"] <= metrics[f"{side}.passes"].value
        assert last["completed"] <= metrics[f"{side}.passes_completed"].value
        assert last["long_completed"] <= last["long_attempted"] <= last["attempted"]
        assert last["pressed_completed"] <= last["pressed_attempted"]
        assert last["completed_minus_xpass"] == pytest.approx(
            last["completed"] - last["xpass"], abs=0.01
        )
        assert last["completion"] == round(last["completed"] / last["attempted"], 4)


def test_heuristics_are_labelled_and_never_called_calibrated(match):
    rec = passing_record(match)
    kinds = {s["name"]: s["kind"] for s in rec["stats"]}
    assert {k for k, v in kinds.items() if v == HEURISTIC} == {"xpass", "completed_minus_xpass"}
    for s in rec["stats"]:
        if s["kind"] == HEURISTIC:
            assert "not calibrated" in s["definition"] or "luck" in s["definition"]


def test_xpass_is_matched_to_the_generator_by_construction_only():
    """On untuned seeds the heuristic sums to the completions within 3 %: it is built from the
    generator's rules, which is all this shows."""
    done = expected = 0.0
    pressed, unpressed = [0, 0], [0, 0]
    for seed in range(100, 130):
        for f in pass_features(generate_match(seed)):
            done += f["completed"]
            expected += f["xpass"]
            bucket = pressed if f["pressed"] else unpressed
            bucket[0] += f["completed"]
            bucket[1] += 1
    assert expected == pytest.approx(done, rel=0.03)
    assert pressed[0] / pressed[1] < unpressed[0] / unpressed[1]  # pressure costs completions


def test_xpass_falls_with_length_pressure_and_the_touchline():
    assert xpass(10, False) > xpass(30, False) > xpass(30, True)
    assert xpass(10, False, start_y=1.0) < xpass(10, False, start_y=34.0)


def test_the_bundle_carries_passing_deterministically(match):
    a, b = build_bundle(match), build_bundle(match)
    assert a["passing"] == b["passing"]
    assert len(a["passing"]["passes"]) == len(pass_features(match))

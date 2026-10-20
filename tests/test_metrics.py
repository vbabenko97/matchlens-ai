import pytest

from matchlens.generator import generate_match, xg_heuristic
from matchlens.metrics import FACT, HEURISTIC, compute_metrics, window_bounds
from matchlens.schema import AWAY, HOME, SIDES
from matchlens.validate import validate_match

from .hand_match import eid, hand_match


@pytest.fixture(scope="module")
def match():
    m = hand_match()
    assert validate_match(m) == []
    return m


@pytest.fixture(scope="module")
def metrics(match):
    return compute_metrics(match, window_s=30)


def _ids(*seqs):
    return tuple(eid(s) for s in seqs)


# Hand-counted from the event table in hand_match.py.
EXPECTED = {
    "home.passes": (5, _ids(2, 3, 5, 9, 13)),
    "home.passes_completed": (4, _ids(2, 3, 9, 13)),
    "home.shots": (3, _ids(10, 14, 22)),
    "home.shots_on_target": (2, _ids(14, 22)),
    "home.goals": (1, _ids(14)),
    "home.pressures": (1, _ids(18)),
    "home.turnovers": (1, _ids(5)),
    "home.possessions": (4, _ids(2, 9, 12, 20)),
    "away.passes": (4, _ids(7, 8, 11, 17)),
    "away.passes_completed": (3, _ids(7, 11, 17)),
    "away.shots": (1, _ids(19)),
    "away.shots_on_target": (0, ()),
    "away.goals": (0, ()),
    "away.pressures": (1, _ids(4)),
    "away.turnovers": (2, _ids(8, 12)),
    "away.possessions": (4, _ids(6, 11, 17, 23)),
}


@pytest.mark.parametrize("metric_id", sorted(EXPECTED))
def test_hand_counted_values_and_sources(metrics, metric_id):
    value, sources = EXPECTED[metric_id]
    m = metrics[metric_id]
    assert (m.value, m.source_event_ids, m.kind) == (value, sources, FACT)


def test_pass_completion(metrics):
    home, away = metrics["home.pass_completion"], metrics["away.pass_completion"]
    assert home.value == 0.8 and away.value == 0.75
    assert home.source_event_ids == EXPECTED["home.passes"][1]
    assert home.numerator_event_ids == EXPECTED["home.passes_completed"][1]


def test_pass_completion_undefined_without_passes(match):
    no_away_passes = match.__class__(
        meta=match.meta,
        events=tuple(e for e in match.events if not (e.type == "pass" and e.team == AWAY)),
    )
    assert compute_metrics(no_away_passes)["away.pass_completion"].value is None


def test_xg_is_heuristic_sum_of_shots(metrics):
    home = metrics["home.xg_heuristic"]
    expected = round(
        xg_heuristic(HOME, (65, 60)) + xg_heuristic(HOME, (90, 34)) + xg_heuristic(HOME, (80, 30)),
        3,
    )
    assert home.kind == HEURISTIC and home.value == expected
    assert home.source_event_ids == _ids(10, 14, 22)
    assert metrics["away.xg_heuristic"].value == xg_heuristic(AWAY, (58, 34))


def test_window_counts(metrics):
    assert window_bounds(hand_match(), 30) == [(0, 30), (30, 60), (60, 90), (90, 120)]
    assert [metrics[f"home.shots@{w}"].value for w in ("0-30", "30-60", "60-90", "90-120")] == [
        1,
        1,
        1,
        0,
    ]
    assert metrics["away.turnovers@0-30"].source_event_ids == _ids(8)
    assert metrics["away.turnovers@30-60"].source_event_ids == _ids(12)
    assert metrics["away.passes@60-90"].window == (60.0, 90.0)


def test_metric_ids_are_unique_and_deterministic(match):
    a, b = compute_metrics(match), compute_metrics(match)
    assert list(a) == list(b) and a == b


def test_window_bounds_rejects_bad_size(match):
    with pytest.raises(ValueError):
        window_bounds(match, 0)


@pytest.mark.parametrize("seed", [1, 42])
def test_generated_match_consistency(seed):
    m = generate_match(seed)
    metrics = compute_metrics(m)
    by_id = {e.event_id: e for e in m.events}
    for metric in metrics.values():
        # Every cited source exists, numerators are subsets, counts equal source sizes.
        assert all(i in by_id for i in metric.source_event_ids)
        assert set(metric.numerator_event_ids) <= set(metric.source_event_ids)
        if metric.unit == "count":
            assert metric.value == len(metric.source_event_ids)
        if metric.window is not None:
            lo, hi = metric.window
            assert all(lo <= by_id[i].clock_s <= hi for i in metric.source_event_ids)
    for team in SIDES:
        windows = [k for k in metrics if k.startswith(f"{team}.shots@")]
        assert sum(metrics[k].value for k in windows) == metrics[f"{team}.shots"].value
    assert (metrics["home.goals"].value, metrics["away.goals"].value) == m.final_score

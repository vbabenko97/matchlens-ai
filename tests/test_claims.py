import pytest

from matchlens.claims import UNSUPPORTED, VERIFIED, Claim, verify_claim
from matchlens.metrics import compute_metrics

from .hand_match import eid, hand_match


@pytest.fixture(scope="module")
def ctx():
    m = hand_match()
    return m, compute_metrics(m, window_s=30)


def _check(ctx, claim):
    m, metrics = ctx
    return verify_claim(claim, metrics, m)


def test_supported_fact_claim(ctx):
    c = Claim("Home scored once.", "home.goals", 1, (eid(14),))
    result = _check(ctx, c)
    assert result.status == VERIFIED and result.verified and result.reasons == ()


def test_supported_ratio_claim(ctx):
    assert _check(ctx, Claim("Home completed 80% of passes.", "home.pass_completion", 0.8)).verified


def test_wrong_value_is_unsupported(ctx):
    r = _check(ctx, Claim("Home scored twice.", "home.goals", 2, (eid(14),)))
    assert r.status == UNSUPPORTED and "claimed 2" in r.reasons[0]


def test_unknown_metric(ctx):
    r = _check(ctx, Claim("Home ran 120 km.", "home.distance_km", 120))
    assert not r.verified and "unknown metric" in r.reasons[0]


def test_no_evidence(ctx):
    r = _check(ctx, Claim("Home dominated."))
    assert not r.verified and "no metric and no events" in r.reasons[0]


def test_nonexistent_event(ctx):
    r = _check(ctx, Claim("Goal!", "home.goals", 1, ("hand-99999",)))
    assert not r.verified and any("not in match" in x for x in r.reasons)


def test_event_not_a_source_of_metric(ctx):
    # Event 22 is a saved shot, not a goal.
    r = _check(ctx, Claim("Home scored once.", "home.goals", 1, (eid(22),)))
    assert not r.verified and any("not sources" in x for x in r.reasons)


def test_metric_without_value(ctx):
    assert not _check(ctx, Claim("Home had shots.", "home.shots")).verified


def test_undefined_metric_is_unsupported(ctx):
    m, _ = ctx
    stripped = m.__class__(
        meta=m.meta,
        events=tuple(e for e in m.events if not (e.type == "pass" and e.team == "away")),
    )
    r = verify_claim(
        Claim("Away completed 0% of passes.", "away.pass_completion", 0.0),
        compute_metrics(stripped),
        stripped,
    )
    assert not r.verified and "undefined" in r.reasons[0]


def test_heuristic_must_be_disclosed(ctx):
    _, metrics = ctx
    value = metrics["home.xg_heuristic"].value
    assert not _check(ctx, Claim(f"Home's xG was {value}.", "home.xg_heuristic", value)).verified
    disclosed = Claim(f"Home's synthetic xG heuristic was {value}.", "home.xg_heuristic", value)
    assert _check(ctx, disclosed).verified


def test_event_only_claim(ctx):
    assert _check(ctx, Claim("A goal was scored.", cited_event_ids=(eid(14),))).verified

import pytest

from matchlens.claims import (
    HEURISTIC_NOTE,
    UNSUPPORTED,
    UNVERIFIED,
    VERIFIED,
    Claim,
    assess_prose,
    verify_claim,
)
from matchlens.metrics import compute_metrics

from .hand_match import eid, hand_match


@pytest.fixture(scope="module")
def ctx():
    m = hand_match()
    return m, compute_metrics(m, window_s=30)


def _check(ctx, claim):
    m, metrics = ctx
    return verify_claim(claim, metrics, m)


def test_supported_fact_claim_is_rendered_from_template(ctx):
    r = _check(ctx, Claim("home.goals", 1, (eid(14),)))
    assert r.status == VERIFIED and r.verified and r.reasons == ()
    assert r.text == (
        "Northbridge Rovers — Goals: 1; "
        "1' Northbridge Rovers, " + _scorer(ctx) + ": shot (goal) "
        "[evidence: metric home.goals, hand-00014]"
    )


def _scorer(ctx):
    m, _ = ctx
    return next(p.name for p in m.meta.home.players if p.player_id == "NBR-09")


def test_supported_ratio_claim(ctx):
    r = _check(ctx, Claim("home.pass_completion", 0.8))
    assert r.verified and "Pass completion rate: 80.0%" in r.text


def test_window_claim_mentions_window(ctx):
    r = _check(ctx, Claim("home.shots@30-60", 1))
    assert r.verified and "(between 0' and 1')" in r.text


def test_heuristic_is_always_disclosed_in_rendering(ctx):
    _, metrics = ctx
    value = metrics["home.xg_heuristic"].value
    r = _check(ctx, Claim("home.xg_heuristic", value))
    assert r.verified and HEURISTIC_NOTE in r.text


def test_wrong_value_is_unsupported_and_not_rendered(ctx):
    r = _check(ctx, Claim("home.goals", 2, (eid(14),)))
    assert r.status == UNSUPPORTED and r.text is None and "claimed 2" in r.reasons[0]


def test_unknown_metric(ctx):
    r = _check(ctx, Claim("home.distance_km", 120))
    assert not r.verified and "unknown metric" in r.reasons[0]


def test_no_evidence(ctx):
    r = _check(ctx, Claim())
    assert not r.verified and "no metric and no events" in r.reasons[0]


def test_value_without_metric(ctx):
    r = _check(ctx, Claim(value=3, cited_event_ids=(eid(14),)))
    assert not r.verified and "needs a metric" in r.reasons[0]


def test_nonexistent_event(ctx):
    r = _check(ctx, Claim("home.goals", 1, ("hand-99999",)))
    assert not r.verified and any("not in match" in x for x in r.reasons)


def test_event_not_a_source_of_metric(ctx):
    # Event 22 is a saved shot, not a goal.
    r = _check(ctx, Claim("home.goals", 1, (eid(22),)))
    assert not r.verified and any("not sources" in x for x in r.reasons)


def test_metric_without_value(ctx):
    assert not _check(ctx, Claim("home.shots")).verified


def test_undefined_metric_is_unsupported(ctx):
    m, _ = ctx
    stripped = m.__class__(
        meta=m.meta,
        events=tuple(e for e in m.events if not (e.type == "pass" and e.team == "away")),
    )
    r = verify_claim(Claim("away.pass_completion", 0.0), compute_metrics(stripped), stripped)
    assert not r.verified and "undefined" in r.reasons[0]


def test_event_only_claim(ctx):
    r = _check(ctx, Claim(cited_event_ids=(eid(14),)))
    assert r.verified and "shot (goal)" in r.text


# --- Limitation: the meaning of free text cannot be verified -------------------------


def test_claims_cannot_carry_free_text():
    """A claim has no text field, so false wording cannot ride on valid provenance."""
    with pytest.raises(TypeError):
        Claim(text="Away won 5-0.", metric_id="home.goals", value=1)  # type: ignore[call-arg]


def test_verified_text_comes_only_from_data(ctx):
    """Wording depends on the verified metric, never on caller-supplied prose."""
    a = _check(ctx, Claim("home.goals", 1, (eid(14),)))
    b = _check(ctx, Claim("home.goals", 1.0, (eid(14),)))
    assert a.text == b.text and "Away" not in a.text


@pytest.mark.parametrize(
    "prose",
    [
        "Eastvale Athletic won 5-0.",  # false
        "Northbridge Rovers scored once.",  # true, but still not machine-verified
        "Home's xG was 0.3 [evidence: metric home.xg_heuristic, hand-00014]",  # fake citation
    ],
)
def test_free_prose_is_never_verified(prose):
    r = assess_prose(prose)
    assert r.status == UNVERIFIED and r.status != VERIFIED and r.text == prose

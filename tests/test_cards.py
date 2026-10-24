import json
from dataclasses import replace

import pytest

from matchlens import cards as cards_module
from matchlens.cards import (
    CHAIN,
    DISPLAY_AFTER_S,
    WHAT,
    WHY,
    build_cards,
    format_card,
    future_sources,
)
from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.metrics import HEURISTIC, as_of_id, compute_metrics
from matchlens.moments import detect_moments, select_key_moments
from matchlens.narrative import AI_UNVERIFIED, ANALYST, CASUAL, OFFLINE_LABEL, TEMPLATE
from matchlens.schema import Match

from .conftest import DEMO_FIXTURE
from .hand_match import eid, hand_match


@pytest.fixture(scope="module")
def match():
    return hand_match()


def _name(match, player_id):
    team = match.meta.home if player_id.startswith("NBR") else match.meta.away
    return next(p.name for p in team.players if p.player_id == player_id)


def test_casual_cards_hand_checked(match):
    cards = build_cards(match, CASUAL).cards
    # Every goal, every missed big chance and the final whistle; press wins get no card.
    assert [c.kind for c in cards] == ["goal", "big_chance_missed", "full_time"]
    goal, chance, ft = cards
    scorer = _name(match, "NBR-09")
    assert goal.title == "Northbridge Rovers strike first"
    assert [(f.text, f.role) for f in goal.facts] == [
        (f"{scorer} scores in minute 1: 1-0.", WHAT),
        ("First goal of the match: Northbridge Rovers lead 1-0.", WHY),
        ("The move began with a tackle that won the ball back, then 1 pass.", WHAT),
    ]
    assert goal.facts[0].event_ids == goal.facts[1].event_ids == (eid(14),)
    assert goal.facts[2].event_ids == (eid(12), eid(13), eid(14))
    assert [(f.text, f.role) for f in chance.facts] == [
        (f"{_name(match, 'NBR-11')} had a great chance in minute 2, but the shot was saved.", WHAT),
        ("It stays 1-0; a goal would have doubled Northbridge Rovers' lead.", WHY),
    ]
    assert chance.facts[1].event_ids == (eid(22),)
    # Full time: shots 3-2 (10, 14, 22 vs 8, 19); xG 0.002 + 0.084 + 0.207 vs 0.001 + 0.001.
    assert ft.title == "Full time: Northbridge Rovers win 1-0"
    assert [f.text for f in ft.facts] == [
        "Northbridge Rovers win 1-0.",
        f"{scorer} scored the winner in minute 1.",
        "Northbridge Rovers had more shots (3–2) and the better chances "
        "(synthetic xG, a heuristic: 0.29 vs 0.00).",
    ]
    assert all(f.role == WHY for f in ft.facts) and ft.facts[2].kind == HEURISTIC
    assert ft.facts[0].event_ids == (eid(14), eid(24))
    assert ft.facts[2].metric_ids == tuple(
        f"{side}.{n}:asof:{eid(24)}" for side in ("home", "away") for n in ("shots", "xg_heuristic")
    )
    # (lead-in, anchor, publish, end): a card is published at its last evidence event.
    assert (goal.start_clock_s, goal.anchor_clock_s, goal.publish_clock_s, goal.end_clock_s) == (
        44.0,
        50.0,
        50.0,
        62.0,
    )
    # The missed chance (shot at 70 s) is known only once the keeper's recovery (72 s) is;
    # the full-time card appears at the whistle.
    assert (chance.anchor_clock_s, chance.publish_clock_s) == (70.0, 72.0)
    assert (ft.start_clock_s, ft.publish_clock_s, ft.end_clock_s) == (120.0, 120.0, 132.0)


def test_analyst_goal_card_hand_checked(match):
    goal = next(c for c in build_cards(match, ANALYST).cards if c.kind == "goal")
    assert goal.title == "Goal 1' — Northbridge Rovers (1-0)"
    texts = [f.text for f in goal.facts]
    assert (
        texts[0] == f"First goal of the match: Northbridge Rovers lead 1-0. [evidence: {eid(14)}]"
    )
    assert [f.role for f in goal.facts] == [WHY, CHAIN, CHAIN, CHAIN] + [WHAT] * 5
    # The evidence chain, one verified event fact per event, in match order.
    assert [f.event_ids for f in goal.facts[1:4]] == [(eid(12),), (eid(13),), (eid(14),)]
    assert texts[4] == (
        "Possession #5: 3 events, 1 pass(es) before the shot, started by tackle. "
        f"[evidence: {eid(12)}, {eid(13)}, {eid(14)}]"
    )
    assert texts[5].startswith("Shot quality 0.084 xG [synthetic heuristic")
    # Window metrics for the window containing the goal (the whole 120 s match here),
    # counted only up to the goal: the home shot at 70 s has not happened yet.
    assert [f.metric_ids for f in goal.facts[6:]] == [
        (f"home.shots@0-120:asof:{eid(14)}",),
        (f"home.xg_heuristic@0-120:asof:{eid(14)}",),
        (f"away.pressures@0-120:asof:{eid(14)}",),
    ]
    assert texts[6] == (
        "Northbridge Rovers — Shots: 2 (between 0' and 2', as of 1') "
        f"[evidence: metric home.shots@0-120:asof:{eid(14)}]"
    )
    assert texts[8] == (
        "Eastvale Athletic — Pressures applied: 1 (between 0' and 2', as of 1') "
        f"[evidence: metric away.pressures@0-120:asof:{eid(14)}]"
    )


def test_analyst_full_time_card_lists_verified_totals(match):
    ft = build_cards(match, ANALYST).cards[-1]
    assert ft.title == "Full time — Northbridge Rovers 1-0 Eastvale Athletic"
    assert ft.facts[0].text == f"Northbridge Rovers win 1-0. [evidence: {eid(14)}, {eid(24)}]"
    totals = [f for f in ft.facts if f.role == WHAT]
    assert [f.metric_ids[0].split(":")[0] for f in totals[:4]] == [
        "home.goals",
        "away.goals",
        "home.shots",
        "away.shots",
    ]
    assert totals[0].text == (
        f"Northbridge Rovers — Goals: 1 (at full time) [evidence: metric home.goals:asof:{eid(24)}]"
    )


def test_modes_differ_in_content(match):
    casual = build_cards(match, CASUAL).cards
    analyst = build_cards(match, ANALYST).cards
    casual_texts = {f.text for c in casual for f in c.facts}
    analyst_texts = {f.text for c in analyst for f in c.facts}
    assert casual_texts.isdisjoint(analyst_texts)
    assert all("[evidence:" not in t for t in casual_texts)
    assert all("[evidence:" in t for t in analyst_texts)
    assert any(f.metric_ids for c in analyst for f in c.facts)
    # Casual cards carry no statistics, except the labelled full-time chance comparison.
    stats = [f for c in casual for f in c.facts if f.metric_ids or "xG" in f.text]
    assert [(f.role, f.kind) for f in stats] == [(WHY, HEURISTIC)]
    assert "synthetic xG, a heuristic" in stats[0].text
    for c, a in zip(casual, analyst, strict=True):
        assert len(a.facts) > len(c.facts) and c.title != a.title


@pytest.mark.parametrize("mode", [CASUAL, ANALYST])
def test_every_fact_cites_existing_evidence(match, mode):
    event_ids = {e.event_id for e in match.events}
    card_set = build_cards(match, mode)
    metrics = {m.metric_id: m for m in card_set.metrics}
    for card in card_set.cards:
        for fact in card.facts:
            assert fact.event_ids or fact.metric_ids
            assert set(fact.event_ids) <= event_ids
            assert all(m in metrics for m in fact.metric_ids)
        assert card.narrative.status == TEMPLATE and card.narrative.label == OFFLINE_LABEL


def test_tampered_moment_is_suppressed(match):
    goal = next(m for m in detect_moments(match) if m.kind == "goal")
    forged = replace(goal, details={**goal.details, "score": (5, 0)})
    result = build_cards(match, CASUAL, moments=[forged, goal])
    assert [c.moment_id for c in result.cards] == [goal.moment_id]
    assert result.suppressed == ((forged.moment_id, "moment is not reproduced by the detectors"),)


class LyingProvider:
    name = "azure_openai"

    def render(self, request):
        return "Eastvale won 9-0 in a stunning comeback."


def test_llm_wording_never_changes_or_verifies_facts(match):
    offline = build_cards(match, CASUAL)
    ai = build_cards(match, CASUAL, provider=LyingProvider())
    for o, a in zip(offline.cards, ai.cards, strict=True):
        assert o.facts == a.facts
        assert a.narrative.status == AI_UNVERIFIED
        assert "number 9 is not in the verified facts" in a.narrative.warnings
    text = format_card(ai.cards[1])
    assert "not verified" in text and "! number 9" in text


def test_invalid_mode(match):
    with pytest.raises(ValueError):
        build_cards(match, "pundit")


@pytest.mark.parametrize("seed", [1, 42])
def test_generated_cards_are_deterministic_and_grounded(seed):
    m = generate_match(seed)
    for mode in (CASUAL, ANALYST):
        a, b = build_cards(m, mode), build_cards(m, mode)
        chosen = select_key_moments(detect_moments(m))
        assert a == b and [c.moment_id for c in a.cards] == [x.moment_id for x in chosen]
        assert a.suppressed == ()


def test_cli_cards_and_demo(capsys, monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    assert main(["cards", str(DEMO_FIXTURE), "--mode", "analyst", "--json", "--offline"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "analyst" and data["cards"]
    assert all(c["narrative"]["status"] == TEMPLATE for c in data["cards"])
    assert main(["demo", "--seed", "1", "--limit", "2"]) == 0
    out = capsys.readouterr().out
    assert "Narrative provider: offline_template" in out
    assert "[CASUAL]" in out and "[ANALYST]" in out and "Offline fallback" in out


def test_cli_reports_bad_azure_config_without_traceback(capsys, monkeypatch):
    for key, value in {
        "AZURE_OPENAI_ENDPOINT": "http://insecure.example",
        "AZURE_OPENAI_API_KEY": "secret-value-123",
        "AZURE_OPENAI_DEPLOYMENT": "d",
    }.items():
        monkeypatch.setenv(key, value)
    assert main(["demo", "--seed", "1", "--limit", "1"]) == 2
    err = capsys.readouterr().err
    assert "must be an https:// URL" in err and "secret-value-123" not in err
    assert main(["demo", "--seed", "1", "--limit", "1", "--offline"]) == 0


def _assert_causal(match, card_set):
    """Every card source has happened by publication; cited metrics match a replay cut there."""
    by_id = {e.event_id: e for e in match.events}
    metrics = {m.metric_id: m for m in card_set.metrics}
    published = [c.publish_clock_s for c in card_set.cards]
    assert published == sorted(published)  # the UI shows the last published card as active
    for card in card_set.cards:
        last = by_id[card.evidence_event_ids[-1]]
        assert card.publish_clock_s == last.clock_s >= card.anchor_clock_s >= card.start_clock_s
        assert card.end_clock_s == card.publish_clock_s + DISPLAY_AFTER_S
        args = (card.publish_clock_s, card.evidence_event_ids, card.facts, match, metrics)
        assert future_sources(*args) == ()
        # Independent oracle: the same metric computed on the match as replayed so far.
        so_far = compute_metrics(Match(match.meta, match.events[: last.seq]))
        for metric_id in {m for f in card.facts for m in f.metric_ids}:
            metric = metrics[metric_id]
            assert metric.as_of_event_id == last.event_id
            plain = so_far[metric_id.removesuffix(f":asof:{last.event_id}")]
            assert (metric.value, metric.source_event_ids) == (plain.value, plain.source_event_ids)


def test_seed_1_cards_do_not_look_ahead():
    """Regression: seed 1 cards counted a later shot and cited shots not yet taken."""
    m = generate_match(1)
    clock = {e.event_id: e.clock_s for e in m.events}
    analyst = build_cards(m, ANALYST)
    goal = next(c for c in analyst.cards if c.moment_id == "goal:ml1-01112")
    assert goal.publish_clock_s == goal.anchor_clock_s == 4213.6  # 70:13
    shots_id = "home.shots@3600-4500:asof:ml1-01112"
    shots = next(f for f in goal.facts if f.metric_ids == (shots_id,))
    assert shots.text.startswith("Northbridge Rovers — Shots: 3 (between 60' and 75', as of 71')")
    # The full 60'-75' window has 4 home shots; the 4th (ml1-01166, 74:05.8) comes later.
    assert compute_metrics(m)["home.shots@3600-4500"].value == 4
    assert clock["ml1-01166"] > goal.publish_clock_s
    cited = {x.metric_id: x for x in analyst.metrics}
    assert "ml1-01166" not in cited[shots_id].source_event_ids

    all_moments = detect_moments(m)
    for mode in (CASUAL, ANALYST):
        card_set = build_cards(m, mode, moments=all_moments)
        assert card_set.suppressed == ()
        by_moment = {c.moment_id: c for c in card_set.cards}
        # The press win's tackle is at 7:51.5, but its shot ml1-00130 only comes 14 s later.
        press = by_moment["press_win:ml1-00126"]
        assert (
            press.anchor_clock_s == 471.5 and press.publish_clock_s == clock["ml1-00130"] == 485.5
        )
        chance = by_moment["big_chance_missed:ml1-00591"]
        assert (chance.anchor_clock_s, chance.publish_clock_s) == (2267.3, clock["ml1-00592"])
        _assert_causal(m, card_set)


@pytest.mark.parametrize("seed", range(1, 21))
def test_cards_never_cite_the_future(seed):
    m = generate_match(seed)
    moments = detect_moments(m)
    for mode in (CASUAL, ANALYST):
        for card_set in (build_cards(m, mode), build_cards(m, mode, moments=moments)):
            assert card_set.suppressed == ()
            _assert_causal(m, card_set)


def test_future_sources_flags_early_publication():
    m = generate_match(1)
    card_set = build_cards(m, CASUAL, moments=detect_moments(m))
    press = next(c for c in card_set.cards if c.moment_id == "press_win:ml1-00126")
    # Shown at the tackle instead of after the shot, the card would cite the future.
    late = future_sources(press.anchor_clock_s, press.evidence_event_ids, press.facts, m, {})
    assert late == ("ml1-00130",)


def test_card_with_look_ahead_metrics_is_suppressed(monkeypatch):
    """The old full-window metrics, relabelled as as-of metrics, are caught by the guard."""

    def leaky(match, window_s, as_of_event_id):
        return {
            as_of_id(k, as_of_event_id): replace(
                v, metric_id=as_of_id(k, as_of_event_id), as_of_event_id=as_of_event_id
            )
            for k, v in compute_metrics(match, window_s).items()
        }

    monkeypatch.setattr(cards_module, "compute_metrics", leaky)
    result = build_cards(generate_match(1), ANALYST)
    reasons = dict(result.suppressed)
    assert "goal:ml1-01112" not in {c.moment_id for c in result.cards}
    assert reasons["goal:ml1-01112"].startswith("card cites events after it is published:")
    assert "ml1-01166" in reasons["goal:ml1-01112"]

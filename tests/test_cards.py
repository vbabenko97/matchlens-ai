import json
from dataclasses import replace

import pytest

from matchlens.cards import build_cards, format_card
from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.metrics import compute_metrics
from matchlens.moments import detect_moments
from matchlens.narrative import AI_UNVERIFIED, ANALYST, CASUAL, OFFLINE_LABEL, TEMPLATE

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
    assert [c.kind for c in cards] == ["press_win", "goal", "big_chance_missed"]
    press, goal, chance = cards
    assert goal.title == "GOAL! Northbridge Rovers score"
    assert [f.text for f in goal.facts] == [
        f"{_name(match, 'NBR-09')} scores for Northbridge Rovers in minute 1.",
        "That puts Northbridge Rovers ahead, 1-0.",
        "The move started with a tackle that won the ball back.",
    ]
    assert goal.facts[0].event_ids == (eid(14),)
    assert goal.facts[2].event_ids == (eid(12), eid(13), eid(14))
    assert [f.text for f in chance.facts] == [
        f"{_name(match, 'NBR-11')} had a great chance in minute 2, but the shot was saved.",
        "The score stays 1-0.",
    ]
    assert press.facts[0].text == (
        f"Eastvale Athletic pressed Northbridge Rovers and {_name(match, 'EVA-03')} "
        "won the ball in minute 1."
    )
    assert press.facts[1].event_ids == (eid(8),)
    assert (goal.start_clock_s, goal.anchor_clock_s, goal.end_clock_s) == (44.0, 50.0, 62.0)


def test_analyst_goal_card_hand_checked(match):
    goal = next(c for c in build_cards(match, ANALYST).cards if c.kind == "goal")
    assert goal.title == "Goal 1' — Northbridge Rovers (1-0)"
    texts = [f.text for f in goal.facts]
    # The evidence chain, one verified event fact per event, in match order.
    assert [f.event_ids for f in goal.facts[:3]] == [(eid(12),), (eid(13),), (eid(14),)]
    assert texts[3] == (
        "Possession #5: 3 events, 1 pass(es) before the shot, started by tackle. "
        f"[evidence: {eid(12)}, {eid(13)}, {eid(14)}]"
    )
    assert texts[4].startswith("Shot quality 0.084 xG [synthetic heuristic")
    # Window metrics for the window containing the goal (the whole 120 s match here).
    assert [f.metric_ids for f in goal.facts[5:]] == [
        ("home.shots@0-120",),
        ("home.xg_heuristic@0-120",),
        ("away.pressures@0-120",),
    ]
    assert texts[5] == (
        "Northbridge Rovers — Shots: 3 (between 0' and 2') [evidence: metric home.shots@0-120]"
    )
    assert texts[7] == (
        "Eastvale Athletic — Pressures applied: 1 (between 0' and 2') "
        "[evidence: metric away.pressures@0-120]"
    )


def test_modes_differ_in_content(match):
    casual = build_cards(match, CASUAL).cards
    analyst = build_cards(match, ANALYST).cards
    casual_texts = {f.text for c in casual for f in c.facts}
    analyst_texts = {f.text for c in analyst for f in c.facts}
    assert casual_texts.isdisjoint(analyst_texts)
    assert all("[evidence:" not in t and "xG" not in t for t in casual_texts)
    assert all("[evidence:" in t for t in analyst_texts)
    assert any(f.metric_ids for c in analyst for f in c.facts)
    assert not any(f.metric_ids for c in casual for f in c.facts)
    for c, a in zip(casual, analyst, strict=True):
        assert len(a.facts) > len(c.facts) and c.title != a.title


@pytest.mark.parametrize("mode", [CASUAL, ANALYST])
def test_every_fact_cites_existing_evidence(match, mode):
    event_ids = {e.event_id for e in match.events}
    metrics = compute_metrics(match)
    for card in build_cards(match, mode).cards:
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
        assert a == b and 1 <= len(a.cards) <= 4 and a.suppressed == ()


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

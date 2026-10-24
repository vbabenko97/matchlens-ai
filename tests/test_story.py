"""Match-story lines: hand-checked text, causality, and rejection of unsupported lines."""

from dataclasses import replace

import pytest

from matchlens import cards as cards_module
from matchlens.cards import WHY, build_cards
from matchlens.generator import generate_match
from matchlens.narrative import ANALYST, CASUAL
from matchlens.schema import Match
from matchlens.story import (
    SITUATIONS,
    StoryLine,
    chance_story,
    check_line,
    full_time_story,
    goal_story,
    render,
)


@pytest.fixture(scope="module")
def match():
    return generate_match(1)  # the public demo: 0-1 (5'), 1-1 (56'), 2-1 (71'), 2-2, 2-3 (81')


def _texts(lines):
    return [(ln.kind, ln.text, ln.event_ids) for ln in lines]


def test_seed_1_goal_stories_hand_checked(match):
    assert _texts(goal_story(match, "ml1-00089")) == [
        ("opener", "First goal of the match: Eastvale Athletic lead 0-1.", ("ml1-00089",)),
    ]
    assert _texts(goal_story(match, "ml1-00871")) == [
        (
            "equaliser",
            "Northbridge Rovers level it at 1-1, 51 minutes after going behind.",
            ("ml1-00089", "ml1-00871"),
        ),
    ]
    assert _texts(goal_story(match, "ml1-01112")) == [
        (
            "comeback",
            "Northbridge Rovers were 0-1 down in minute 5. "
            "Goals in minutes 56 and 71 turned it around.",
            ("ml1-00089", "ml1-00871", "ml1-01112"),
        ),
    ]
    assert _texts(goal_story(match, "ml1-01206"))[0][1] == (
        "Eastvale Athletic level it at 2-2, 6 minutes after going behind."
    )
    assert _texts(goal_story(match, "ml1-01253")) == [
        (
            "comeback",
            "Eastvale Athletic were 2-1 down in minute 71. "
            "Goals in minutes 77 and 81 turned it around.",
            ("ml1-01112", "ml1-01206", "ml1-01253"),
        ),
        (
            "redemption",  # Ugo Vale's 0.61 xG shot was saved at 38'
            "Ugo Vale saw a big chance saved in minute 38; this time it went in.",
            ("ml1-00591", "ml1-01253"),
        ),
    ]


def test_seed_1_chance_and_full_time_hand_checked(match):
    assert _texts(chance_story(match, "ml1-00591")) == [
        (
            "stakes",
            "It stays 0-1; a goal would have doubled Eastvale Athletic's lead.",
            ("ml1-00591",),
        )
    ]
    goals = ("ml1-00089", "ml1-00871", "ml1-01112", "ml1-01206", "ml1-01253")
    assert _texts(full_time_story(match, "ml1-01399")) == [
        (
            "ft_comeback",
            "Eastvale Athletic were 2-1 down in minute 71 and won 2-3.",
            ("ml1-01112", "ml1-01206", "ml1-01253", "ml1-01399"),
        ),
        (
            "winner",  # Eastvale's 3rd goal took them past Northbridge's final 2
            "Ugo Vale scored the winner in minute 81, "
            "after seeing a big chance saved in minute 38.",
            ("ml1-00591", "ml1-01253", "ml1-01399"),
        ),
        ("lead_changes", "The lead changed hands twice.", (*goals, "ml1-01399")),
    ]


@pytest.mark.parametrize("seed", range(1, 21))
def test_stories_never_use_later_events_and_all_check(seed):
    """A goal's story is the same on the match cut right after the goal, and every line is
    accepted by the checker from its own cited events."""
    m = generate_match(seed)
    end = m.events[-1].event_id
    for e in m.events:
        if e.type == "shot" and e.outcome == "goal":
            lines = goal_story(m, e.event_id)
            assert lines == goal_story(Match(m.meta, m.events[: e.seq]), e.event_id)
            assert sum(ln.kind in SITUATIONS for ln in lines) == 1 and lines[0].kind in SITUATIONS
        elif e.type == "shot" and (e.xg_heuristic or 0) >= 0.2:
            lines = chance_story(m, e.event_id)
        else:
            continue
        for ln in lines:
            assert check_line(ln, m, e.event_id) == [], (ln, check_line(ln, m, e.event_id))
    ft = full_time_story(m, end)
    assert ft[0].kind in ("ft_comeback", "ft_result")
    assert all(check_line(ln, m, end) == [] for ln in ft)


@pytest.mark.parametrize("seed", range(1, 21))
def test_every_goal_card_says_why_it_matters(seed):
    m = generate_match(seed)
    for mode in (CASUAL, ANALYST):
        card_set = build_cards(m, mode)
        assert card_set.suppressed == ()
        goals = [c for c in card_set.cards if c.kind == "goal"]
        assert len(goals) == sum(m.final_score)
        for card in card_set.cards:
            assert any(f.role == WHY for f in card.facts), card.card_id


def _forge(match, kind, team, ids):
    """A line whose text is honestly rendered from ``ids``: only its condition can fail."""
    by_id = {e.event_id: e for e in match.events}
    return StoryLine(kind, team, ids, render(kind, team, [by_id[i] for i in ids], match))


def _line(match, goal_id, kind=None):
    lines = goal_story(match, goal_id)
    return next(ln for ln in lines if kind is None or ln.kind == kind)


def test_checker_rejects_wrong_text(match):
    line = _line(match, "ml1-01253", "comeback")
    forged = replace(line, text=line.text.replace("2-1 down", "3-1 down"))
    assert check_line(forged, match, "ml1-01253") == ["text does not match its cited events"]


def test_checker_rejects_a_missing_goal_in_between(match):
    forged = _forge(match, "comeback", "away", ("ml1-01112", "ml1-01253"))  # 77' left out
    assert check_line(forged, match, "ml1-01253") == ["cited events do not show a comeback"]


def test_checker_rejects_the_wrong_kind(match):
    line = _line(match, "ml1-01206", "equaliser")
    forged = _forge(match, "extends_lead", "away", line.event_ids)
    assert "cited events do not show a extends_lead" in check_line(forged, match, "ml1-01206")


def test_checker_rejects_future_and_unknown_events(match):
    line = _line(match, "ml1-01253", "comeback")
    assert check_line(line, match, "ml1-01206") == ["cites events after ml1-01206"]
    ghost = replace(line, event_ids=(*line.event_ids[:-1], "ml1-99999"))
    assert check_line(ghost, match, "ml1-01253") == ["cited events not in match: ['ml1-99999']"]
    assert check_line(replace(line, kind="pundit_take"), match, "ml1-01253") == [
        "pundit_take cannot be rendered from its cited events"
    ]


def test_checker_rejects_redemption_without_a_big_chance(match):
    # ml1-00130 is an Eastvale shot of 0.057 xG: no big chance, and by another player.
    forged = _forge(match, "redemption", "away", ("ml1-00130", "ml1-01253"))
    assert check_line(forged, match, "ml1-01253") == ["cited events do not show a redemption"]


def test_unsupported_story_line_is_dropped_and_reported(match, monkeypatch):
    real = cards_module.goal_story

    def forged(m, goal_id):
        lines = real(m, goal_id)
        return (replace(lines[0], text=lines[0].text + " Unstoppable."), *lines[1:])

    monkeypatch.setattr(cards_module, "goal_story", forged)
    card_set = build_cards(match, CASUAL)
    goal = next(c for c in card_set.cards if c.moment_id == "goal:ml1-00089")
    assert not any(f.role == WHY for f in goal.facts)
    assert goal.title == "GOAL! Eastvale Athletic score"  # no situation title without its line
    assert (
        "goal:ml1-00089",
        "story line opener dropped: text does not match its cited events",
    ) in (card_set.suppressed)

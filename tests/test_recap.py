"""Match recap: lines of the verified casual cards, causal, complete, and copyable."""

import json
from pathlib import Path

import pytest

from matchlens.cards import WHY, build_cards
from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.io import dumps_match
from matchlens.narrative import ANALYST, CASUAL
from matchlens.personalize import (
    PUBLISHED_PERSONALIZE_REVIEW,
    PUBLISHED_PERSONALIZE_TRACE,
    RecordedPersonalizer,
)
from matchlens.recap import RECAP_SCHEMA, TITLE_INDEX, build_recap, format_recap
from matchlens.replay import build_bundle

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def match():
    return generate_match(1)


@pytest.fixture(scope="module")
def cards(match):
    return build_cards(match, CASUAL).cards


def _goals(match):
    return [e.event_id for e in match.events if e.type == "shot" and e.outcome == "goal"]


def test_recap_is_the_full_time_story_and_the_scorers(match, cards):
    recap = build_recap(match, cards)
    end = next(c for c in cards if c.kind == "full_time")
    assert recap["schema"] == RECAP_SCHEMA and (recap["match_id"], recap["seed"]) == ("ml1", 1)
    assert recap["publish_clock_s"] == end.publish_clock_s == 5400
    assert recap["headline"]["text"] == end.title
    assert recap["headline"]["text"] == "Full time: Eastvale Athletic come from behind to win 2-3"
    assert [ln["text"] for ln in recap["summary"]] == [f.text for f in end.facts if f.role == WHY]
    assert [ln["event_ids"][0] for ln in recap["goals"]] == _goals(match)
    assert recap["goals"][-1]["text"] == "Ugo Vale scores in minute 81: 2-3."


def test_every_recap_line_is_a_card_line_with_its_ids(match, cards):
    recap = build_recap(match, cards)
    by_id = {c.card_id: c for c in cards}
    for ln in [recap["headline"], *recap["summary"], *recap["goals"]]:
        card = by_id[ln["card_id"]]
        assert card.publish_clock_s <= recap["publish_clock_s"]  # nothing from later
        if ln["index"] == TITLE_INDEX:
            assert ln["text"] == card.title
            continue
        f = card.facts[ln["index"]]
        assert (ln["text"], ln["kind"]) == (f.text, f.kind)
        assert (ln["event_ids"], ln["metric_ids"]) == (list(f.event_ids), list(f.metric_ids))
        assert ln["event_ids"] or ln["metric_ids"]  # every line cites its sources


@pytest.mark.parametrize("seed", range(100, 130))
def test_goal_list_is_complete_on_untuned_seeds(seed):
    match = generate_match(seed)
    recap = build_recap(match, build_cards(match, CASUAL).cards)
    assert [ln["event_ids"][0] for ln in recap["goals"]] == _goals(match)
    assert recap["summary"]  # the full-time card always says how it ended


def test_no_recap_before_full_time_and_only_from_casual_cards(match, cards):
    assert build_recap(match, [c for c in cards if c.kind != "full_time"]) is None
    assert build_recap(match, cards[:1]) is None
    with pytest.raises(ValueError):
        build_recap(match, build_cards(match, ANALYST).cards)


def test_a_missing_goal_card_empties_the_goal_list(match, cards):
    first_goal = next(c for c in cards if c.kind == "goal")
    recap = build_recap(match, [c for c in cards if c is not first_goal])
    assert recap["goals"] == [] and recap["summary"]


def test_format_recap_is_plain_text_with_provenance(match, cards):
    text = format_recap(build_recap(match, cards))
    blocks = text.rstrip("\n").split("\n\n")
    assert blocks[0] == "Full time: Eastvale Athletic come from behind to win 2-3"
    assert blocks[1].startswith("Eastvale Athletic were 2-1 down in minute 71 and won 2-3. ")
    assert blocks[2].splitlines()[:2] == ["Goals", "- Rafe Pembrook scores in minute 5: 0-1."]
    assert blocks[3].startswith("Synthetic match ml1 (seed 1) between fictional teams.")
    assert "evidence" not in text and "ml1-0" not in text  # no IDs in the copy text


def test_bundle_carries_the_recap_and_every_variant_covers_it(match):
    personalizer = RecordedPersonalizer(
        json.loads((ROOT / PUBLISHED_PERSONALIZE_TRACE).read_text(encoding="utf-8")),
        PUBLISHED_PERSONALIZE_TRACE,
        json.loads((ROOT / PUBLISHED_PERSONALIZE_REVIEW).read_text(encoding="utf-8")),
    )
    bundle = build_bundle(match, personalizer=personalizer)
    recap = bundle["recap"]
    assert recap == build_recap(match, build_cards(match, CASUAL).cards)
    lines = [recap["headline"], *recap["summary"], *recap["goals"]]
    for variant in bundle["personalized"]["variants"].values():
        for ln in lines:  # the UI never has to fall back silently for a recap line
            rec = variant["cards"][ln["card_id"]]
            (shown,) = [x for x in rec["lines"] if x["index"] == ln["index"]]
            assert shown["source"] == ln["text"]


def test_cli_recap_prints_text_and_json(match, tmp_path, capsys):
    path = tmp_path / "m.jsonl"
    path.write_text(dumps_match(match), encoding="utf-8")
    assert main(["recap", str(path)]) == 0
    assert capsys.readouterr().out == format_recap(
        build_recap(match, build_cards(match, CASUAL).cards)
    )
    assert main(["recap", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema"] == RECAP_SCHEMA

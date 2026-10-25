"""Event-by-event processing: the same cards as the batch build, published on arrival of their
last evidence event, never earlier; a broken feed stops publishing."""

import io
import json
from dataclasses import asdict, replace

import pytest

from matchlens.cards import build_cards
from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.io import MatchFormatError, dumps_match, iter_records
from matchlens.narrative import ANALYST, CASUAL, MODES
from matchlens.replay import build_bundle
from matchlens.schema import Match
from matchlens.stream import STREAM_CHECK_SCHEMA, StreamProcessor, run_stream, stream_check
from matchlens.validate import EventValidator

BIG_CHANCE = "ml1-00591"  # 38' shot saved; ml1-00592 answers it
FIRST_GOAL = "ml1-00089"  # 5', 0-1


@pytest.fixture(scope="module")
def match():
    return generate_match(1)


def _upto(match: Match, event_id: str) -> list:
    i = next(i for i, e in enumerate(match.events) if e.event_id == event_id)
    return list(match.events[: i + 1])


@pytest.mark.parametrize("seed", [1, *range(100, 130)])
def test_stream_gives_the_batch_cards_at_their_last_evidence_event(seed):
    match = generate_match(seed)
    record = stream_check(match, [build_cards(match, mode) for mode in MODES])
    assert record["schema"] == STREAM_CHECK_SCHEMA and record["events"] == len(match.events)
    assert record["identical_to_batch"] == record["published_at_last_evidence"] == record["cards"]


def test_each_card_is_published_on_arrival_and_never_changes(match):
    processor = StreamProcessor(match.meta)
    batch = {c.card_id: c for mode in MODES for c in build_cards(match, mode).cards}
    for event in match.events:
        update = processor.push(event)
        for p in update.published:
            # Published as this event arrived: the processor had seen nothing later.
            assert processor.events_received == p.seq == event.seq == p.evidence_seq
            assert asdict(p.card) == asdict(batch[p.card.card_id])
    assert processor.close() == ()
    ids = [p.card.card_id for p in processor.publications]
    assert len(ids) == len(set(ids)) == len(batch)
    assert processor.issues == processor.end_issues == []


def test_a_big_chance_waits_for_the_event_that_answers_it(match):
    processor = StreamProcessor(match.meta, modes=(CASUAL,))
    events = _upto(match, BIG_CHANCE)
    updates = [processor.push(e) for e in events]
    assert not any(p.card.kind == "big_chance_missed" for p in processor.publications)
    answer = match.events[len(events)]
    assert answer.related_event_id == BIG_CHANCE
    (pub,) = processor.push(answer).published
    assert pub.card.kind == "big_chance_missed" and pub.seq == pub.evidence_seq == answer.seq
    assert pub.card.evidence_event_ids == (BIG_CHANCE, answer.event_id)
    assert updates[-1].score == (0, 1)


def test_end_of_stream_completes_a_waiting_moment_with_what_arrived(match):
    processor = StreamProcessor(match.meta, modes=(CASUAL,))
    for e in _upto(match, BIG_CHANCE):
        processor.push(e)
    (pub,) = processor.close()
    assert pub.card.kind == "big_chance_missed"
    assert pub.card.evidence_event_ids == (BIG_CHANCE,)  # the answer never arrived
    assert any("closed periods" in issue for issue in processor.end_issues)
    with pytest.raises(RuntimeError):
        processor.push(match.events[0])


def test_an_invalid_event_stops_publishing(match):
    events = list(match.events)
    events[49] = replace(events[49], clock_s=events[49].clock_s - 100)  # clock goes back
    processor = StreamProcessor(match.meta)
    updates = [processor.push(e) for e in events]
    assert any("clock went backwards" in issue for issue in updates[49].issues)
    assert processor.halted and processor.publications == []  # not even the 5' goal
    assert processor.close() == ()


def test_updates_carry_the_running_state(match):
    processor = StreamProcessor(match.meta)
    last = [processor.push(e) for e in _upto(match, FIRST_GOAL)][-1]
    assert last.score == (0, 1) and last.event.event_id == FIRST_GOAL
    shots = [e for e in _upto(match, FIRST_GOAL) if e.type == "shot"]
    assert sum(last.shots) == len(shots) and last.xg[1] >= shots[-1].xg_heuristic - 1e-9
    assert {p.card.mode for p in last.published} == {CASUAL, ANALYST}


def test_stream_check_fails_on_any_difference(match):
    card_sets = [build_cards(match, mode) for mode in MODES]
    casual = card_sets[0]
    missing = replace(casual, cards=casual.cards[1:])
    with pytest.raises(ValueError, match="different cards"):
        stream_check(match, [missing, card_sets[1]])
    changed = replace(casual, cards=(replace(casual.cards[0], title="Other"), *casual.cards[1:]))
    with pytest.raises(ValueError, match="content differs"):
        stream_check(match, [changed, card_sets[1]])


def test_bundle_records_the_stream_check(match):
    bundle = build_bundle(match)
    cards = sum(len(v) for v in bundle["cards"].values())
    assert bundle["stream"]["cards"] == bundle["stream"]["identical_to_batch"] == cards
    assert "stream" not in build_bundle(match, limit=2)  # a partial card set has nothing to match


def test_event_validator_checks_a_prefix_without_the_end(match):
    validator = EventValidator(match.meta)
    assert all(validator.check(e) == [] for e in match.events[:100])
    assert validator.finish() == ["match must contain closed periods [1, 2], got []"]
    assert run_stream(match).end_issues == []


def test_iter_records_reads_one_line_at_a_time(match):
    lines = dumps_match(match).splitlines(keepends=True)
    records = iter_records(iter(lines[:3]))
    assert next(records).match_id == match.meta.match_id
    assert next(records).event_id == match.events[0].event_id
    with pytest.raises(MatchFormatError, match="first record must be the match record"):
        list(iter_records(iter(lines[1:2])))
    with pytest.raises(MatchFormatError, match="invalid JSON"):
        list(iter_records(iter([lines[0], "{oops\n"])))


def test_cli_feed_writes_the_match_line_by_line(match, capsys):
    assert main(["feed", "--seed", "1"]) == 0
    assert capsys.readouterr().out == dumps_match(match)


def test_cli_stream_reads_standard_input_and_publishes_cards(match, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(dumps_match(match)))
    assert main(["stream", "-"]) == 0
    out = capsys.readouterr().out
    assert "04:41  event 89 arrived: shot (goal), 0-1" in out
    assert "  ▸ casual card published: Eastvale Athletic strike first" in out
    assert "event 592 arrived: recovery (won)" in out  # the big chance waited for its answer
    assert out.rstrip().endswith(
        "1399 events, 14 cards published; each card waited at most 0 event(s) after its last "
        "evidence event; 0 issue(s)."
    )


def test_cli_stream_json_and_a_broken_feed(match, tmp_path, capsys):
    path = tmp_path / "m.jsonl"
    path.write_text(dumps_match(match), encoding="utf-8")
    assert main(["stream", str(path), "--json", "--mode", "casual"]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    cards = [r for r in records if r["kind"] == "card"]
    assert len(cards) == 7 and all(r["card"]["mode"] == "casual" for r in cards)
    assert all(r["seq"] == r["evidence_seq"] for r in cards)
    assert records[-1] == {
        "kind": "summary",
        "events": 1399,
        "cards": 7,
        "max_wait_events": 0,
        "issues": [],
    }
    events = list(match.events)
    events[49] = replace(events[49], clock_s=events[49].clock_s - 100)
    path.write_text(dumps_match(replace(match, events=tuple(events))), encoding="utf-8")
    assert main(["stream", str(path)]) == 1
    out = capsys.readouterr().out
    assert "clock went backwards" in out and ": publishing stopped" in out
    assert "card published" not in out

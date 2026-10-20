import json

import pytest

from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.io import MatchFormatError, dumps_match, match_to_lines, parse_lines, read_match
from matchlens.validate import validate_match

from .conftest import DEMO_FIXTURE, DEMO_PERIOD_S, DEMO_SEED


def test_roundtrip_preserves_match():
    m = generate_match(9, period_length_s=300)
    assert parse_lines(dumps_match(m).splitlines()) == m


def test_checked_in_fixture_matches_generator():
    """Golden test: the demo fixture is reproducible byte-for-byte from its seed."""
    regenerated = dumps_match(generate_match(DEMO_SEED, period_length_s=DEMO_PERIOD_S))
    assert DEMO_FIXTURE.read_text(encoding="utf-8") == regenerated
    assert validate_match(read_match(DEMO_FIXTURE)) == []


@pytest.fixture
def lines() -> list[str]:
    return match_to_lines(generate_match(4, period_length_s=60))


def _errors(lines: list[str]) -> list[str]:
    with pytest.raises(MatchFormatError) as exc:
        parse_lines(lines)
    return exc.value.errors


def _edit(lines: list[str], idx: int, **changes) -> list[str]:
    rec = json.loads(lines[idx])
    for key, value in changes.items():
        if value is ...:
            del rec[key]
        else:
            rec[key] = value
    return [*lines[:idx], json.dumps(rec), *lines[idx + 1 :]]


def test_invalid_json(lines):
    assert any("invalid JSON" in e for e in _errors([*lines[:3], "{not json", *lines[3:]]))


def test_missing_field(lines):
    assert any("missing field 'clock_s'" in e for e in _errors(_edit(lines, 2, clock_s=...)))


def test_wrong_type(lines):
    assert any("'seq' has wrong type" in e for e in _errors(_edit(lines, 2, seq="2")))
    assert any("'period' has wrong type" in e for e in _errors(_edit(lines, 2, period=True)))


def test_bad_point(lines):
    assert any("[x, y]" in e for e in _errors(_edit(lines, 3, start=[1.0])))


def test_unknown_field_and_record(lines):
    assert any("unknown field" in e for e in _errors(_edit(lines, 2, mood="happy")))
    assert any("unknown record kind" in e for e in _errors(_edit(lines, 2, record="frame")))


def test_missing_match_record(lines):
    assert any("match record" in e for e in _errors(lines[1:]))


def test_cli_generate_and_validate(tmp_path, capsys):
    out = tmp_path / "m.jsonl"
    assert main(["generate", "--seed", "3", "--period-length", "120", "--out", str(out)]) == 0
    assert main(["validate", str(out)]) == 0
    assert "OK:" in capsys.readouterr().out
    out.write_text(out.read_text().replace('"seq":5,', '"seq":50,'), encoding="utf-8")
    assert main(["validate", str(out)]) == 1


def test_cli_metrics(capsys):
    assert main(["metrics", str(DEMO_FIXTURE), "--window", "300"]) == 0
    records = {r["metric_id"]: r for r in json.loads(capsys.readouterr().out)}
    goals = (records["home.goals"]["value"], records["away.goals"]["value"])
    assert goals == read_match(DEMO_FIXTURE).final_score
    assert records["home.xg_heuristic"]["kind"] == "heuristic"
    assert "home.shots@300-600" in records


def test_cli_moments(capsys):
    assert main(["moments", str(DEMO_FIXTURE), "--limit", "3"]) == 0
    records = json.loads(capsys.readouterr().out)
    assert len(records) <= 3
    assert all(r["event_ids"] and r["text"].endswith("]") for r in records)

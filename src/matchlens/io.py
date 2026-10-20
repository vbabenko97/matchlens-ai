"""JSONL serialisation of matches.

Line 1 is a ``{"record": "match", ...}`` metadata record; every following line is a
``{"record": "event", ...}`` record. Output is byte-for-byte deterministic for a given
``Match`` (fixed key order, no wall-clock timestamps).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .schema import Event, Match, MatchMeta, Player, Team


class MatchFormatError(ValueError):
    """Raised when JSONL input is malformed. ``errors`` lists every problem found."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        shown = "; ".join(errors[:5])
        more = f" (+{len(errors) - 5} more)" if len(errors) > 5 else ""
        super().__init__(f"{len(errors)} format error(s): {shown}{more}")


def _dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def match_to_lines(match: Match) -> list[str]:
    return [_dumps(match.meta.to_record()), *(_dumps(e.to_record()) for e in match.events)]


def dumps_match(match: Match) -> str:
    return "\n".join(match_to_lines(match)) + "\n"


def write_match(match: Match, path: str | Path) -> None:
    Path(path).write_text(dumps_match(match), encoding="utf-8", newline="\n")


_EVENT_FIELDS: dict[str, tuple[type | tuple[type, ...], bool]] = {
    # name: (accepted JSON types, nullable)
    "event_id": (str, False),
    "seq": (int, False),
    "period": (int, False),
    "clock_s": ((int, float), False),
    "type": (str, False),
    "team": (str, True),
    "player_id": (str, True),
    "possession_id": (int, True),
    "outcome": (str, True),
    "home_score": (int, False),
    "away_score": (int, False),
    "start": (list, True),
    "end": (list, True),
    "subtype": (str, True),
    "xg_heuristic": ((int, float), True),
    "related_event_id": (str, True),
}


def _parse_point(
    value: Any, where: str, name: str, errors: list[str]
) -> tuple[float, float] | None:
    if value is None:
        return None
    if len(value) != 2 or not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
    ):
        errors.append(f"{where}: '{name}' must be [x, y] numbers")
        return None
    return (float(value[0]), float(value[1]))


def _parse_event(rec: dict[str, Any], where: str, errors: list[str]) -> Event | None:
    n_before = len(errors)
    unknown = set(rec) - set(_EVENT_FIELDS) - {"record"}
    if unknown:
        errors.append(f"{where}: unknown field(s) {sorted(unknown)}")
    for name, (types, nullable) in _EVENT_FIELDS.items():
        if name not in rec:
            errors.append(f"{where}: missing field '{name}'")
            continue
        value = rec[name]
        if value is None:
            if not nullable:
                errors.append(f"{where}: '{name}' must not be null")
        elif isinstance(value, bool) or not isinstance(value, types):
            errors.append(f"{where}: '{name}' has wrong type {type(value).__name__}")
    if len(errors) > n_before:
        return None
    start = _parse_point(rec["start"], where, "start", errors)
    end = _parse_point(rec["end"], where, "end", errors)
    if len(errors) > n_before:
        return None
    xg = rec["xg_heuristic"]
    return Event(
        event_id=rec["event_id"],
        seq=rec["seq"],
        period=rec["period"],
        clock_s=float(rec["clock_s"]),
        type=rec["type"],
        team=rec["team"],
        player_id=rec["player_id"],
        possession_id=rec["possession_id"],
        outcome=rec["outcome"],
        home_score=rec["home_score"],
        away_score=rec["away_score"],
        start=start,
        end=end,
        subtype=rec["subtype"],
        xg_heuristic=float(xg) if xg is not None else None,
        related_event_id=rec["related_event_id"],
    )


def _parse_team(rec: Any) -> Team:
    return Team(
        side=rec["side"],
        team_id=rec["team_id"],
        name=rec["name"],
        players=tuple(Player(**p) for p in rec["players"]),
    )


def _parse_meta(rec: dict[str, Any]) -> MatchMeta:
    return MatchMeta(
        match_id=rec["match_id"],
        seed=rec["seed"],
        period_length_s=rec["period_length_s"],
        home=_parse_team(rec["home"]),
        away=_parse_team(rec["away"]),
        schema_version=rec["schema_version"],
        generator=rec["generator"],
    )


def parse_lines(lines: Iterable[str]) -> Match:
    """Parse JSONL lines into a ``Match``; raise ``MatchFormatError`` on any malformed record.

    This checks record shape and types only. Semantic invariants live in ``validate``.
    """
    errors: list[str] = []
    meta: MatchMeta | None = None
    events: list[Event] = []
    for lineno, raw in enumerate(lines, start=1):
        where = f"line {lineno}"
        if not raw.strip():
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"{where}: invalid JSON ({exc.msg})")
            continue
        if not isinstance(rec, dict):
            errors.append(f"{where}: record must be a JSON object")
            continue
        kind = rec.get("record")
        if kind == "match":
            if meta is not None or events:
                errors.append(f"{where}: match record must appear exactly once, first")
                continue
            try:
                meta = _parse_meta(rec)
            except (KeyError, TypeError) as exc:
                errors.append(f"{where}: malformed match record ({exc!r})")
        elif kind == "event":
            if meta is None and lineno == 1:
                errors.append(f"{where}: first record must be the match record")
            event = _parse_event(rec, where, errors)
            if event is not None:
                events.append(event)
        else:
            errors.append(f"{where}: unknown record kind {kind!r}")
    if meta is None and not any("match record" in e for e in errors):
        errors.append("missing match record")
    if errors:
        raise MatchFormatError(errors)
    assert meta is not None
    return Match(meta=meta, events=tuple(events))


def read_match(path: str | Path) -> Match:
    with Path(path).open(encoding="utf-8") as fh:
        return parse_lines(fh)

"""Pass quality and tempo: per-pass features and running team totals.

Facts (computed from the simulated coordinates, outcomes and timestamps):
- ``length_m``: distance from the pass's start to its end (where it was received, cut out or
  went out), in metres.
- ``band``: short (< 15 m), medium (15-30 m) or long (>= 30 m).
- ``progress_m``: how much closer to the centre of the opponent's goal the ball ended up.
  A pass is ``progressive`` when it is open play and gains at least ``PROGRESSIVE_M``.
- ``pressed``: the event right before the pass is a pressure by the opponent, in the same
  possession, at most ``PRESSED_WINDOW_S`` earlier.
- ``tempo_s``: seconds since the team's previous ball event in the same possession.

Heuristic (synthetic, uncalibrated):
- ``xpass``: expected completion of an open-play pass from its length, whether it was pressed
  and how close to a touchline it started. It is built from the generator's own rules, so on
  this synthetic data it matches the outcomes by construction (tested over seeds 100-129);
  it says nothing about real football. The generator gives players no individual passing
  skill, so "completed minus expected" is luck here, not quality.

Ball speed is not modelled: the generator draws the time between events at random,
independent of distance, so dividing a length by a time would invent a number. Tempo is the
only time-based figure, and it describes the simulation's clock, not player behaviour.

Only open-play passes count here; restarts (kick-offs, goal kicks, throw-ins) are excluded.
Totals are running steps ``[seq, values]``, one per open-play pass, so the replay shows them as
they stood at any moment. No AI is involved.
"""

from __future__ import annotations

import math
from typing import Any

from .metrics import FACT, HEURISTIC
from .schema import BALL_EVENTS, HOME, PITCH_LENGTH, PITCH_WIDTH, SIDES, Event, Match

PASSING_SCHEMA = "matchlens.passing/v1"
SHORT_M = 15.0
LONG_M = 30.0
PROGRESSIVE_M = 10.0
PRESSED_WINDOW_S = 2.0
XPASS_BASE, XPASS_PER_M, XPASS_PRESSED = 0.9, 0.006, 0.15
XPASS_FLOOR, XPASS_CEIL = 0.05, 0.98
XPASS_LATERAL_SD_M = 12.0  # spread of a pass across the pitch; near a touchline it can go out

# name -> (label, kind, unit, definition)
TEAM_STATS: dict[str, tuple[str, str, str, str]] = {
    "attempted": ("Open-play passes", FACT, "count", "Open-play passes attempted."),
    "completed": ("Completed", FACT, "count", "Open-play passes with outcome 'complete'."),
    "completion": ("Pass accuracy", FACT, "ratio", "Completed / attempted open-play passes."),
    "avg_length_m": (
        "Average length",
        FACT,
        "m",
        "Mean start-to-end distance of open-play passes.",
    ),
    "long_attempted": (
        "Long passes",
        FACT,
        "count",
        f"Open-play passes of {LONG_M:.0f} m or more.",
    ),
    "long_completed": ("Long completed", FACT, "count", "Long passes completed."),
    "progressive_attempted": (
        "Progressive passes",
        FACT,
        "count",
        f"Open-play passes ending at least {PROGRESSIVE_M:.0f} m closer to the opponent's goal.",
    ),
    "progressive_completed": (
        "Progressive completed",
        FACT,
        "count",
        "Progressive passes completed.",
    ),
    "pressed_attempted": (
        "Passes under pressure",
        FACT,
        "count",
        f"Open-play passes made within {PRESSED_WINDOW_S:.0f} s of an opponent's pressure.",
    ),
    "pressed_completed": ("Under pressure completed", FACT, "count", "Pressed passes completed."),
    "tempo_s": (
        "Tempo (s between touches)",
        FACT,
        "s",
        "Mean seconds since the team's previous ball event in the same possession. Synthetic "
        "timing: event gaps are drawn at random, so this describes the simulation's clock.",
    ),
    "xpass": (
        "Synthetic xPass (heuristic)",
        HEURISTIC,
        "xpass",
        "Sum of expected completions from length, pressure and touchline distance. Built from "
        "the generator's rules; not calibrated on real football.",
    ),
    "completed_minus_xpass": (
        "Completed minus xPass (heuristic)",
        HEURISTIC,
        "xpass",
        "Completed passes minus synthetic xPass. Players have no passing skill in the "
        "generator, so this is luck, not quality.",
    ),
}


def _goal_centre(side: str) -> tuple[float, float]:
    return (PITCH_LENGTH if side == HOME else 0.0, PITCH_WIDTH / 2)


def _below(z: float) -> float:  # standard normal CDF
    return 0.5 * math.erfc(-z / math.sqrt(2))


def xpass(length_m: float, pressed: bool, start_y: float = PITCH_WIDTH / 2) -> float:
    in_play = 1.0 - _below(-start_y / XPASS_LATERAL_SD_M)
    in_play -= _below(-(PITCH_WIDTH - start_y) / XPASS_LATERAL_SD_M)
    p = XPASS_BASE - XPASS_PER_M * length_m - (XPASS_PRESSED if pressed else 0.0)
    return round(min(max(in_play * p, XPASS_FLOOR), XPASS_CEIL), 3)


def band_of(length_m: float) -> str:
    return "short" if length_m < SHORT_M else "long" if length_m >= LONG_M else "medium"


def pass_features(match: Match) -> list[dict[str, Any]]:
    """One record per open-play pass, in match order."""
    events = match.events
    out = []
    last_touch: dict[tuple[str, int], Event] = {}  # (team, possession) -> previous ball event
    for i, e in enumerate(events):
        prev_touch = None
        if e.type in BALL_EVENTS and e.team is not None and e.possession_id is not None:
            prev_touch = last_touch.get((e.team, e.possession_id))
            last_touch[(e.team, e.possession_id)] = e
        if e.type != "pass" or e.subtype != "open_play" or e.start is None or e.end is None:
            continue
        assert e.team is not None
        before = events[i - 1] if i else None
        pressed = (
            before is not None
            and before.type == "pressure"
            and before.team != e.team
            and before.possession_id == e.possession_id
            and e.clock_s - before.clock_s <= PRESSED_WINDOW_S
        )
        goal = _goal_centre(e.team)
        length = round(math.dist(e.start, e.end), 1)
        progress = round(math.dist(e.start, goal) - math.dist(e.end, goal), 1)
        out.append(
            {
                "event_id": e.event_id,
                "seq": e.seq,
                "team": e.team,
                "player_id": e.player_id,
                "completed": e.outcome == "complete",
                "length_m": length,
                "band": band_of(length),
                "progress_m": progress,
                "progressive": progress >= PROGRESSIVE_M,
                "pressed": bool(pressed),
                "pressure_event_id": before.event_id if pressed and before else None,
                "tempo_s": round(e.clock_s - prev_touch.clock_s, 1) if prev_touch else None,
                "xpass": xpass(length, bool(pressed), e.start[1]),
            }
        )
    return out


def team_steps(features: list[dict[str, Any]], team: str) -> list[list[Any]]:
    """Running team totals after each of the team's open-play passes:
    ``[seq, *values]``, values in ``TEAM_STATS`` order."""
    t = {k: 0 for k in ("attempted", "completed", "long_attempted", "long_completed")}
    t.update({k: 0 for k in TEAM_STATS if k.startswith(("progressive", "pressed"))})
    length = xp = tempo = 0.0
    tempo_n = 0
    steps = []
    for f in features:
        if f["team"] != team:
            continue
        done = f["completed"]
        t["attempted"] += 1
        t["completed"] += done
        length += f["length_m"]
        xp += f["xpass"]
        for flag, key in (
            (f["band"] == "long", "long"),
            (f["progressive"], "progressive"),
            (f["pressed"], "pressed"),
        ):
            if flag:
                t[f"{key}_attempted"] += 1
                t[f"{key}_completed"] += done
        if f["tempo_s"] is not None:
            tempo += f["tempo_s"]
            tempo_n += 1
        values = {
            **t,
            "avg_length_m": round(length / t["attempted"], 1),
            "completion": round(t["completed"] / t["attempted"], 4),
            "tempo_s": round(tempo / tempo_n, 1) if tempo_n else None,
            "xpass": round(xp, 2),
            "completed_minus_xpass": round(t["completed"] - xp, 2),
        }
        steps.append([f["seq"], *(values[k] for k in TEAM_STATS)])
    return steps


def passing_record(match: Match) -> dict[str, Any]:
    """The bundle's ``passing`` section: definitions, per-pass features and team steps."""
    features = pass_features(match)
    return {
        "schema": PASSING_SCHEMA,
        "stats": [
            {"name": k, "label": lab, "kind": kind, "unit": unit, "definition": d}
            for k, (lab, kind, unit, d) in TEAM_STATS.items()
        ],
        "thresholds": {
            "short_m": SHORT_M,
            "long_m": LONG_M,
            "progressive_m": PROGRESSIVE_M,
            "pressed_window_s": PRESSED_WINDOW_S,
        },
        # Compact per-pass rows: [seq, event_id, team, completed, length_m, progress_m,
        # progressive, pressed, xpass]. The browser filters them for evidence; it never
        # recomputes a total.
        "passes": [
            [
                f["seq"],
                f["event_id"],
                f["team"],
                f["completed"],
                f["length_m"],
                f["progress_m"],
                f["progressive"],
                f["pressed"],
                f["xpass"],
            ]
            for f in features
        ],
        "teams": {side: team_steps(features, side) for side in SIDES},
    }

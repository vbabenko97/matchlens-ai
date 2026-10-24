"""Evidence-grounded insight cards for two audiences.

A card is built only for a key moment that re-detects identically from the match
(``verify_moment``); otherwise it is suppressed with a reason. Every ``Fact`` on a card is
rendered from structured data that was verified first — metric and event facts via
``verify_claim``, moment facts from the verified moment's fields — and carries its event
and metric IDs. The narrative is wording only (see ``matchlens.narrative``).

Casual Fan and Analyst modes differ in content, not just headings: casual cards state who,
when, the score situation and how the move began, in plain language without statistics;
analyst cards add the evidence event chain, possession/build-up structure, the labelled
xG heuristic and window metrics, each with IDs in the text.

Causality: a card is published at ``publish_clock_s``, the time of the moment's last evidence
event, and its window metrics are computed as of that event. ``future_sources`` re-checks
that nothing a card cites happens later; a card that fails the check is suppressed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .claims import HEURISTIC_NOTE, Claim, player_name, verify_claim
from .metrics import (
    DEFAULT_WINDOW_S,
    FACT,
    HEURISTIC,
    Metric,
    as_of_id,
    compute_metrics,
    window_bounds,
)
from .moments import KeyMoment, detect_moments, select_key_moments
from .narrative import (
    CASUAL,
    MODES,
    Narrative,
    NarrativeProvider,
    NarrativeRequest,
    OfflineTemplateProvider,
    narrate,
)
from .schema import AWAY, HOME, Match, minute_of

DISPLAY_AFTER_S = 12.0  # how long a card stays up after it is published


@dataclass(frozen=True, slots=True)
class Fact:
    text: str
    kind: str  # FACT or HEURISTIC
    event_ids: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InsightCard:
    card_id: str
    mode: str
    moment_id: str
    kind: str
    team: str
    title: str
    start_clock_s: float  # earliest evidence event
    anchor_clock_s: float  # the moment's anchor event (its minute is shown on the card)
    publish_clock_s: float  # last evidence event: the card may not be shown before it
    end_clock_s: float
    facts: tuple[Fact, ...]
    evidence_event_ids: tuple[str, ...]
    narrative: Narrative


@dataclass(frozen=True, slots=True)
class CardSet:
    mode: str
    cards: tuple[InsightCard, ...]
    suppressed: tuple[tuple[str, str], ...]  # (moment_id, reason)
    metrics: tuple[Metric, ...] = ()  # every metric the cards cite, by metric_id


def verify_moment(moment: KeyMoment, detected: list[KeyMoment]) -> bool:
    """A moment is trusted only if the deterministic detectors reproduce it exactly."""
    return moment in detected


def future_sources(
    publish_clock_s: float,
    evidence_event_ids: Sequence[str],
    facts: Sequence[Fact],
    match: Match,
    metrics: dict[str, Metric],
) -> tuple[str, ...]:
    """Cited events that have not happened when the card is published, in match order.

    Covers the evidence, each fact's events and the source events of each cited metric. An
    event is in the future if it is after ``publish_clock_s`` or comes after the last
    evidence event in match order.
    """
    by_id = {e.event_id: e for e in match.events}
    known_seq = by_id[evidence_event_ids[-1]].seq
    cited = set(evidence_event_ids)
    for f in facts:
        cited.update(f.event_ids)
        for m in f.metric_ids:
            cited.update(metrics[m].source_event_ids)
    late = [
        by_id[i] for i in cited if by_id[i].seq > known_seq or by_id[i].clock_s > publish_clock_s
    ]
    return tuple(e.event_id for e in sorted(late, key=lambda e: e.seq))


def _other(side: str) -> str:
    return AWAY if side == HOME else HOME


_STARTS = {
    "tackle": "a tackle that won the ball back",
    "interception": "an interception",
    "recovery": "winning a loose ball",
    "clearance": "a clearance",
    "kick_off": "the kick-off",
    "goal_kick": "a goal kick",
    "throw_in": "a throw-in",
}


def _score_situation(match: Match, side: str, score: tuple[int, int]) -> str:
    team = match.meta.team(side).name
    mine, theirs = (score[0], score[1]) if side == HOME else (score[1], score[0])
    if mine == theirs:
        return f"That levels the score at {score[0]}-{score[1]}."
    if mine - theirs == 1:
        return f"That puts {team} ahead, {score[0]}-{score[1]}."
    if mine > theirs:
        return f"That stretches {team}'s lead to {score[0]}-{score[1]}."
    return f"That pulls one back: {score[0]}-{score[1]}."


def _score_at(match: Match, event_id: str) -> tuple[int, int]:
    e = next(e for e in match.events if e.event_id == event_id)
    return (e.home_score, e.away_score)


def _casual(moment: KeyMoment, match: Match) -> tuple[str, list[Fact]]:
    d = moment.details
    team = match.meta.team(moment.team).name
    who = player_name(match, moment.team, d.get("player_id"))
    minute = minute_of(moment.clock_s)
    ids = moment.event_ids
    anchor = (moment.anchor_event_id,)
    if moment.kind == "goal":
        h, a = d["score"]
        start = _STARTS.get(d["chain_start"], d["chain_start"].replace("_", " "))
        return f"GOAL! {team} score", [
            Fact(f"{who} scores for {team} in minute {minute}.", FACT, anchor),
            Fact(_score_situation(match, moment.team, (h, a)), FACT, anchor),
            Fact(f"The move started with {start}.", FACT, ids),
        ]
    if moment.kind == "big_chance_missed":
        outcome = {"saved": "saved", "off_target": "off target", "blocked": "blocked"}
        h, a = _score_at(match, moment.anchor_event_id)
        return f"So close for {team}!", [
            Fact(
                f"{who} had a great chance in minute {minute}, but the shot was "
                f"{outcome[d['outcome']]}.",
                FACT,
                ids,
            ),
            Fact(f"The score stays {h}-{a}.", FACT, anchor),
        ]
    opponent = match.meta.team(_other(moment.team)).name
    return f"{team} win it back", [
        Fact(f"{team} pressed {opponent} and {who} won the ball in minute {minute}.", FACT, ids),
        Fact(f"{team} had a shot within seconds of winning it.", FACT, (d["shot_event_id"],)),
    ]


def _window_of(match: Match, clock_s: float) -> tuple[int, int]:
    bounds = window_bounds(match, DEFAULT_WINDOW_S)
    for i, (lo, hi) in enumerate(bounds):
        if lo <= clock_s < hi or (i == len(bounds) - 1 and clock_s == hi):
            return int(lo), int(hi)
    raise ValueError(f"clock {clock_s} outside match")


def _metric_fact(metric_id: str, metrics: dict[str, Metric], match: Match) -> Fact | None:
    metric = metrics.get(metric_id)
    if metric is None:
        return None
    check = verify_claim(Claim(metric_id, metric.value), metrics, match)
    return Fact(check.text, metric.kind, (), (metric_id,)) if check.verified else None


def _event_fact(event_id: str, metrics: dict[str, Metric], match: Match) -> Fact | None:
    check = verify_claim(Claim(cited_event_ids=(event_id,)), metrics, match)
    return Fact(check.text, FACT, (event_id,)) if check.verified else None


def _analyst(
    moment: KeyMoment, match: Match, metrics: dict[str, Metric]
) -> tuple[str, list[Fact | None]]:
    """``metrics`` must be computed as of the moment's last evidence event."""
    d = moment.details
    team = match.meta.team(moment.team).name
    minute = minute_of(moment.clock_s)
    ids = moment.event_ids
    lo, hi = _window_of(match, moment.clock_s)
    win = f"@{lo}-{hi}"
    side, opp = moment.team, _other(moment.team)
    evidence = f" [evidence: {', '.join(ids)}]"
    xg = d["xg_heuristic"]
    facts: list[Fact | None] = [_event_fact(i, metrics, match) for i in ids]
    if moment.kind == "goal":
        poss = next(e.possession_id for e in match.events if e.event_id == moment.anchor_event_id)
        title = f"Goal {minute}' — {team} ({d['score'][0]}-{d['score'][1]})"
        facts += [
            Fact(
                f"Possession #{poss}: {len(ids)} events, {d['build_up_passes']} pass(es) before "
                f"the shot, started by {d['chain_start'].replace('_', ' ')}.{evidence}",
                FACT,
                ids,
            ),
            Fact(f"Shot quality {xg:.3f} xG [{HEURISTIC_NOTE}].{evidence}", HEURISTIC, ids),
        ]
        metric_ids = [f"{side}.shots{win}", f"{side}.xg_heuristic{win}", f"{opp}.pressures{win}"]
    elif moment.kind == "big_chance_missed":
        title = f"Missed big chance {minute}' — {team}"
        facts.append(
            Fact(
                f"Shot quality {xg:.3f} xG, above the 0.2 big-chance threshold "
                f"[{HEURISTIC_NOTE}].{evidence}",
                HEURISTIC,
                ids,
            )
        )
        metric_ids = [f"{side}.shots{win}", f"{side}.xg_heuristic{win}", f"{side}.turnovers{win}"]
    else:
        title = f"Press win {minute}' — {team}"
        facts += [
            Fact(
                f"Ball won by {d['win_type']} {d['seconds_to_win']:.1f} s after pressure "
                f"{d['pressure_event_id']}; shot {d['shot_event_id']} followed.{evidence}",
                FACT,
                ids,
            ),
            Fact(f"Resulting shot {xg:.3f} xG [{HEURISTIC_NOTE}].{evidence}", HEURISTIC, ids),
        ]
        metric_ids = [f"{side}.pressures{win}", f"{opp}.turnovers{win}", f"{side}.shots{win}"]
    facts += [_metric_fact(as_of_id(m, ids[-1]), metrics, match) for m in metric_ids]
    return title, facts


def build_cards(
    match: Match,
    mode: str,
    provider: NarrativeProvider | None = None,
    limit: int = 4,
    moments: list[KeyMoment] | None = None,
) -> CardSet:
    """Build verified cards for the selected key moments (or for ``moments`` if given).

    Each card is published when its moment's last evidence event happens, and analyst
    metrics count only the events up to that one.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    provider = provider or OfflineTemplateProvider()
    detected = detect_moments(match)
    candidates = moments if moments is not None else select_key_moments(detected, limit)
    cards: list[InsightCard] = []
    suppressed: list[tuple[str, str]] = []
    cited: dict[str, Metric] = {}
    for moment in candidates:
        if not verify_moment(moment, detected):
            suppressed.append((moment.moment_id, "moment is not reproduced by the detectors"))
            continue
        metrics: dict[str, Metric] = {}
        if mode == CASUAL:
            title, facts = _casual(moment, match)
        else:
            metrics = compute_metrics(match, DEFAULT_WINDOW_S, as_of_event_id=moment.event_ids[-1])
            title, maybe = _analyst(moment, match, metrics)
            facts = [f for f in maybe if f is not None]
        late = future_sources(moment.known_clock_s, moment.event_ids, facts, match, metrics)
        if late:
            suppressed.append(
                (moment.moment_id, f"card cites events after it is published: {list(late)}")
            )
            continue
        cited.update((m, metrics[m]) for f in facts for m in f.metric_ids)
        narrative = narrate(
            NarrativeRequest(mode=mode, title=title, facts=tuple(f.text for f in facts)),
            provider,
        )
        cards.append(
            InsightCard(
                card_id=f"{mode}:{moment.moment_id}",
                mode=mode,
                moment_id=moment.moment_id,
                kind=moment.kind,
                team=moment.team,
                title=title,
                start_clock_s=moment.start_clock_s,
                anchor_clock_s=moment.clock_s,
                publish_clock_s=moment.known_clock_s,
                end_clock_s=moment.known_clock_s + DISPLAY_AFTER_S,
                facts=tuple(facts),
                evidence_event_ids=moment.event_ids,
                narrative=narrative,
            )
        )
    return CardSet(mode, tuple(cards), tuple(suppressed), tuple(cited[m] for m in sorted(cited)))


def format_card(card: InsightCard) -> str:
    """Plain-text rendering for the CLI demo: verified facts first, wording clearly labelled."""
    lines = [f"[{card.mode.upper()}] {card.title}  ({minute_of(card.anchor_clock_s)}')"]
    lines += [f"  ✓ {f.text}" for f in card.facts]
    n = card.narrative
    lines.append(f"  Wording ({n.label}): {n.text}")
    if n.fallback_reason:
        lines.append(f"  Note: {n.fallback_reason}")
    lines += [f"  ! {w}" for w in n.warnings]
    return "\n".join(lines)

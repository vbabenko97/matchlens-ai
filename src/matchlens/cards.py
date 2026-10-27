"""Evidence-grounded insight cards for two audiences.

A card is built only for a key moment that re-detects identically from the match
(``verify_moment``); otherwise it is suppressed with a reason. Every ``Fact`` on a card is
rendered from structured data that was verified first — metric and event facts via
``verify_claim``, moment facts from the verified moment's fields — and carries its event
and metric IDs. The narrative is wording only (see ``matchlens.narrative``).

Every card leads with why the moment matters (``role == WHY``): match-story lines from
``matchlens.story`` (comeback, equaliser, what a missed chance would have meant, how the
match was won), each re-checked against its cited events by ``check_line``; a line that
fails is dropped and reported in ``suppressed``. Then come the facts of what happened.

Casual Fan and Analyst modes differ in content, not just headings: casual cards state who,
when, why it matters and how the move began, in plain language; analyst cards add the
evidence event chain (``role == CHAIN``), possession/build-up structure, the labelled xG
heuristic and window metrics, each with IDs in the text. The full-time card compares shots
with chance quality (synthetic xG) in both modes.

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
from .moments import DRAW, KeyMoment, detect_moments, select_key_moments
from .narrative import (
    ANALYST,
    CASUAL,
    MODES,
    Narrative,
    NarrativeProvider,
    NarrativeRequest,
    OfflineTemplateProvider,
    narrate,
)
from .schema import AWAY, HOME, PASS_SUBTYPES, Match, minute_of
from .story import StoryLine, chance_story, check_line, full_time_story, goal_story

DISPLAY_AFTER_S = 12.0  # how long a card stays up after it is published
WHY = "why"  # why the moment matters: match-story and comparison lines
WHAT = "what"  # what happened
CHAIN = "chain"  # one evidence event of the move (analyst)


@dataclass(frozen=True, slots=True)
class Fact:
    text: str
    kind: str  # FACT or HEURISTIC
    event_ids: tuple[str, ...] = ()
    metric_ids: tuple[str, ...] = ()
    role: str = WHAT  # WHY, WHAT or CHAIN


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


_GOAL_TITLES = {
    "opener": "{team} strike first",
    "equaliser": "{team} draw level",
    "comeback": "Comeback! {team} take the lead",
    "back_in_front": "{team} back in front",
    "extends_lead": "{team} extend their lead",
    "pulls_back": "{team} pull one back",
}


def _story_facts(
    lines: tuple[StoryLine, ...],
    match: Match,
    as_of_event_id: str,
    dropped: list[str],
    evidence: bool,
) -> tuple[list[Fact], set[str]]:
    """WHY facts and kinds of the story lines that ``check_line`` accepts; the reasons for
    the others go to ``dropped``."""
    out, kinds = [], set()
    for line in lines:
        reasons = check_line(line, match, as_of_event_id)
        if reasons:
            dropped.append(f"story line {line.kind} dropped: {'; '.join(reasons)}")
            continue
        text = line.text + (f" [evidence: {', '.join(line.event_ids)}]" if evidence else "")
        out.append(Fact(text, FACT, line.event_ids, (), WHY))
        kinds.add(line.kind)
    return out, kinds


def _move(d: dict, ids: tuple[str, ...]) -> Fact:
    start = _STARTS.get(d["chain_start"], d["chain_start"].replace("_", " "))
    # ``build_up_passes`` counts every pass of the chain; when the chain began with a pass (a
    # throw-in, a goal kick, the kick-off), that pass is the start, not one of the passes after.
    n = d["build_up_passes"] - (d["chain_start"] in PASS_SUBTYPES)
    if n == 0:
        return Fact(f"It came straight from {start}.", FACT, ids)
    return Fact(f"The move began with {start}, then {n} pass{'es' if n > 1 else ''}.", FACT, ids)


def _shots_vs_chances(
    match: Match, metrics: dict[str, Metric], end_id: str, evidence: bool
) -> Fact | None:
    """Full time: who had more shots and who had the better chances (synthetic xG)."""
    ids = {
        (side, name): as_of_id(f"{side}.{name}", end_id)
        for side in (HOME, AWAY)
        for name in ("shots", "xg_heuristic")
    }
    for metric_id in ids.values():
        metric = metrics.get(metric_id)
        check = verify_claim(Claim(metric_id, metric.value), metrics, match) if metric else None
        if check is None or not check.verified:
            return None
    shots = {side: metrics[ids[side, "shots"]].value for side in (HOME, AWAY)}
    xg = {side: metrics[ids[side, "xg_heuristic"]].value for side in (HOME, AWAY)}
    name = {side: match.meta.team(side).name for side in (HOME, AWAY)}
    more = max((HOME, AWAY), key=lambda side: shots[side])
    better = max((HOME, AWAY), key=lambda side: xg[side])
    if shots[HOME] == shots[AWAY] or xg[HOME] == xg[AWAY]:
        return None
    s = f"{shots[more]}–{shots[_other(more)]}"
    x = f"{xg[better]:.2f} vs {xg[_other(better)]:.2f}"
    if more == better:
        text = f"{name[more]} had more shots ({s}) and the better chances (synthetic xG {x})."
    else:
        text = (
            f"{name[more]} had more shots ({s}), but {name[better]} had the better chances "
            f"(synthetic xG {x})."
        )
    if evidence:
        text += f" [{HEURISTIC_NOTE}] [evidence: {', '.join('metric ' + m for m in ids.values())}]"
    else:
        text = text.replace("(synthetic xG ", "(synthetic xG, a heuristic: ")
    return Fact(text, HEURISTIC, (), tuple(ids.values()), WHY)


def _full_time(
    moment: KeyMoment, match: Match, metrics: dict[str, Metric], dropped: list[str], analyst: bool
) -> tuple[str, list[Fact | None]]:
    """Title and WHY facts of the full-time card: the result, how it was won, shots vs
    chance quality."""
    last = moment.event_ids[-1]
    story = full_time_story(match, last)
    facts, kinds = _story_facts(story, match, last, dropped, evidence=analyst)
    h, a = moment.details["score"]
    home, away = match.meta.home.name, match.meta.away.name
    if analyst:
        title = f"Full time — {home} {h}-{a} {away}"
    elif moment.team == DRAW:
        title = f"Full time: {home} {h}-{a} {away}"
    else:
        won = "come from behind to win" if "ft_comeback" in kinds else "win"
        title = f"Full time: {match.meta.team(moment.team).name} {won} {h}-{a}"
    return title, [*facts, _shots_vs_chances(match, metrics, last, evidence=analyst)]


def _casual(
    moment: KeyMoment, match: Match, metrics: dict[str, Metric], dropped: list[str]
) -> tuple[str, list[Fact | None]]:
    d = moment.details
    team = match.meta.team(moment.team).name
    who = player_name(match, moment.team, d.get("player_id"))
    minute = minute_of(moment.clock_s)
    ids = moment.event_ids
    anchor = (moment.anchor_event_id,)
    last = ids[-1]
    if moment.kind == "goal":
        why, kinds = _story_facts(goal_story(match, anchor[0]), match, last, dropped, False)
        situation = next((k for k in _GOAL_TITLES if k in kinds), "")
        title = _GOAL_TITLES.get(situation, "GOAL! {team} score").format(team=team)
        h, a = d["score"]
        scored = Fact(f"{who} scores in minute {minute}: {h}-{a}.", FACT, anchor)
        return title, [scored, *why, _move(d, ids)]
    if moment.kind == "big_chance_missed":
        outcome = {"saved": "saved", "off_target": "off target", "blocked": "blocked"}
        why, _ = _story_facts(chance_story(match, anchor[0]), match, last, dropped, False)
        return f"So close for {team}!", [
            Fact(
                f"{who} had a great chance in minute {minute}, but the shot was "
                f"{outcome[d['outcome']]}.",
                FACT,
                ids,
            ),
            *why,
        ]
    if moment.kind == "full_time":
        return _full_time(moment, match, metrics, dropped, analyst=False)
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
    return Fact(check.text, FACT, (event_id,), (), CHAIN) if check.verified else None


def _analyst(
    moment: KeyMoment, match: Match, metrics: dict[str, Metric], dropped: list[str]
) -> tuple[str, list[Fact | None]]:
    """``metrics`` must be computed as of the moment's last evidence event."""
    d = moment.details
    ids = moment.event_ids
    if moment.kind == "full_time":
        title, why = _full_time(moment, match, metrics, dropped, analyst=True)
        names = ("goals", "shots", "shots_on_target", "xg_heuristic", "pass_completion")
        totals = [
            _metric_fact(as_of_id(f"{side}.{n}", ids[-1]), metrics, match)
            for n in names
            for side in (HOME, AWAY)
        ]
        return title, why + totals
    team = match.meta.team(moment.team).name
    minute = minute_of(moment.clock_s)
    lo, hi = _window_of(match, moment.clock_s)
    win = f"@{lo}-{hi}"
    side, opp = moment.team, _other(moment.team)
    evidence = f" [evidence: {', '.join(ids)}]"
    xg = d["xg_heuristic"]
    story = (
        goal_story(match, moment.anchor_event_id)
        if moment.kind == "goal"
        else chance_story(match, moment.anchor_event_id)
        if moment.kind == "big_chance_missed"
        else ()
    )
    facts: list[Fact | None] = [*_story_facts(story, match, ids[-1], dropped, True)[0]]
    facts += [_event_fact(i, metrics, match) for i in ids]
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
    limit: int | None = None,
    moments: list[KeyMoment] | None = None,
) -> CardSet:
    """Build verified cards for the selected key moments (or for ``moments`` if given).

    Each card is published when its moment's last evidence event happens, and metrics count
    only the events up to that one. ``limit`` keeps the first ``limit`` moments.
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
        if mode == ANALYST or moment.kind == "full_time":
            metrics = compute_metrics(match, DEFAULT_WINDOW_S, as_of_event_id=moment.event_ids[-1])
        dropped: list[str] = []
        build = _casual if mode == CASUAL else _analyst
        title, maybe = build(moment, match, metrics, dropped)
        facts = [f for f in maybe if f is not None]
        suppressed += [(moment.moment_id, reason) for reason in dropped]
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
    when = "FT" if card.kind == "full_time" else f"{minute_of(card.anchor_clock_s)}'"
    lines = [f"[{card.mode.upper()}] {card.title}  ({when})"]
    lines += [f"  ✓ {f.text}" for f in card.facts]
    n = card.narrative
    lines.append(f"  Wording ({n.label}): {n.text}")
    if n.fallback_reason:
        lines.append(f"  Note: {n.fallback_reason}")
    lines += [f"  ! {w}" for w in n.warnings]
    return "\n".join(lines)

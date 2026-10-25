"""Event-by-event processing: a synthetic feed in, cards out as soon as they are known.

``StreamProcessor`` takes one event at a time, as from a live feed, and never sees an event
before it arrives:

- Each event is checked on arrival with the rules of ``validate_match``
  (``EventValidator``). The first invalid event stops publishing for the rest of the
  stream: a broken feed fails closed instead of putting doubtful facts on screen.
- When an event can complete a key moment (a shot, the event after a shot, the final
  whistle), moments are detected again on the events received so far. Every complete moment
  without a card gets one in each audience mode, built from those events alone.
- A missed big chance waits one event: the next event may answer the shot (a save, a
  clearance, a goal kick) and then belongs to its evidence. When the stream ends, a moment
  still waiting is completed with what arrived.
- No model is called per event: card wording is the offline template unless a provider is
  given.

``stream_check`` feeds a whole match through the processor and compares the result with the
batch cards. The replay bundle carries that result, and the build fails on any difference.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .cards import CardSet, InsightCard, build_cards
from .moments import detect_moments, select_key_moments
from .narrative import MODES, NarrativeProvider, OfflineTemplateProvider
from .schema import AWAY, HOME, Event, Match, MatchMeta
from .validate import EventValidator

STREAM_CHECK_SCHEMA = "matchlens.stream-check/v1"
WAITS_FOR_NEXT = frozenset({"big_chance_missed"})  # the next event may join the evidence


@dataclass(frozen=True)
class Publication:
    card: InsightCard
    event_id: str  # the event whose arrival published the card
    seq: int
    clock_s: float
    evidence_seq: int  # the card's last evidence event; ``seq - evidence_seq`` is the wait


@dataclass(frozen=True)
class Update:
    """What one event changed."""

    event: Event
    score: tuple[int, int]
    shots: tuple[int, int]  # home, away, so far
    xg: tuple[float, float]  # synthetic xG heuristic, so far
    issues: tuple[str, ...]  # validation issues found on this event
    published: tuple[Publication, ...]


class StreamProcessor:
    def __init__(
        self,
        meta: MatchMeta,
        provider: NarrativeProvider | None = None,
        modes: Sequence[str] = MODES,
    ):
        self.meta = meta
        self._provider = provider or OfflineTemplateProvider()
        self._modes = tuple(modes)
        self._validator = EventValidator(meta)
        self._events: list[Event] = []
        self._seq: dict[str, int] = {}
        self._done: set[str] = set()  # moments already turned into cards (or suppressed)
        self._shots = {HOME: 0, AWAY: 0}
        self._xg = {HOME: 0.0, AWAY: 0.0}
        self.issues: list[str] = []  # per-event issues; any of them halts publishing
        self.end_issues: list[str] = []  # whole-match checks, run by ``close``
        self.publications: list[Publication] = []
        self.suppressed: list[tuple[str, str, str]] = []  # (mode, moment_id, reason)
        self.closed = False

    @property
    def halted(self) -> bool:
        return bool(self.issues)

    @property
    def events_received(self) -> int:
        return len(self._events)

    @property
    def last_event(self) -> Event | None:
        return self._events[-1] if self._events else None

    def push(self, event: Event) -> Update:
        if self.closed:
            raise RuntimeError("the stream is closed")
        issues = tuple(self._validator.check(event))
        self.issues += issues
        prev = self._events[-1] if self._events else None
        self._events.append(event)
        self._seq[event.event_id] = event.seq
        if event.type == "shot" and event.team in (HOME, AWAY):
            self._shots[event.team] += 1
            self._xg[event.team] += event.xg_heuristic or 0.0
        published: tuple[Publication, ...] = ()
        may_complete = event.type in ("shot", "period_end") or (prev and prev.type == "shot")
        if may_complete and not self.halted:
            published = self._publish(event, final=False)
        return Update(
            event=event,
            score=(event.home_score, event.away_score),
            shots=(self._shots[HOME], self._shots[AWAY]),
            xg=(round(self._xg[HOME], 3), round(self._xg[AWAY], 3)),
            issues=issues,
            published=published,
        )

    def close(self) -> tuple[Publication, ...]:
        """End of the stream: complete the moments still waiting, then run the whole-match
        checks (an early end, without the final whistle, is reported in ``end_issues``)."""
        if self.closed:
            return ()
        self.closed = True
        published: tuple[Publication, ...] = ()
        if self._events and not self.halted:
            published = self._publish(self._events[-1], final=True)
        self.end_issues = self._validator.finish() if self._events else ["no events received"]
        return published

    def _publish(self, at: Event, final: bool) -> tuple[Publication, ...]:
        match = Match(self.meta, tuple(self._events))
        out: list[Publication] = []
        for moment in select_key_moments(detect_moments(match)):
            if moment.moment_id in self._done:
                continue
            waiting = moment.kind in WAITS_FOR_NEXT and self._seq[moment.anchor_event_id] == at.seq
            if waiting and not final:
                continue
            self._done.add(moment.moment_id)
            for mode in self._modes:
                card_set = build_cards(match, mode, self._provider, moments=[moment])
                self.suppressed += [(mode, mid, reason) for mid, reason in card_set.suppressed]
                for card in card_set.cards:
                    last = self._seq[card.evidence_event_ids[-1]]
                    out.append(Publication(card, at.event_id, at.seq, at.clock_s, last))
        self.publications += out
        return tuple(out)


def run_stream(match: Match, provider: NarrativeProvider | None = None) -> StreamProcessor:
    """Feed every event of ``match`` in order, then close the stream."""
    processor = StreamProcessor(match.meta, provider)
    for event in match.events:
        processor.push(event)
    processor.close()
    return processor


def _content(card: InsightCard) -> dict[str, Any]:
    record = asdict(card)
    record.pop("narrative")  # wording depends on the provider, not on how events arrived
    return record


def stream_check(match: Match, card_sets: Sequence[CardSet]) -> dict[str, Any]:
    """Feed ``match`` one event at a time and compare with the batch ``card_sets``.

    The streamed cards must equal the batch cards (wording aside), each published on the
    arrival of its last evidence event, with the same suppressions and no validation issue.
    Raises ``ValueError`` on any difference.
    """
    processor = run_stream(match)
    problems = [*processor.issues, *processor.end_issues]
    batch = {(c.mode, c.card_id): c for cs in card_sets for c in cs.cards}
    streamed = {(p.card.mode, p.card.card_id): p for p in processor.publications}
    if len(streamed) != len(processor.publications):
        problems.append("a card was published twice")
    if set(batch) != set(streamed):
        only_batch = sorted(set(batch) - set(streamed))
        only_stream = sorted(set(streamed) - set(batch))
        problems.append(f"different cards: batch only {only_batch}, stream only {only_stream}")
    for key in sorted(set(batch) & set(streamed)):
        card, pub = batch[key], streamed[key]
        if _content(pub.card) != _content(card):
            problems.append(f"{key[1]} ({key[0]}): content differs from the batch card")
        if pub.seq != pub.evidence_seq:
            problems.append(
                f"{key[1]} ({key[0]}): published at event {pub.seq}, its last evidence is "
                f"event {pub.evidence_seq}"
            )
    batch_suppressed = {(cs.mode, mid, why) for cs in card_sets for mid, why in cs.suppressed}
    if batch_suppressed != set(processor.suppressed):
        problems.append("suppressed moments differ from the batch build")
    if problems:
        raise ValueError("stream check failed: " + "; ".join(problems[:5]))
    return {
        "schema": STREAM_CHECK_SCHEMA,
        "events": len(match.events),
        "cards": len(batch),
        "identical_to_batch": len(batch),
        "published_at_last_evidence": len(batch),
    }

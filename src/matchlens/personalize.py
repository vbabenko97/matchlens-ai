"""Personalizer agent: rewrites a casual card for a language and a club, checked line by line.

The second agent in MatchLens. The claim agent decides *what* a card may say; this one only
re-words a card that is already verified, for a language (English, German, Ukrainian) and a
point of view (neutral, or a fan of one team). A Microsoft Foundry deployment (Responses API
v1, structured JSON output) rewrites the title and each fact separately, and
``check_line`` compares every rewritten line with its English source:

- the same numbers, as digits (scores, minutes, counts, xG): ``2-3`` may become ``2:3`` and
  ``3.04`` may become ``3,04``, but nothing may be added, dropped or changed;
- every score or count pair in the same order (home first, as on the scoreboard): ``2-3``
  never becomes ``3-2``, even where a language would usually put the subject's goals first;
- every player and team name of the source, verbatim (Latin script), and no other name;
- protected terms such as ``xG`` kept;
- the target language's script (and no Russian-only letters in Ukrainian), no markup or
  links, a bounded length.

Rejected lines go back to the agent once with the reasons; a line still rejected keeps its
English source text, marked as kept. Every published line keeps the event and metric IDs of
the fact it came from: the agent never cites anything itself. What the check cannot see is
meaning and tone: a rewrite can keep every number and name and still drift ("the lead
changed hands" becoming "confidence changed hands"), so the page labels these lines as AI
wording with numbers and names checked and meaning not checked, never as verified facts.

``RecordedPersonalizer`` replays a recorded live run (the static demo cannot call Azure);
the checks are recomputed whenever a bundle is built.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .agent import _call_record, _incomplete_reason, post_with_retries
from .cards import CardSet, InsightCard
from .narrative import (
    AzureOpenAIConfig,
    ResponseFormatError,
    Transport,
    extract_output_text,
)
from .schema import AWAY, HOME, Match

LANGS = {"en": "English", "de": "German", "uk": "Ukrainian"}
NEUTRAL = "neutral"
FOCUSES = (NEUTRAL, HOME, AWAY)
# Every variant but the source itself (English, neutral).
VARIANTS = tuple((lang, f) for lang in LANGS for f in FOCUSES if (lang, f) != ("en", NEUTRAL))
TRACE_SCHEMA = "matchlens.personalize-trace/v1"
# The recorded run the public builds replay (deploy-pages.yml, deploy-swa.yml).
PERSONALIZE_TRACE = "docs/evidence/personalize-trace-2026-10-24.json"
FOUNDRY = "foundry_personalizer"
RECORDED = "recorded_foundry_personalizer"
VERIFIED = "verified"
KEPT = "kept_original"  # rejected twice (or not rewritten): the English source is shown
TITLE = -1  # line index of the card title
MAX_OUTPUT_TOKENS = 700
PROTECTED_TERMS = ("xG",)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_LETTER = re.compile(r"[^\W\d_]")
# A score or count pair is written tight ("2-3", "2:3", "14–11"); "minute 81: 2-3" holds one.
_SCORE = re.compile(r"(?<![\d.,])(\d+)[-–:](\d+)(?!\d|[.,]\d)")
# Letters of Russian that Ukrainian does not use: a sure sign of a slip into Russian.
_NOT_UKRAINIAN = re.compile(r"[ыэъё]", re.IGNORECASE)
_MARKUP = re.compile(r"[<>\[\]{}]|https?://|www\.", re.IGNORECASE)


def variant_key(lang: str, focus: str) -> str:
    return f"{lang}|{focus}"


def known_names(match: Match) -> tuple[str, ...]:
    """Every team and player name of the match, longest first (so no name hides another)."""
    teams = (match.meta.home, match.meta.away)
    names = {t.name for t in teams} | {p.name for t in teams for p in t.players}
    return tuple(sorted(names, key=lambda n: (-len(n), n)))


def numbers(text: str) -> Counter[str]:
    """Numbers as written in digits; a decimal comma counts as a decimal point."""
    return Counter(n.replace(",", ".") for n in _NUMBER.findall(text))


def _names_in(text: str, names: Iterable[str]) -> set[str]:
    found, rest = set(), text
    for name in names:  # longest first; blank out each hit so a shorter name cannot match it
        if name in rest:
            found.add(name)
            rest = rest.replace(name, " ")
    return found


def check_line(source: str, text: str, lang: str, names: Sequence[str]) -> list[str]:
    """Reasons ``text`` is not a faithful rewrite of ``source`` in ``lang`` (empty: accepted)."""
    text = text.strip()
    if not text:
        return ["empty"]
    reasons = []
    if len(text) > 3 * len(source) + 60:
        reasons.append("too long")
    if _MARKUP.search(text):
        reasons.append("contains markup or a link")
    want, got = numbers(source), numbers(text)
    if want != got:
        missing = sorted((want - got).elements())
        extra = sorted((got - want).elements())
        detail = "; ".join(
            part
            for part in (
                f"missing {', '.join(missing)}" if missing else "",
                f"not in the fact: {', '.join(extra)}" if extra else "",
            )
            if part
        )
        reasons.append(f"numbers differ ({detail})")
    want_pairs, got_pairs = Counter(_SCORE.findall(source)), Counter(_SCORE.findall(text))
    if want == got and want_pairs != got_pairs:
        pairs = ", ".join(f"{a}-{b}" for a, b in sorted(want_pairs.elements()))
        reasons.append(f"score order changed (keep {pairs}, home first)")
    expected, used = _names_in(source, names), _names_in(text, names)
    for name in sorted(expected - used):
        reasons.append(f"name changed or missing: {name}")
    for name in sorted(used - expected):
        reasons.append(f"adds a name not in the fact: {name}")
    for term in PROTECTED_TERMS:
        if term in source and term not in text:
            reasons.append(f"drops '{term}'")
    rest = text
    for name in expected | used:
        rest = rest.replace(name, " ")
    letters = _LETTER.findall(rest)
    cyrillic = sum(bool(_CYRILLIC.match(c)) for c in letters)
    if lang == "uk" and letters and cyrillic < 0.5 * len(letters):
        reasons.append("not written in Ukrainian")
    if lang == "uk" and _NOT_UKRAINIAN.search(rest):
        reasons.append("Russian letters in a Ukrainian text")
    if lang != "uk" and cyrillic:
        reasons.append(f"Cyrillic letters in a {LANGS[lang]} text")
    if lang != "en" and text == source.strip() and len(letters) > 3:
        reasons.append("not translated")
    return reasons


# --- Proposers ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Draft:
    """One reply of the personalizer: rewritten lines by index (``TITLE`` for the title)."""

    lines: dict[int, str]
    calls: tuple[dict[str, Any], ...] = ()


class Personalizer(Protocol):
    name: str
    label: str

    def propose(
        self, card: InsightCard, lang: str, focus: str, team_names: dict[str, str], feedback=()
    ) -> Draft: ...


def card_view(card: InsightCard) -> dict[str, Any]:
    """What the agent sees: the title and the card's verified fact texts, nothing else."""
    return {
        "title": card.title,
        "facts": [{"index": i, "text": f.text} for i, f in enumerate(card.facts)],
    }


OUTPUT_FORMAT = {
    "type": "json_schema",
    "name": "personalized_card",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["title", "lines"],
        "properties": {
            "title": {"type": "string"},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["index", "text"],
                    "properties": {"index": {"type": "integer"}, "text": {"type": "string"}},
                },
            },
        },
    },
}

INSTRUCTIONS = (
    "You rewrite short, already verified football facts for fans of a synthetic match. "
    "Return JSON only: the title and one line per input fact, with the same index. "
    "Rules: write in the requested language. Keep every number as digits and keep exactly the "
    "numbers of each fact: no new numbers, none dropped, none changed (scores, minutes, "
    "counts, xG). Keep every player and team name exactly as written, in Latin script: never "
    "translate, transliterate or inflect a name, and add no other names. Keep 'xG' and the "
    "note that it is a synthetic heuristic. Do not add events, causes, statistics or "
    "predictions. For a fan's point of view you may change the tone (joy, relief, "
    "disappointment), never the facts. No markup, no links."
)


def personalizer_brief(
    card: InsightCard, lang: str, focus: str, team_names: dict[str, str], feedback=()
) -> str:
    audience = "neutral viewers" if focus == NEUTRAL else f"fans of {team_names[focus]}"
    brief = {"language": LANGS[lang], "audience": audience, **card_view(card)}
    if feedback:
        brief["rejected"] = list(feedback)
        brief["task"] = (
            "Some lines were rejected by the checker for the reasons given. Rewrite those "
            "lines so they pass; return every line again."
        )
    return json.dumps(brief, ensure_ascii=False)


def parse_draft(text: str) -> dict[int, str]:
    try:
        doc = json.loads(text)
    except ValueError:
        raise ResponseFormatError("personalizer reply is not JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("lines"), list):
        raise ResponseFormatError("personalizer reply has no lines")
    lines: dict[int, str] = {}
    if isinstance(doc.get("title"), str):
        lines[TITLE] = doc["title"]
    for item in doc["lines"]:
        ok = isinstance(item, dict) and isinstance(item.get("index"), int)
        if ok and isinstance(item.get("text"), str) and item["index"] >= 0:
            lines.setdefault(item["index"], item["text"])  # a repeated index: first wins
    return lines


class FoundryPersonalizer:
    """One Responses API (v1) call per card and attempt, structured JSON output, no tools."""

    name = FOUNDRY

    def __init__(
        self,
        config: AzureOpenAIConfig,
        transport: Transport | None = None,
        timeout_s: float = 30.0,
        sleep: Callable[[float], None] | None = None,
        log: Callable[[str], None] = lambda line: None,
    ):
        self.config, self.transport, self.timeout_s = config, transport, timeout_s
        self.sleep, self.log = sleep or time.sleep, log
        self.label = (
            f"Microsoft Foundry personalizer (deployment {config.deployment}, Responses API "
            "v1), run when this output was built; numbers and names checked line by line."
        )

    def propose(self, card, lang, focus, team_names, feedback=()) -> Draft:
        body = {
            "model": self.config.deployment,
            "input": [
                {"role": "system", "content": INSTRUCTIONS},
                {
                    "role": "user",
                    "content": personalizer_brief(card, lang, focus, team_names, feedback),
                },
            ],
            "text": {"format": OUTPUT_FORMAT},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        response, retries = post_with_retries(
            self.config, body, self.transport, self.timeout_s, self.sleep, self.log
        )
        if _incomplete_reason(response) == "max_output_tokens":
            raise ResponseFormatError("personalizer reply was cut off")
        text = extract_output_text(response)  # raises unless completed with output_text
        return Draft(parse_draft(text), (_call_record(response, [], retries),))


class RecordedPersonalizer:
    """Replays the drafts of a recorded live run; every line is re-checked."""

    name = RECORDED

    def __init__(self, trace: dict[str, Any], source: str):
        problems = trace_problems(trace)
        if problems:
            raise ValueError("not acceptable evidence: " + "; ".join(problems))
        self.match_id = trace["match_id"]
        self._runs = {(r["card_id"], r["lang"], r["focus"]): r["attempts"] for r in trace["runs"]}
        self.label = (
            f"Recorded Microsoft Foundry personalizer run ({trace['recorded_at_utc']}, "
            f"deployment {trace['deployment']}), replayed from {source} — not a live call. "
            "Numbers and names are re-checked line by line when this output is built."
        )

    def covers(self, card_id: str, lang: str, focus: str) -> bool:
        return (card_id, lang, focus) in self._runs

    def propose(self, card, lang, focus, team_names, feedback=()) -> Draft:
        rec = self._runs[(card.card_id, lang, focus)][1 if feedback else 0]
        lines = {int(k): v for k, v in rec["lines"].items()}
        return Draft(lines, tuple(rec["calls"]))


# --- Personalizing a card ----------------------------------------------------------------


@dataclass(frozen=True)
class LineResult:
    index: int  # TITLE or the fact's index on the card
    source: str
    text: str  # what is shown: the verified rewrite, or the English source when kept
    status: str  # VERIFIED or KEPT
    attempt: int  # the attempt whose text is shown (0 when kept)
    reasons: tuple[str, ...] = ()  # the last rejection, when kept


@dataclass(frozen=True)
class PersonalizedCard:
    card_id: str
    lang: str
    focus: str
    proposer: str
    lines: tuple[LineResult, ...]  # title first, then every fact in card order
    drafts: tuple[Draft, ...]
    fallback_reason: str | None = None


def _sources(card: InsightCard) -> list[tuple[int, str]]:
    return [(TITLE, card.title), *((i, f.text) for i, f in enumerate(card.facts))]


def personalize_card(
    match: Match,
    card: InsightCard,
    lang: str,
    focus: str,
    proposer: Personalizer,
    strict: bool = False,
) -> PersonalizedCard:
    """Draft, check every line, send rejected lines back once, keep what still fails."""
    names = known_names(match)
    teams = {HOME: match.meta.home.name, AWAY: match.meta.away.name}
    sources = _sources(card)
    drafts: list[Draft] = []
    accepted: dict[int, tuple[str, int]] = {}
    rejected: dict[int, list[str]] = {}
    fallback = None
    for attempt in (1, 2):
        feedback = [
            {"index": i, "text": drafts[-1].lines.get(i, ""), "reasons": rejected[i]}
            for i, _ in sources
            if i in rejected
        ]
        if attempt == 2 and not feedback:
            break
        try:
            draft = proposer.propose(card, lang, focus, teams, feedback)
        except Exception as exc:  # network, HTTP, format: the type only, never content
            if strict:
                raise
            fallback = f"{proposer.name} unavailable ({type(exc).__name__})"
            break
        drafts.append(draft)
        rejected = {}
        for i, source in sources:
            if i in accepted:
                continue
            reasons = check_line(source, draft.lines.get(i, ""), lang, names)
            if i not in draft.lines:
                reasons = ["missing from the reply"]
            if reasons:
                rejected[i] = reasons
            else:
                accepted[i] = (draft.lines[i].strip(), attempt)
    lines = []
    for i, source in sources:
        if i in accepted:
            text, attempt = accepted[i]
            lines.append(LineResult(i, source, text, VERIFIED, attempt))
        else:
            lines.append(LineResult(i, source, source, KEPT, 0, tuple(rejected.get(i, ()))))
    return PersonalizedCard(
        card.card_id, lang, focus, proposer.name, tuple(lines), tuple(drafts), fallback
    )


def personalize_cards(
    match: Match,
    card_set: CardSet,
    proposer: Personalizer,
    variants: Sequence[tuple[str, str]] = VARIANTS,
    strict: bool = False,
    log: Callable[[str], None] = lambda line: None,
) -> list[PersonalizedCard]:
    """Every card in every variant; a recorded run is replayed only where it has the card."""
    out = []
    for lang, focus in variants:
        for card in card_set.cards:
            if isinstance(proposer, RecordedPersonalizer) and not proposer.covers(
                card.card_id, lang, focus
            ):
                continue
            result = personalize_card(match, card, lang, focus, proposer, strict)
            kept = sum(line.status == KEPT for line in result.lines)
            ok = len(result.lines) - kept
            log(f"{variant_key(lang, focus)} {card.card_id}: {ok} ok, {kept} kept")
            out.append(result)
    return out


def personalized_record(results: Sequence[PersonalizedCard], label: str) -> dict[str, Any]:
    """The bundle's ``personalized`` section: variants -> card ID -> checked lines."""
    variants: dict[str, dict[str, Any]] = {}
    for r in results:
        v = variants.setdefault(
            variant_key(r.lang, r.focus), {"lang": r.lang, "focus": r.focus, "cards": {}}
        )
        v["cards"][r.card_id] = {
            "lines": [asdict(line) for line in r.lines],
            "fallback_reason": r.fallback_reason,
            "response_ids": [c["response_id"] for d in r.drafts for c in d.calls],
        }
    proposers = {r.proposer for r in results}
    return {"label": label, "proposers": sorted(proposers), "variants": variants}


# --- Evidence ----------------------------------------------------------------------------


def build_trace(
    match: Match, results: Sequence[PersonalizedCard], config: AzureOpenAIConfig
) -> dict[str, Any]:
    """Redacted evidence of a live run: drafts and call metadata, never the API key."""
    return {
        "schema": TRACE_SCHEMA,
        "recorded_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": "responses-v1",
        "deployment": config.deployment,
        "match_id": match.meta.match_id,
        "runs": [
            {
                "card_id": r.card_id,
                "lang": r.lang,
                "focus": r.focus,
                "attempts": [
                    {"lines": {str(k): v for k, v in d.lines.items()}, "calls": list(d.calls)}
                    for d in r.drafts
                ],
            }
            for r in results
        ],
    }


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def trace_problems(trace: dict[str, Any]) -> list[str]:
    problems = []
    if trace.get("schema") != TRACE_SCHEMA:
        problems.append(f"schema is not {TRACE_SCHEMA}")
    runs = trace.get("runs")
    if not isinstance(runs, list) or not runs:
        return [*problems, "no runs"]
    text = json.dumps(trace)
    if re.search(r"api[-_]?key|authorization|bearer", text, re.IGNORECASE):
        problems.append("trace mentions a key or auth header")
    for r in runs:
        where = f"{r.get('card_id')} {r.get('lang')}|{r.get('focus')}"
        if r.get("lang") not in LANGS or r.get("focus") not in FOCUSES:
            problems.append(f"{where}: unknown variant")
        attempts = r.get("attempts")
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= 2:
            problems.append(f"{where}: expected 1 or 2 attempts")
            continue
        for a in attempts:
            calls = a.get("calls")
            if not isinstance(calls, list) or len(calls) != 1:
                problems.append(f"{where}: an attempt must have exactly one call")
                continue
            call = calls[0]
            if not str(call.get("response_id", "")).startswith("resp"):
                problems.append(f"{where}: call without a response ID")
            usage = call.get("usage") or {}
            if not all(_positive_int(usage.get(k)) for k in ("input_tokens", "output_tokens")):
                problems.append(f"{where}: call without positive token usage")
    return problems

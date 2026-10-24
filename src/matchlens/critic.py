"""Critic agent: reviews the personalizer's rewrites for meaning, line by line.

The third agent in MatchLens. ``personalize.check_line`` proves that a rewrite keeps every
number, score order and name, but not that it keeps the *meaning*: the recorded run showed
rewrites that added details ("fierce tackle"), changed the meaning ("the lead changed hands"
became "confidence changed hands") or were not correct language. The critic is a Microsoft
Foundry deployment acting as a strict sports-desk editor. For one card it receives each
English fact and its rewrite (no IDs, no other context), and returns a structured verdict
per line: faithful or not, the kind of problem, and a short reason.

In the personalizer loop (``personalize.personalize_card``) the critic runs after the
deterministic checks, on the lines that passed them. A line it rejects goes back to the
personalizer with the critic's reason; a line still rejected keeps its English fact. A
missing verdict or a failed critic call rejects the line: when in doubt, the page shows
the verified English fact. The critic is a model, so its verdicts are labelled as such;
``critic_eval`` measures it against a human editorial review of a recorded run.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from .agent import _call_record, _incomplete_reason, post_with_retries
from .narrative import AzureOpenAIConfig, ResponseFormatError, Transport, extract_output_text

CRITIC = "foundry_critic"
RECORDED_CRITIC = "recorded_foundry_critic"
ISSUES = ("none", "added_detail", "changed_meaning", "overstatement", "language", "other")
MAX_OUTPUT_TOKENS = 600
EVAL_SCHEMA = "matchlens.critic-eval/v1"


@dataclass(frozen=True)
class Verdict:
    index: int
    faithful: bool
    issue: str  # one of ISSUES; "none" when faithful
    reason: str

    def rejection(self) -> str:
        return f"critic: {self.issue.replace('_', ' ')}: {self.reason}".strip().rstrip(":")


@dataclass(frozen=True)
class CriticReview:
    verdicts: dict[int, Verdict]
    calls: tuple[dict[str, Any], ...] = ()


class Critic(Protocol):
    name: str
    label: str

    def review(
        self,
        key: tuple[str, str, str, int],  # card_id, lang, focus, attempt (for replays)
        items: Sequence[dict[str, Any]],  # {"index", "source", "text"}
        language: str,
        audience: str,
    ) -> CriticReview: ...


OUTPUT_FORMAT = {
    "type": "json_schema",
    "name": "critic_verdicts",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdicts"],
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["index", "faithful", "issue", "reason"],
                    "properties": {
                        "index": {"type": "integer"},
                        "faithful": {"type": "boolean"},
                        "issue": {"type": "string", "enum": list(ISSUES)},
                        "reason": {"type": "string"},
                    },
                },
            }
        },
    },
}

INSTRUCTIONS = (
    "You are a strict sports-desk editor checking rewrites of verified football facts. For "
    "every line you get the English source fact and a rewrite in the target language for a "
    "given audience. Judge only faithfulness. Mark a rewrite NOT faithful if it: adds any "
    "detail, adjective about the play, cause, event or claim that the source does not state "
    "(for example calling a tackle fierce or passes quick); changes the meaning (who did "
    "what, what happened, a saved shot becoming a missed one, a level score becoming a "
    "drawn match); overstates or understates the data (for example 'dominated' for a small "
    "difference); or is not correct, natural language for the target language (wrong words, "
    "typos, other-language letters, literal calques). Tone and point of view are allowed: "
    "joy, disappointment, 'crucial', exclamation marks. Numbers, scores and names were "
    "already checked by a program; do not re-check them. Return one verdict per line with "
    "the same index, an issue from the list, and a reason of at most 15 words in English."
)


def critic_brief(items: Sequence[dict[str, Any]], language: str, audience: str) -> str:
    return json.dumps(
        {
            "language": language,
            "audience": audience,
            "lines": [
                {"index": it["index"], "source": it["source"], "rewrite": it["text"]}
                for it in items
            ],
        },
        ensure_ascii=False,
    )


def parse_verdicts(text: str) -> dict[int, Verdict]:
    try:
        doc = json.loads(text)
    except ValueError:
        raise ResponseFormatError("critic reply is not JSON") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("verdicts"), list):
        raise ResponseFormatError("critic reply has no verdicts")
    out: dict[int, Verdict] = {}
    for v in doc["verdicts"]:
        if not isinstance(v, dict) or not isinstance(v.get("index"), int):
            continue
        faithful = v.get("faithful")
        issue = v.get("issue") if v.get("issue") in ISSUES else "other"
        if faithful is False and issue == "none":
            issue = "other"
        reason = str(v.get("reason", ""))[:200].strip()
        if isinstance(faithful, bool):
            if faithful and issue != "none":  # a faithful line has no issue: take it as unsure
                faithful, reason = False, reason or f"marked faithful but flagged {issue}"
            out.setdefault(v["index"], Verdict(v["index"], faithful, issue, reason))
    return out


class FoundryCritic:
    """One Responses API (v1) call per card and attempt; structured JSON, no tools."""

    name = CRITIC

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
            f"Microsoft Foundry critic (deployment {config.deployment}, Responses API v1): a "
            "second model reviewing each rewrite for meaning."
        )

    def review(self, key, items, language, audience) -> CriticReview:
        body = {
            "model": self.config.deployment,
            "input": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": critic_brief(items, language, audience)},
            ],
            "text": {"format": OUTPUT_FORMAT},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        response, retries = post_with_retries(
            self.config, body, self.transport, self.timeout_s, self.sleep, self.log
        )
        if _incomplete_reason(response) == "max_output_tokens":
            raise ResponseFormatError("critic reply was cut off")
        verdicts = parse_verdicts(extract_output_text(response))
        return CriticReview(verdicts, (_call_record(response, [], retries),))


def verdict_record(v: Verdict) -> dict[str, Any]:
    return {"faithful": v.faithful, "issue": v.issue, "reason": v.reason}


def verdict_from_record(index: int, rec: dict[str, Any]) -> Verdict:
    return Verdict(index, bool(rec["faithful"]), str(rec["issue"]), str(rec["reason"]))


class RecordedCritic:
    """Replays the critic's recorded verdicts, keyed by card, variant and attempt."""

    name = RECORDED_CRITIC

    def __init__(self, reviews: dict[tuple[str, str, str, int], dict[str, Any]], label: str):
        self._reviews = reviews
        self.label = label

    def review(self, key, items, language, audience) -> CriticReview:
        rec = self._reviews.get(key)
        if rec is None:
            raise KeyError(f"no recorded critic review for {key}")
        verdicts = {int(k): verdict_from_record(int(k), v) for k, v in rec["verdicts"].items()}
        return CriticReview(verdicts, tuple(rec["calls"]))


# --- Measuring the critic against a human editorial review ----------------------------------


def critic_eval(
    items_by_card: Sequence[tuple[tuple[str, str, str], list[dict[str, Any]], str, str]],
    critic: Critic,
    config: AzureOpenAIConfig,
    trace_source: str,
    review_source: str,
    log: Callable[[str], None] = lambda line: None,
) -> dict[str, Any]:
    """Run ``critic`` once per card variant over lines a human already judged; record it.

    ``items_by_card`` holds ``((card_id, lang, focus), items, language, audience)``; each item
    is ``{"index", "source", "text"}``. The result is evidence: the verdicts, the texts they
    judged, and the call metadata, never the key.
    """
    runs = []
    for (card_id, lang, focus), items, language, audience in items_by_card:
        rev = critic.review((card_id, lang, focus, 1), items, language, audience)
        bad = sum(not v.faithful for v in rev.verdicts.values())
        log(f"{lang}|{focus} {card_id}: {len(items)} line(s), critic rejects {bad}")
        runs.append(
            {
                "card_id": card_id,
                "lang": lang,
                "focus": focus,
                "lines": {str(it["index"]): it["text"] for it in items},
                "verdicts": {str(i): verdict_record(v) for i, v in rev.verdicts.items()},
                "calls": list(rev.calls),
            }
        )
    return {
        "schema": EVAL_SCHEMA,
        "recorded_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": "responses-v1",
        "deployment": config.deployment,
        "trace": trace_source,
        "review": review_source,
        "runs": runs,
    }


def eval_metrics(eval_doc: dict[str, Any], human_rejections: set[tuple]) -> dict[str, Any]:
    """Compare the critic with the human labels: a human rejection is a positive.

    ``human_rejections`` holds ``(card_id, lang, focus, index, text)``. A line the critic
    gave no verdict counts as a critic rejection, as it does in the pipeline.
    """
    tp = fp = fn = tn = 0
    misses, false_alarms = [], []
    for run in eval_doc["runs"]:
        for k, text in run["lines"].items():
            key = (run["card_id"], run["lang"], run["focus"], int(k), text)
            verdict = run["verdicts"].get(k)
            critic_rejects = verdict is None or not verdict["faithful"]
            human_rejects = key in human_rejections
            if critic_rejects and human_rejects:
                tp += 1
            elif critic_rejects:
                fp += 1
                false_alarms.append({"line": key[:4], "text": text, "critic": verdict})
            elif human_rejects:
                fn += 1
                misses.append({"line": key[:4], "text": text})
            else:
                tn += 1
    return {
        "lines": tp + fp + fn + tn,
        "human_rejections": tp + fn,
        "caught": tp,
        "missed": fn,
        "false_alarms": fp,
        "agreed_faithful": tn,
        "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "precision": round(tp / (tp + fp), 3) if tp + fp else None,
        "misses": misses,
        "false_alarm_lines": false_alarms,
    }

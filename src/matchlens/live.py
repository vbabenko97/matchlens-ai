"""Live claim check of one demo card through Microsoft Foundry, behind fuses.

This is what the public demo's "Check this card live" button calls. The request carries a
card ID and nothing else: the model request is built here, from the demo match and the
same bounded agent as ``agent-review``, so the endpoint cannot be used as a general proxy
to the model. Every claim is still decided by the deterministic validator.

Fuses, in the order they apply:

1. Only analyst card IDs of the demo match are accepted (404 otherwise).
2. One live run at a time per instance; a second request gets "busy" instead of queuing,
   and costs no quota.
3. A per-client limit (``PER_CLIENT_LIMIT`` checks per ``PER_CLIENT_WINDOW_S``), in process
   memory. It is a courtesy limit only: the client address comes from headers a script can
   forge, and each instance counts on its own.
4. The daily limit (``MATCHLENS_LIVE_DAILY_LIMIT``, default ``DAILY_LIMIT``, UTC day) is the
   spend cap. It is a shared, durable count (``quota.TableCounter``), reserved before any
   model call; a reservation is never returned, even when the call fails. If the count
   cannot be read or updated, no live call is made.
5. One deadline (``DEADLINE_S``) over the whole request: the quota reservation, every model
   call and every rate-limit wait. Each store or model call gets at most the time left.

Whenever the live call is not made or does not finish, the reply says why and tells the
page to keep showing the recorded run, which it labels as recorded. A fallback is never
presented as a live response.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from .agent import FoundryAgentProposer, review_card, review_record
from .cards import InsightCard, build_cards
from .generator import generate_match
from .narrative import (
    ANALYST,
    AzureOpenAIConfig,
    OfflineTemplateProvider,
    Transport,
    _urllib_transport,
)
from .quota import (
    COUNTER_URL_ENV,
    CounterError,
    CounterTimeout,
    DailyCounter,
    MemoryCounter,
    TableCounter,
    TimeLeft,
)
from .replay import DEFAULT_DEMO_SEED as DEMO_SEED  # the match the public demo replays
from .schema import Match

LIVE_SCHEMA = "matchlens.live-check/v1"
LIVE_PATH = "api/live-check"
PER_CLIENT_LIMIT = 3
PER_CLIENT_WINDOW_S = 600.0
DAILY_LIMIT = 100  # card checks per UTC day; each is at most a dozen small model calls
DAILY_LIMIT_ENV = "MATCHLENS_LIVE_DAILY_LIMIT"
DEADLINE_S = 30.0  # under the HTTP timeout of a serverless front end
MAX_BODY_BYTES = 1024
# Where the client address may arrive, first match wins. Without any, every visitor shares
# one per-client bucket (stricter, never looser).
CLIENT_HEADERS = ("x-forwarded-for", "x-azure-clientip", "x-client-ip")

# Fallback reasons (machine-readable; the page words them).
NOT_CONFIGURED = "not_configured"
CLIENT_LIMIT = "client_limit"
DAILY_LIMIT_REACHED = "daily_limit"
COUNTER_UNAVAILABLE = "counter_unavailable"
BUSY = "busy"
DEADLINE = "deadline"
UNAVAILABLE = "foundry_unavailable"


class DeadlineExceeded(RuntimeError):
    """The live check ran out of time. Not a network error, so the agent does not retry it."""


@dataclass
class Budget:
    """The per-client courtesy limit (memory) and the shared daily cap (``counter``).

    ``counter=None`` means no shared quota store is configured: live checks are refused.
    """

    per_client: int = PER_CLIENT_LIMIT
    window_s: float = PER_CLIENT_WINDOW_S
    daily: int = DAILY_LIMIT
    counter: DailyCounter | None = field(default_factory=MemoryCounter)
    now: Callable[[], float] = time.time
    _clients: dict[str, list[float]] = field(default_factory=dict)
    _remaining: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, client: str, time_left: TimeLeft | None = None) -> str | None:
        """Reserve one check for ``client``; return a fallback reason instead of reserving.

        ``time_left`` is the request's deadline: a slow quota store ends the check with
        ``DEADLINE`` before any model call, instead of eating the model's time.
        """
        with self._lock:
            if self.counter is None:
                return NOT_CONFIGURED
            t = self.now()
            recent = [s for s in self._clients.get(client, []) if t - s < self.window_s]
            self._clients[client] = recent
            if len(recent) >= self.per_client:
                return CLIENT_LIMIT
            day = datetime.fromtimestamp(t, UTC).date().isoformat()
            try:
                used = self.counter.reserve(day, self.daily, time_left)
            except CounterTimeout:
                return DEADLINE
            except CounterError:
                return COUNTER_UNAVAILABLE
            if used is None:
                self._remaining = 0
                return DAILY_LIMIT_REACHED
            self._remaining = self.daily - used
            recent.append(t)
            return None

    def remaining_today(self) -> int | None:
        """As of this instance's last reservation (other instances may have used more)."""
        return self._remaining


def daily_limit_from_env(env: Mapping[str, str] | None = None) -> int:
    raw = (os.environ if env is None else env).get(DAILY_LIMIT_ENV, "").strip()
    if not raw:
        return DAILY_LIMIT
    value = int(raw)  # a malformed limit fails loudly at start-up, never silently unlimited
    if value < 0:
        raise ValueError(f"{DAILY_LIMIT_ENV} must be >= 0")
    return value


def client_key(forwarded_for: str | None, fallback: str = "") -> str:
    """A short hash of the client address (never stored or logged in the clear).

    Behind Azure's front end the client is the first ``X-Forwarded-For`` entry. A client can
    forge that header to dodge the per-client limit, but not the daily one.
    """
    first = (forwarded_for or "").split(",")[0].strip() or fallback
    if first.count(":") == 1:  # "ip:port" (IPv4); IPv6 addresses have several colons
        first = first.split(":")[0]
    return hashlib.sha256(first.encode()).hexdigest()[:16]


class _Deadline:
    """Wraps the transport and the rate-limit sleep so no call or wait crosses the deadline."""

    def __init__(self, transport: Transport, sleep: Callable[[float], None], clock, end: float):
        self._transport, self._sleep, self._clock = transport, sleep, clock
        self._end = end  # the request's deadline, on ``clock``

    def remaining(self) -> float:
        return self._end - self._clock()

    def transport(self, url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
        left = self.remaining()
        if left <= 1.0:
            raise DeadlineExceeded("no time left for another model call")
        return self._transport(url, headers, body, min(timeout, left))

    def sleep(self, seconds: float) -> None:
        if seconds >= self.remaining() - 1.0:
            raise DeadlineExceeded("rate-limit wait would cross the deadline")
        self._sleep(seconds)


class LiveChecker:
    def __init__(
        self,
        config: AzureOpenAIConfig | None,
        budget: Budget | None = None,
        match: Match | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        deadline_s: float = DEADLINE_S,
    ):
        self.config = config
        self.budget = budget or Budget()
        self.match = match or generate_match(DEMO_SEED)
        cards = build_cards(self.match, ANALYST, OfflineTemplateProvider()).cards
        self.cards: dict[str, InsightCard] = {c.card_id: c for c in cards}
        self._transport = transport or _urllib_transport
        self._sleep, self._clock, self._deadline_s = sleep, clock, deadline_s
        self._busy = threading.Lock()

    def _fallback(self, card_id: str, reason: str, status: int) -> tuple[int, dict[str, Any]]:
        return status, {
            "schema": LIVE_SCHEMA,
            "status": "fallback",
            "card_id": card_id,
            "reason": reason,
            "live": False,
            "message": "No live call was made or completed; the recorded run stays on the page.",
        }

    def check(self, card_id: object, client: str) -> tuple[int, dict[str, Any]]:
        """Return ``(http_status, reply)`` for one card. Never raises for a bad request."""
        if not isinstance(card_id, str) or card_id not in self.cards:
            return 404, {"schema": LIVE_SCHEMA, "status": "unknown_card", "live": False}
        if self.config is None:
            return self._fallback(card_id, NOT_CONFIGURED, 503)
        if not self._busy.acquire(blocking=False):
            return self._fallback(card_id, BUSY, 503)  # turned away before it costs any quota
        try:
            # One deadline for the whole request: the quota reservation counts against it.
            started = self._clock()
            end = started + self._deadline_s
            over = self.budget.take(client, lambda: end - self._clock())
            if over == DEADLINE:
                return self._fallback(card_id, DEADLINE, 504)
            if over is not None:
                return self._fallback(card_id, over, 429)
            return self._run(card_id, started, end)
        finally:
            self._busy.release()

    def _run(self, card_id: str, started: float, end: float) -> tuple[int, dict[str, Any]]:
        assert self.config is not None
        deadline = _Deadline(self._transport, self._sleep, self._clock, end)
        proposer = FoundryAgentProposer(
            self.config, deadline.transport, timeout_s=self._deadline_s, sleep=deadline.sleep
        )
        try:
            review = review_card(self.match, self.cards[card_id], proposer, strict=True)
        except DeadlineExceeded:
            return self._fallback(card_id, DEADLINE, 504)
        except Exception as exc:  # network, HTTP, format: report the type only, never content
            status, reply = self._fallback(card_id, UNAVAILABLE, 502)
            return status, {**reply, "error": type(exc).__name__}
        checked_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        review = replace(
            review,
            proposer_label=(
                f"Live Microsoft Foundry agent call ({checked_at}, deployment "
                f"{self.config.deployment}, Responses API v1), made when you pressed the "
                "button; every claim is checked by the deterministic validator."
            ),
        )
        calls = [c for a in review.attempts for c in a.calls]
        return 200, {
            "schema": LIVE_SCHEMA,
            "status": "live",
            "live": True,
            "card_id": card_id,
            "checked_at_utc": checked_at,
            "latency_ms": round(1000 * (self._clock() - started)),
            "deployment": self.config.deployment,
            "model_calls": len(calls),
            "usage": {
                k: sum(int(c["usage"].get(k) or 0) for c in calls)
                for k in ("input_tokens", "output_tokens")
            },
            "review": review_record(review),
            "metrics": {m.metric_id: m.to_record() for m in review.metrics},
            "remaining_today": self.budget.remaining_today(),
        }


def handle(
    checker: LiveChecker, method: str, headers: Mapping[str, str], body: bytes, peer: str = ""
) -> tuple[int, dict[str, Any]]:
    """Host-neutral HTTP handling (Azure Functions adapter and the local server share it).

    POST with ``Content-Type: application/json`` only: a cross-site page cannot send that
    without a CORS preflight, which this endpoint never grants.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    if method != "POST":
        return 405, {"schema": LIVE_SCHEMA, "status": "method_not_allowed", "live": False}
    if lower.get("content-type", "").split(";")[0].strip() != "application/json":
        return 415, {"schema": LIVE_SCHEMA, "status": "unsupported_media_type", "live": False}
    if len(body) > MAX_BODY_BYTES:
        return 413, {"schema": LIVE_SCHEMA, "status": "too_large", "live": False}
    try:
        doc = json.loads(body)
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or set(doc) != {"card_id"}:
        return 400, {"schema": LIVE_SCHEMA, "status": "bad_request", "live": False}
    forwarded = next((lower[h] for h in CLIENT_HEADERS if lower.get(h, "").strip()), None)
    return checker.check(doc["card_id"], client_key(forwarded, peer))


def checker_from_env(
    env: Mapping[str, str] | None = None, memory_quota: bool = False
) -> LiveChecker:
    """Settings from the environment (app settings on Azure).

    Live calls need the Azure OpenAI settings and the shared quota store
    (``MATCHLENS_LIVE_COUNTER_URL``); without either, every check falls back. Only a local
    server run by the key's owner may use ``memory_quota`` instead of the store.
    """
    env = os.environ if env is None else env
    url = env.get(COUNTER_URL_ENV, "").strip()
    counter: DailyCounter | None = TableCounter(url) if url else None
    if counter is None and memory_quota:
        counter = MemoryCounter()
    budget = Budget(daily=daily_limit_from_env(env), counter=counter)
    return LiveChecker(AzureOpenAIConfig.from_env(env), budget)

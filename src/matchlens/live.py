"""Live claim check of one demo card through Microsoft Foundry, behind fuses.

This is what the public demo's "Check this card live" button calls. The request carries a
card ID and nothing else: the model request is built here, from the demo match and the
same bounded agent as ``agent-review``, so the endpoint cannot be used as a general proxy
to the model. Every claim is still decided by the deterministic validator.

Fuses, in the order they apply:

1. Only analyst card IDs of the demo match are accepted (404 otherwise).
2. One live run at a time per instance; a second request gets "busy" instead of queuing,
   and costs no quota.
3. A per-client limit (``PER_CLIENT_LIMIT`` checks per ``PER_CLIENT_WINDOW_S``) and a daily
   limit (``MATCHLENS_LIVE_DAILY_LIMIT``, default ``DAILY_LIMIT``, UTC day). Both are held in
   process memory, so they are per instance and reset on a cold start: the hard ceiling is
   the Azure side (the deployment's tokens-per-minute limit and the subscription budget).
4. A deadline (``DEADLINE_S``) over all model calls, rate-limit waits included.

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
BUSY = "busy"
DEADLINE = "deadline"
UNAVAILABLE = "foundry_unavailable"


class DeadlineExceeded(RuntimeError):
    """The live check ran out of time. Not a network error, so the agent does not retry it."""


@dataclass
class Budget:
    """Per-client and daily counters (in memory, per instance). A check counts when it starts."""

    per_client: int = PER_CLIENT_LIMIT
    window_s: float = PER_CLIENT_WINDOW_S
    daily: int = DAILY_LIMIT
    now: Callable[[], float] = time.time
    _clients: dict[str, list[float]] = field(default_factory=dict)
    _day: str = ""
    _used: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def take(self, client: str) -> str | None:
        """Record one check for ``client``; return a fallback reason instead when over a limit."""
        with self._lock:
            t = self.now()
            day = datetime.fromtimestamp(t, UTC).date().isoformat()
            if day != self._day:
                self._day, self._used = day, 0
            recent = [s for s in self._clients.get(client, []) if t - s < self.window_s]
            if self._used >= self.daily:
                return DAILY_LIMIT_REACHED
            if len(recent) >= self.per_client:
                self._clients[client] = recent
                return CLIENT_LIMIT
            self._clients[client] = [*recent, t]
            self._used += 1
            return None

    def remaining_today(self) -> int:
        with self._lock:
            return max(0, self.daily - self._used)


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

    def __init__(self, transport: Transport, sleep: Callable[[float], None], clock, limit_s):
        self._transport, self._sleep, self._clock = transport, sleep, clock
        self._end = clock() + limit_s

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
            over = self.budget.take(client)
            if over is not None:
                return self._fallback(card_id, over, 429)
            return self._run(card_id)
        finally:
            self._busy.release()

    def _run(self, card_id: str) -> tuple[int, dict[str, Any]]:
        assert self.config is not None
        started = self._clock()
        deadline = _Deadline(self._transport, self._sleep, self._clock, self._deadline_s)
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


def checker_from_env(env: Mapping[str, str] | None = None) -> LiveChecker:
    """Azure settings from the environment (app settings on Azure); none -> fallback only."""
    return LiveChecker(AzureOpenAIConfig.from_env(env), Budget(daily=daily_limit_from_env(env)))

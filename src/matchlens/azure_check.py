"""Strict, single-call verification of a live Azure OpenAI / Microsoft Foundry deployment.

Unlike ``narrate()``, this never falls back: it makes exactly one Responses API (v1) request
and reports either redacted evidence of a real response or the failure. A response counts
only when its status is ``completed`` and it carries a response ID, a model name, positive
input/output token usage and non-empty ``output_text``. The returned
wording is still unverified prose; the check proves the integration path, not the text.
Mocked transports in tests exercise the formatting only and are not integration evidence.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import urlparse

from .cards import build_cards
from .generator import generate_match
from .narrative import (
    AI_LABEL,
    AI_UNVERIFIED,
    CASUAL,
    ENV_VARS,
    AzureOpenAIConfig,
    AzureOpenAIProvider,
    NarrativeRequest,
    ResponseFormatError,
    Transport,
    extract_output_text,
)
from .replay import DEFAULT_DEMO_SEED


def _probe_request() -> NarrativeRequest:
    card = build_cards(generate_match(DEFAULT_DEMO_SEED), CASUAL, limit=1).cards[0]
    return NarrativeRequest(mode=CASUAL, title=card.title, facts=tuple(f.text for f in card.facts))


def _nonempty_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def validate_response(response: dict) -> list[str]:
    """Return the evidence problems of a parsed Responses API reply (empty when valid)."""
    problems = []
    if response.get("status") != "completed":
        problems.append("status is not 'completed'")
    if not _nonempty_str(response.get("id")):
        problems.append("response id missing")
    if not _nonempty_str(response.get("model")):
        problems.append("model missing")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        problems.append("usage missing")
    else:
        for key in ("input_tokens", "output_tokens"):
            if not _positive_int(usage.get(key)):
                problems.append(f"usage.{key} is not a positive integer")
    return problems


def _safe_status(value: object) -> str | None:
    """Echo the status only when it is a short plain word (never arbitrary content)."""
    if isinstance(value, str) and value.isidentifier() and len(value) <= 32:
        return value
    return None


def run_check(
    env: Mapping[str, str], transport: Transport | None = None, timeout_s: float = 30.0
) -> tuple[int, dict]:
    """Return ``(exit_code, report)``; the report never contains the API key."""
    missing = [k for k in ENV_VARS if not env.get(k, "").strip()]
    if missing:
        return 2, {"ok": False, "error": "missing environment variables", "missing": missing}
    try:
        config = AzureOpenAIConfig.from_env(env)
    except ValueError as exc:
        return 2, {"ok": False, "error": str(exc)}
    assert config is not None
    provider = AzureOpenAIProvider(config, transport=transport, timeout_s=timeout_s)
    request = _probe_request()
    base = {
        "checked_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api": "responses-v1",
        "endpoint_host": urlparse(config.endpoint).hostname,
        "request_path": urlparse(provider.url()).path,
        "deployment": config.deployment,
    }
    started = time.monotonic()
    try:
        response = provider.complete(request)
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):  # body may be absent or not JSON
            detail = json.loads(exc.read()).get("error", {}).get("code", "")
        return 1, {**base, "ok": False, "error": f"HTTP {exc.code}", "error_code": detail}
    except ResponseFormatError as exc:  # fixed message, never response content
        return 1, {**base, "ok": False, "error": "ResponseFormatError", "problems": [str(exc)]}
    except Exception as exc:  # never include exception text that could echo headers
        return 1, {**base, "ok": False, "error": type(exc).__name__}
    latency_ms = round((time.monotonic() - started) * 1000)
    problems = validate_response(response)
    text = None
    if response.get("status") == "completed":  # otherwise already reported above
        try:
            text = extract_output_text(response)
        except ResponseFormatError as exc:
            problems.append(str(exc))
    if problems:
        return 1, {
            **base,
            "ok": False,
            "error": "InvalidResponse",
            "response_status": _safe_status(response.get("status")),
            "problems": problems,
        }
    usage = response["usage"]
    return 0, {
        **base,
        "ok": True,
        "http_status": 200,
        "latency_ms": latency_ms,
        "response_status": response["status"],
        "response_id": response["id"],
        "model": response["model"],
        "usage": {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage.get("total_tokens"),
        },
        "store": False,
        "prompt_facts": list(request.facts),
        "completion": text,
        "completion_status": AI_UNVERIFIED,
        "completion_label": AI_LABEL,
        "note": "Proves one live Azure inference call; the completion text itself is not verified.",
    }

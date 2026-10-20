"""Strict, single-call verification of a live Azure OpenAI / Microsoft Foundry deployment.

Unlike ``narrate()``, this never falls back: it makes exactly one chat-completions request
and reports either redacted evidence of a real response or the failure. The returned
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
    Transport,
)
from .replay import DEFAULT_DEMO_SEED


def _probe_request() -> NarrativeRequest:
    card = build_cards(generate_match(DEFAULT_DEMO_SEED), CASUAL, limit=1).cards[0]
    return NarrativeRequest(mode=CASUAL, title=card.title, facts=tuple(f.text for f in card.facts))


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
        "endpoint_host": urlparse(config.endpoint).hostname,
        "deployment": config.deployment,
        "api_version": config.api_version,
    }
    started = time.monotonic()
    try:
        response = provider.complete(request)
        text = response["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty completion")
    except urllib.error.HTTPError as exc:
        detail = ""
        with contextlib.suppress(Exception):  # body may be absent or not JSON
            detail = json.loads(exc.read()).get("error", {}).get("code", "")
        return 1, {**base, "ok": False, "error": f"HTTP {exc.code}", "error_code": detail}
    except Exception as exc:  # never include exception text that could echo headers
        return 1, {**base, "ok": False, "error": type(exc).__name__}
    latency_ms = round((time.monotonic() - started) * 1000)
    return 0, {
        **base,
        "ok": True,
        "http_status": 200,
        "latency_ms": latency_ms,
        "response_id": response.get("id"),
        "model": response.get("model"),
        "usage": response.get("usage"),
        "prompt_facts": list(request.facts),
        "completion": text.strip(),
        "completion_status": AI_UNVERIFIED,
        "completion_label": AI_LABEL,
        "note": "Proves one live Azure inference call; the completion text itself is not verified.",
    }

"""Narrative wording providers: Azure OpenAI / Microsoft Foundry with an offline fallback.

Providers only *reword* facts that were already verified and template-rendered. Their
output is presentation, never evidence:
- ``OfflineTemplateProvider`` is deterministic and labelled as an offline fallback.
- ``AzureOpenAIProvider`` output is always labelled unverified AI wording, and numbers that
  do not occur in the verified facts are flagged (a heuristic guard, not verification).
Configuration comes only from environment variables (see ``.env.example``).
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

CASUAL = "casual"
ANALYST = "analyst"
MODES = (CASUAL, ANALYST)

TEMPLATE = "deterministic_template"
AI_UNVERIFIED = "unverified_ai_wording"
OFFLINE_LABEL = "Offline fallback: deterministic template wording — not an Azure response."
AI_LABEL = (
    "AI-generated wording (Azure OpenAI) — not verified; the verified facts on this card "
    "are authoritative."
)
ENV_VARS = (
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_API_VERSION",
)


@dataclass(frozen=True, slots=True)
class NarrativeRequest:
    mode: str
    title: str
    facts: tuple[str, ...]  # verified, template-rendered fact texts
    max_words: int = 60


@dataclass(frozen=True, slots=True)
class Narrative:
    text: str
    provider: str
    status: str  # TEMPLATE or AI_UNVERIFIED; never "verified"
    label: str
    fallback_reason: str | None = None
    warnings: tuple[str, ...] = field(default=())


class NarrativeProvider(Protocol):
    name: str

    def render(self, request: NarrativeRequest) -> str: ...


_EVIDENCE = re.compile(r"\s*\[evidence:[^\]]*\]")
_EVENT_LINE = re.compile(r"^\d+' ")


class OfflineTemplateProvider:
    """Deterministic wording assembled from the verified facts themselves.

    Casual: the facts as one plain paragraph. Analyst: the summary facts (per-event lines
    are left to the evidence list) without evidence brackets.
    """

    name = "offline_template"

    def render(self, request: NarrativeRequest) -> str:
        if request.mode == CASUAL:
            return " ".join(request.facts)
        summary = [_EVIDENCE.sub("", f) for f in request.facts if not _EVENT_LINE.match(f)]
        return f"{request.title}: " + " ".join(s if s.endswith(".") else s + "." for s in summary)


Transport = Callable[[str, dict[str, str], bytes, float], bytes]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the api-key header is never re-sent to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with _OPENER.open(req, timeout=timeout) as resp:  # https enforced by the config
        return resp.read()


@dataclass(frozen=True)
class AzureOpenAIConfig:
    endpoint: str
    api_key: str = field(repr=False)
    deployment: str
    api_version: str

    def __post_init__(self) -> None:
        if not self.endpoint.startswith("https://"):
            raise ValueError("AZURE_OPENAI_ENDPOINT must be an https:// URL")
        if not all((self.api_key, self.deployment, self.api_version)):
            raise ValueError("Azure OpenAI configuration is incomplete")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AzureOpenAIConfig | None:
        """Return a config when every variable is set, else ``None`` (offline mode)."""
        env = os.environ if env is None else env
        values = [env.get(k, "").strip() for k in ENV_VARS]
        if not all(values):
            return None
        return cls(*values)


_SYSTEM_PROMPT = {
    CASUAL: (
        "You write one or two short, lively sentences for casual football fans watching a "
        "synthetic (fictional) match. Plain language, no statistics jargon."
    ),
    ANALYST: (
        "You write a concise, neutral note for a football analyst about a synthetic "
        "(fictional) match. Keep technical terms; keep any metric labels."
    ),
}
_RULES = (
    "Use ONLY the facts provided. Do not add events, numbers, names or causes that are not "
    "in the facts. Describe any xG figure as a synthetic heuristic. Never invent evidence IDs."
)


class AzureOpenAIProvider:
    """Chat-completions call to an Azure OpenAI / Microsoft Foundry deployment."""

    name = "azure_openai"

    def __init__(
        self,
        config: AzureOpenAIConfig,
        transport: Transport | None = None,
        timeout_s: float = 15.0,
    ):
        self.config = config
        self.transport = transport
        self.timeout_s = timeout_s

    def url(self) -> str:
        c = self.config
        return (
            f"{c.endpoint.rstrip('/')}/openai/deployments/{c.deployment}"
            f"/chat/completions?api-version={c.api_version}"
        )

    def complete(self, request: NarrativeRequest) -> dict:
        """One chat-completions call; returns the parsed JSON response (raises on failure)."""
        facts = "\n".join(f"- {f}" for f in request.facts)
        body = {
            "messages": [
                {"role": "system", "content": f"{_SYSTEM_PROMPT[request.mode]} {_RULES}"},
                {
                    "role": "user",
                    "content": (
                        f"Title: {request.title}\nFacts:\n{facts}\n"
                        f"Write at most {request.max_words} words."
                    ),
                },
            ],
            "temperature": 0.2,
            "max_tokens": 200,
        }
        headers = {"Content-Type": "application/json", "api-key": self.config.api_key}
        transport = self.transport or _urllib_transport
        raw = transport(self.url(), headers, json.dumps(body).encode(), self.timeout_s)
        return json.loads(raw)

    def render(self, request: NarrativeRequest) -> str:
        text = self.complete(request)["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("empty completion")
        return text.strip()


def provider_from_env(env: Mapping[str, str] | None = None) -> NarrativeProvider:
    config = AzureOpenAIConfig.from_env(env)
    return AzureOpenAIProvider(config) if config else OfflineTemplateProvider()


_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def unsupported_numbers(text: str, request: NarrativeRequest) -> list[str]:
    allowed = set(_NUMBER.findall(" ".join([request.title, *request.facts])))
    return sorted({n for n in _NUMBER.findall(text) if n not in allowed})


def narrate(request: NarrativeRequest, provider: NarrativeProvider) -> Narrative:
    """Word a card; any provider failure falls back to the offline template."""
    offline = OfflineTemplateProvider()
    if isinstance(provider, OfflineTemplateProvider):
        return Narrative(offline.render(request), offline.name, TEMPLATE, OFFLINE_LABEL)
    try:
        text = provider.render(request)
    except Exception as exc:  # network, HTTP, parsing: never leak details such as keys
        return Narrative(
            offline.render(request),
            offline.name,
            TEMPLATE,
            OFFLINE_LABEL,
            fallback_reason=f"{provider.name} unavailable ({type(exc).__name__})",
        )
    warnings = tuple(
        f"number {n} is not in the verified facts" for n in unsupported_numbers(text, request)
    )
    return Narrative(text, provider.name, AI_UNVERIFIED, AI_LABEL, warnings=warnings)

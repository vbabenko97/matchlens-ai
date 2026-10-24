import json
import urllib.error
import urllib.request

import pytest

from matchlens.claims import VERIFIED
from matchlens.narrative import (
    AI_LABEL,
    AI_UNVERIFIED,
    ANALYST,
    CASUAL,
    OFFLINE_LABEL,
    TEMPLATE,
    AzureOpenAIConfig,
    AzureOpenAIProvider,
    NarrativeRequest,
    OfflineTemplateProvider,
    ResponseFormatError,
    extract_output_text,
    narrate,
    provider_from_env,
)

KEY = "test-key-not-real-0123456789"
ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://example-resource.services.ai.azure.com/",
    "AZURE_OPENAI_API_KEY": KEY,
    "AZURE_OPENAI_DEPLOYMENT": "demo-deployment",
}
RESPONSES_URL = "https://example-resource.services.ai.azure.com/openai/v1/responses"
REQUEST = NarrativeRequest(
    mode=CASUAL,
    title="GOAL! Northbridge Rovers score",
    facts=("Ari Vale scores for Northbridge Rovers in minute 1.", "That puts them ahead, 1-0."),
)


def responses_reply(text="Hello", **overrides):
    """A minimal Responses API (v1) payload, shaped like a real completed reply."""
    payload = {
        "id": "resp_fake_0123",
        "object": "response",
        "status": "completed",
        "model": "gpt-4.1-mini-2025-04-14",
        "output": [
            {
                "type": "message",
                "id": "msg_fake_0123",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 17, "output_tokens": 27, "total_tokens": 44},
    }
    payload.update(overrides)
    return payload


class FakeTransport:
    """Records each call; returns ``payload`` (JSON), ``raw`` bytes, or a reply for ``reply``."""

    def __init__(self, reply=None, error=None, payload=None, raw=None):
        self.reply, self.error, self.payload, self.raw = reply, error, payload, raw
        self.calls = []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body), timeout))
        if self.error:
            raise self.error
        if self.raw is not None:
            return self.raw
        return json.dumps(self.payload or responses_reply(self.reply)).encode()


def _azure(transport, env=ENV):
    return AzureOpenAIProvider(AzureOpenAIConfig.from_env(env), transport=transport)


def test_offline_when_any_variable_missing():
    for key in ENV:
        env = {**ENV, key: ""}
        assert AzureOpenAIConfig.from_env(env) is None
        assert isinstance(provider_from_env(env), OfflineTemplateProvider)
    assert isinstance(provider_from_env({}), OfflineTemplateProvider)
    assert isinstance(provider_from_env(ENV), AzureOpenAIProvider)


def test_config_requires_https_and_hides_key():
    with pytest.raises(ValueError, match="https"):
        AzureOpenAIConfig.from_env({**ENV, "AZURE_OPENAI_ENDPOINT": "http://example.com"})
    with pytest.raises(ValueError, match="query"):
        AzureOpenAIConfig.from_env({**ENV, "AZURE_OPENAI_ENDPOINT": RESPONSES_URL + "?x=1"})
    assert KEY not in repr(AzureOpenAIConfig.from_env(ENV))


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example-resource.services.ai.azure.com",
        "https://example-resource.services.ai.azure.com/",
        "https://example-resource.services.ai.azure.com/openai/v1",
        "https://example-resource.services.ai.azure.com/openai/v1/",
        RESPONSES_URL,
    ],
)
def test_endpoint_forms_normalise_to_responses_url(endpoint):
    provider = _azure(None, {**ENV, "AZURE_OPENAI_ENDPOINT": endpoint})
    assert provider.url() == RESPONSES_URL


def test_api_version_is_not_required():
    assert "AZURE_OPENAI_API_VERSION" not in ENV
    assert isinstance(provider_from_env(ENV), AzureOpenAIProvider)


def test_offline_template_is_deterministic_and_labelled():
    a, b = narrate(REQUEST, OfflineTemplateProvider()), narrate(REQUEST, OfflineTemplateProvider())
    assert a == b and a.status == TEMPLATE and a.label == OFFLINE_LABEL
    assert a.text == " ".join(REQUEST.facts) and a.fallback_reason is None


def test_azure_request_shape():
    transport = FakeTransport(reply="Ari Vale fires Northbridge Rovers 1-0 up!")
    narrative = narrate(REQUEST, _azure(transport))
    [(url, headers, body, _)] = transport.calls
    assert url == RESPONSES_URL and "api-version" not in url
    assert headers["api-key"] == KEY and KEY not in json.dumps(body)
    assert set(body) == {"model", "input", "max_output_tokens", "store"}
    assert body["model"] == "demo-deployment"
    assert body["max_output_tokens"] == 200 and body["store"] is False
    system, user = body["input"]
    assert (system["role"], user["role"]) == ("system", "user")
    assert "Use ONLY the facts provided" in system["content"]
    assert all(f in user["content"] for f in REQUEST.facts)
    assert narrative.text == "Ari Vale fires Northbridge Rovers 1-0 up!"
    assert narrative.provider == "azure_openai" and narrative.warnings == ()


def test_ai_prose_is_never_verified_even_when_false():
    """A hallucinated claim stays unverified wording; invented numbers are flagged."""
    transport = FakeTransport(reply="Eastvale win 5-0 after 37 shots!")
    narrative = narrate(REQUEST, _azure(transport))
    assert narrative.status == AI_UNVERIFIED and narrative.status != VERIFIED
    assert narrative.label == AI_LABEL
    assert narrative.warnings == (
        "number 37 is not in the verified facts",
        "number 5 is not in the verified facts",
    )


def _http_error(code):
    return urllib.error.HTTPError(RESPONSES_URL, code, "error", {}, None)


MALFORMED = {
    "not an object": [responses_reply()],
    "incomplete": responses_reply(status="incomplete"),
    "failed": responses_reply(status="failed", output=[]),
    "no output": responses_reply(output=None),
    "output not a list": responses_reply(output={"type": "message"}),
    "empty output": responses_reply(output=[]),
    "only reasoning": responses_reply(output=[{"type": "reasoning", "summary": []}]),
    "refusal only": responses_reply(
        output=[{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]
    ),
    "content not a list": responses_reply(output=[{"type": "message", "content": "hi"}]),
    "text not a string": responses_reply(
        output=[{"type": "message", "content": [{"type": "output_text", "text": 3}]}]
    ),
    "blank text": responses_reply("   "),
    "legacy chat shape": {"choices": [{"message": {"content": "Hi"}}]},
}


@pytest.mark.parametrize(
    "transport",
    [
        FakeTransport(error=urllib.error.URLError(f"connection refused api-key={KEY}")),
        FakeTransport(error=TimeoutError()),
        FakeTransport(error=_http_error(401)),
        FakeTransport(error=_http_error(429)),
        FakeTransport(error=_http_error(500)),
        FakeTransport(raw=b"<html>gateway error</html>"),
        FakeTransport(raw=b""),
        *(FakeTransport(payload=p) for p in MALFORMED.values()),
    ],
)
def test_failures_fall_back_without_leaking_secrets(transport):
    narrative = narrate(REQUEST, _azure(transport))
    assert narrative.status == TEMPLATE and narrative.label == OFFLINE_LABEL
    assert narrative.text == " ".join(REQUEST.facts)
    assert narrative.fallback_reason.startswith("azure_openai unavailable (")
    assert KEY not in narrative.fallback_reason


def test_analyst_offline_template_drops_event_lines_and_brackets():
    request = NarrativeRequest(
        mode=ANALYST,
        title="Goal 1' — Home (1-0)",
        facts=(
            "1' Home, A: shot (goal) [evidence: x-1]",
            "Shot quality 0.084 xG [synthetic heuristic]. [evidence: x-1]",
        ),
    )
    text = OfflineTemplateProvider().render(request)
    assert text == "Goal 1' — Home (1-0): Shot quality 0.084 xG [synthetic heuristic]."


def test_real_transport_refuses_redirects():
    from matchlens.narrative import _NoRedirect

    req = urllib.request.Request("https://example-resource.openai.azure.com/x", method="POST")
    with pytest.raises(urllib.error.HTTPError, match="redirect refused"):
        _NoRedirect().redirect_request(req, None, 302, "Found", {}, "http://elsewhere.test/")


def test_extract_output_text_joins_message_parts_and_skips_other_items():
    reply = responses_reply(
        output=[
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "content": [
                    {"type": "output_text", "text": "Ari Vale "},
                    {"type": "output_text", "text": "scores! "},
                ],
            },
        ]
    )
    assert extract_output_text(reply) == "Ari Vale scores!"


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_extract_output_text_rejects_malformed(name):
    with pytest.raises(ResponseFormatError) as info:
        extract_output_text(MALFORMED[name])
    assert "Ari" not in str(info.value)  # fixed messages, no echoed content

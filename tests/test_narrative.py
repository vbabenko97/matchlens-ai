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
    narrate,
    provider_from_env,
)

KEY = "test-key-not-real-0123456789"
ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://example-resource.openai.azure.com/",
    "AZURE_OPENAI_API_KEY": KEY,
    "AZURE_OPENAI_DEPLOYMENT": "demo-deployment",
    "AZURE_OPENAI_API_VERSION": "2024-10-21",
}
REQUEST = NarrativeRequest(
    mode=CASUAL,
    title="GOAL! Northbridge Rovers score",
    facts=("Ari Vale scores for Northbridge Rovers in minute 1.", "That puts them ahead, 1-0."),
)


class FakeTransport:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body), timeout))
        if self.error:
            raise self.error
        return json.dumps({"choices": [{"message": {"content": self.reply}}]}).encode()


def _azure(transport):
    return AzureOpenAIProvider(AzureOpenAIConfig.from_env(ENV), transport=transport)


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
    assert KEY not in repr(AzureOpenAIConfig.from_env(ENV))


def test_offline_template_is_deterministic_and_labelled():
    a, b = narrate(REQUEST, OfflineTemplateProvider()), narrate(REQUEST, OfflineTemplateProvider())
    assert a == b and a.status == TEMPLATE and a.label == OFFLINE_LABEL
    assert a.text == " ".join(REQUEST.facts) and a.fallback_reason is None


def test_azure_request_shape():
    transport = FakeTransport(reply="Ari Vale fires Northbridge Rovers 1-0 up!")
    narrative = narrate(REQUEST, _azure(transport))
    [(url, headers, body, _)] = transport.calls
    assert url == (
        "https://example-resource.openai.azure.com/openai/deployments/demo-deployment"
        "/chat/completions?api-version=2024-10-21"
    )
    assert headers["api-key"] == KEY and KEY not in json.dumps(body)
    system, user = body["messages"]
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


@pytest.mark.parametrize(
    "transport",
    [
        FakeTransport(error=urllib.error.URLError(f"connection refused api-key={KEY}")),
        FakeTransport(error=TimeoutError()),
        FakeTransport(reply="   "),
        FakeTransport(reply=None),
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

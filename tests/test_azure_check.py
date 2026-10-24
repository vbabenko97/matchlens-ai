"""``azure-check`` formatting and failure handling with a FAKE transport.

These tests use mocks and are NOT evidence of Azure integration; only a real run of
``python -m matchlens azure-check`` against a live deployment counts (docs/AZURE_VERIFICATION.md).
"""

import io
import json
import urllib.error

import pytest

from matchlens.azure_check import run_check
from matchlens.cli import main
from matchlens.narrative import AI_UNVERIFIED

from .test_narrative import ENV, KEY, MALFORMED, RESPONSES_URL, FakeTransport, responses_reply


def test_missing_configuration_lists_names_only():
    code, report = run_check({"AZURE_OPENAI_API_KEY": KEY})
    assert code == 2 and report["ok"] is False
    assert report["missing"] == ["AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"]
    assert KEY not in json.dumps(report)


def test_insecure_endpoint_rejected():
    code, report = run_check({**ENV, "AZURE_OPENAI_ENDPOINT": "http://x.example"})
    assert code == 2 and "https" in report["error"]


def test_success_report_is_redacted_and_labelled():
    transport = FakeTransport(reply="What a strike!")
    code, report = run_check(ENV, transport=transport)
    assert code == 0 and report["ok"] is True
    assert report["api"] == "responses-v1" and report["request_path"] == "/openai/v1/responses"
    assert report["endpoint_host"] == "example-resource.services.ai.azure.com"
    assert report["deployment"] == "demo-deployment" and "api_version" not in report
    assert report["response_status"] == "completed"
    assert report["response_id"] == "resp_fake_0123"
    assert report["model"] == "gpt-4.1-mini-2025-04-14"
    assert report["usage"] == {"input_tokens": 17, "output_tokens": 27, "total_tokens": 44}
    assert report["store"] is False
    assert report["completion"] == "What a strike!"
    assert report["completion_status"] == AI_UNVERIFIED
    assert KEY not in json.dumps(report)
    [(url, _, body, _)] = transport.calls  # exactly one request, no retries
    assert url == RESPONSES_URL and body["store"] is False


def test_http_failures_do_not_fall_back():
    body = io.BytesIO(json.dumps({"error": {"code": "DeploymentNotFound"}}).encode())
    err = urllib.error.HTTPError(RESPONSES_URL, 404, "Not Found", {}, body)
    code, report = run_check(ENV, transport=FakeTransport(error=err))
    assert code == 1 and report["ok"] is False
    assert (report["error"], report["error_code"]) == ("HTTP 404", "DeploymentNotFound")
    err = urllib.error.HTTPError(RESPONSES_URL, 401, "Unauthorized", {}, None)
    code, report = run_check(ENV, transport=FakeTransport(error=err))
    assert code == 1 and (report["error"], report["error_code"]) == ("HTTP 401", "")
    code, report = run_check(ENV, transport=FakeTransport(error=TimeoutError()))
    assert code == 1 and report["error"] == "TimeoutError" and "completion" not in report
    code, report = run_check(ENV, transport=FakeTransport(raw=b"<html>oops</html>"))
    assert code == 1 and report["error"] == "JSONDecodeError"


@pytest.mark.parametrize(
    ("overrides", "problem"),
    [
        ({"status": "incomplete"}, "status is not 'completed'"),
        ({"status": "failed", "output": []}, "status is not 'completed'"),
        ({"id": ""}, "response id missing"),
        ({"id": None}, "response id missing"),
        ({"model": ""}, "model missing"),
        ({"usage": None}, "usage missing"),
        ({"usage": {"input_tokens": 17}}, "usage.output_tokens is not a positive integer"),
        ({"usage": {"input_tokens": 0, "output_tokens": 3}}, "usage.input_tokens"),
        ({"usage": {"input_tokens": "17", "output_tokens": 3}}, "usage.input_tokens"),
        ({"usage": {"input_tokens": True, "output_tokens": 3}}, "usage.input_tokens"),
        ({"output": []}, "response contains no output_text"),
        ({"output": None}, "response has no output array"),
    ],
)
def test_invalid_responses_fail_with_named_problems(overrides, problem):
    payload = responses_reply("Secret-ish wording", **overrides)
    code, report = run_check(ENV, transport=FakeTransport(payload=payload))
    assert code == 1 and report["ok"] is False and report["error"] == "InvalidResponse"
    assert any(p.startswith(problem) for p in report["problems"])
    assert "completion" not in report and "Secret-ish" not in json.dumps(report)


def test_problems_are_all_listed_and_status_is_sanitised():
    payload = responses_reply(status="<script>", id=None, model=None, usage={})
    code, report = run_check(ENV, transport=FakeTransport(payload=payload))
    assert code == 1 and report["response_status"] is None
    assert len(report["problems"]) == 5  # status, id, model, two token counts


@pytest.mark.parametrize("name", sorted(MALFORMED))
def test_every_malformed_shape_fails(name):
    code, report = run_check(ENV, transport=FakeTransport(payload=MALFORMED[name]))
    assert code == 1 and report["ok"] is False and "completion" not in report


def test_cli_without_configuration_exits_2(capsys, monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert main(["azure-check"]) == 2
    assert json.loads(capsys.readouterr().out)["missing"]

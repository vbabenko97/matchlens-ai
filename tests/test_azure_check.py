"""``azure-check`` formatting and failure handling with a FAKE transport.

These tests use mocks and are NOT evidence of Azure integration; only a real run of
``python -m matchlens azure-check`` against a live deployment counts (docs/AZURE_VERIFICATION.md).
"""

import io
import json
import urllib.error

from matchlens.azure_check import run_check
from matchlens.cli import main
from matchlens.narrative import AI_UNVERIFIED

from .test_narrative import ENV, KEY, FakeTransport


def test_missing_configuration_lists_names_only():
    code, report = run_check({"AZURE_OPENAI_API_KEY": KEY})
    assert code == 2 and report["ok"] is False
    assert "AZURE_OPENAI_ENDPOINT" in report["missing"]
    assert KEY not in json.dumps(report)


def test_insecure_endpoint_rejected():
    code, report = run_check({**ENV, "AZURE_OPENAI_ENDPOINT": "http://x.example"})
    assert code == 2 and "https" in report["error"]


def test_success_report_is_redacted_and_labelled():
    transport = FakeTransport(reply="What a strike!")
    code, report = run_check(ENV, transport=transport)
    assert code == 0 and report["ok"] is True
    assert report["endpoint_host"] == "example-resource.openai.azure.com"
    assert report["deployment"] == "demo-deployment" and report["api_version"] == "2024-10-21"
    assert report["completion"] == "What a strike!"
    assert report["completion_status"] == AI_UNVERIFIED
    assert KEY not in json.dumps(report)
    assert len(transport.calls) == 1  # exactly one request, no retries


def test_failures_do_not_fall_back():
    body = io.BytesIO(json.dumps({"error": {"code": "DeploymentNotFound"}}).encode())
    err = urllib.error.HTTPError("https://x", 404, "Not Found", {}, body)
    code, report = run_check(ENV, transport=FakeTransport(error=err))
    assert code == 1 and report["ok"] is False
    assert (report["error"], report["error_code"]) == ("HTTP 404", "DeploymentNotFound")
    code, report = run_check(ENV, transport=FakeTransport(error=TimeoutError()))
    assert code == 1 and report["error"] == "TimeoutError" and "completion" not in report
    code, report = run_check(ENV, transport=FakeTransport(reply=" "))
    assert code == 1 and report["error"] == "ValueError"


def test_cli_without_configuration_exits_2(capsys, monkeypatch):
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    assert main(["azure-check"]) == 2
    assert json.loads(capsys.readouterr().out)["missing"]

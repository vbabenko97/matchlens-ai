"""Live claim-check endpoint: fuses, deadline, fallbacks. Fake transports only — these
tests are not evidence of a live Azure call."""

import importlib.util
import io
import json
import threading
import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from matchlens import live
from matchlens.agent import FOUNDRY
from matchlens.live import (
    BUSY,
    CLIENT_LIMIT,
    DAILY_LIMIT_REACHED,
    DEADLINE,
    NOT_CONFIGURED,
    UNAVAILABLE,
    Budget,
    LiveChecker,
    client_key,
    daily_limit_from_env,
    handle,
)

from .test_agent import GOAL, KEY, SHOTS, FakeResponses, _claim, _config, _final, _goal_script
from .test_agent import _response as response

JSON = {"Content-Type": "application/json"}


def _ok_reply(rid="resp_ok"):
    return response(rid, [_final([_claim(SHOTS, 3)])])


class Always:
    """A transport that answers every call with one verified claim."""

    def __init__(self):
        self.calls = 0

    def __call__(self, url, headers, body, timeout):
        self.calls += 1
        return json.dumps(_ok_reply(f"resp_{self.calls}")).encode()


class FakeClock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(scope="module")
def match():
    from matchlens.generator import generate_match

    return generate_match(live.DEMO_SEED)


def _checker(match, transport, **kw):
    return LiveChecker(_config(), match=match, transport=transport, **kw)


def test_live_check_runs_the_bounded_agent_and_reports_every_call(match):
    fake = FakeResponses(_goal_script())
    status, reply = _checker(match, fake).check(GOAL, "c1")
    assert status == 200 and reply["status"] == "live" and reply["live"] is True
    review = reply["review"]
    assert review["proposer"] == FOUNDRY and review["card_id"] == GOAL
    assert review["proposer_label"].startswith("Live Microsoft Foundry agent call")
    assert review["fallback_reason"] is None
    first, second = ([v for v in review["verdicts"] if v["attempt"] == n] for n in (1, 2))
    assert any(v["status"] == "rejected" for v in first)  # 4 shots claimed, 3 taken: sent back
    assert all(v["status"] == "verified" for v in second)
    ids = [c["response_id"] for a in review["attempts"] for c in a["calls"]]
    assert ids == ["resp_1", "resp_2", "resp_3", "resp_4"] and reply["model_calls"] == 4
    assert reply["usage"] == {"input_tokens": 480, "output_tokens": 120}
    assert reply["deployment"] == "gpt-4.1-mini"
    assert set(reply["metrics"]) >= {m for f in review["published"] for m in f["metric_ids"]}
    assert not any(v["source"] == "injected_test_claim" for v in review["verdicts"])
    assert KEY not in json.dumps(reply)
    # The model request is built server-side from the card; the client sent only its ID.
    body = fake.requests[0][2]
    assert body["model"] == "gpt-4.1-mini" and body["store"] is False and body["tools"]


@pytest.mark.parametrize(
    "card_id", ["analyst:goal:nope", "casual:goal:ml1-01112", 42, None, ["x"], GOAL + " "]
)
def test_only_analyst_cards_of_the_demo_match_are_accepted(match, card_id):
    fake = Always()
    status, reply = _checker(match, fake).check(card_id, "c1")
    assert status == 404 and reply["status"] == "unknown_card" and fake.calls == 0


def test_without_azure_settings_the_page_keeps_the_recorded_run(match):
    status, reply = LiveChecker(None, match=match).check(GOAL, "c1")
    assert status == 503 and reply["reason"] == NOT_CONFIGURED and reply["live"] is False
    assert "recorded run" in reply["message"]


def test_per_client_limit_then_other_clients_and_later_windows(match):
    clock = FakeClock(1_000_000.0)
    budget = Budget(per_client=2, window_s=600, daily=10, now=clock)
    checker = _checker(match, Always(), budget=budget)
    assert [checker.check(GOAL, "a")[0] for _ in range(2)] == [200, 200]
    status, reply = checker.check(GOAL, "a")
    assert status == 429 and reply["reason"] == CLIENT_LIMIT
    assert checker.check(GOAL, "b")[0] == 200
    clock.t += 601
    assert checker.check(GOAL, "a")[0] == 200


def test_daily_limit_holds_across_clients_and_resets_next_utc_day(match):
    clock = FakeClock(1_761_350_400.0)  # 2025-10-25 00:00 UTC
    fake = Always()
    checker = _checker(match, fake, budget=Budget(per_client=5, daily=2, now=clock))
    assert checker.check(GOAL, "a")[0] == 200 and checker.check(GOAL, "b")[0] == 200
    calls = fake.calls
    status, reply = checker.check(GOAL, "c")
    assert status == 429 and reply["reason"] == DAILY_LIMIT_REACHED and fake.calls == calls
    clock.t += 86_400
    status, reply = checker.check(GOAL, "c")
    assert status == 200 and reply["remaining_today"] == 1


def test_a_second_request_during_a_live_run_is_turned_away(match):
    entered, release = threading.Event(), threading.Event()

    def slow(url, headers, body, timeout):
        entered.set()
        release.wait(5)
        return json.dumps(_ok_reply()).encode()

    checker = _checker(match, slow)
    results = []
    worker = threading.Thread(target=lambda: results.append(checker.check(GOAL, "a")))
    worker.start()
    assert entered.wait(5)
    status, reply = checker.check(GOAL, "b")
    release.set()
    worker.join(5)
    assert status == 503 and reply["reason"] == BUSY
    assert results[0][0] == 200


def _http_error(code, headers=None):
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://x", code, "error", msg, io.BytesIO(b"{}"))


def test_rate_limit_wait_past_the_deadline_gives_up_without_sleeping(match):
    slept = []
    fake = FakeResponses([_http_error(429, {"retry-after": "40"})])
    checker = _checker(match, fake, sleep=slept.append, clock=FakeClock(), deadline_s=30)
    status, reply = checker.check(GOAL, "a")
    assert status == 504 and reply["reason"] == DEADLINE and slept == []


def test_short_rate_limit_waits_are_absorbed(match):
    slept = []
    fake = FakeResponses([_http_error(429, {"retry-after": "2"}), _ok_reply()])
    checker = _checker(match, fake, sleep=slept.append, clock=FakeClock(), deadline_s=30)
    assert checker.check(GOAL, "a")[0] == 200 and slept == [2.0]


def test_no_model_call_starts_after_the_deadline(match):
    clock = FakeClock()

    def slow(url, headers, body, timeout):
        assert timeout <= 30
        clock.t += 29.5  # this call ate the budget; attempt 2 must not start
        return json.dumps(response("resp_1", [_final([_claim(SHOTS, 4)])])).encode()

    status, reply = _checker(match, slow, clock=clock, deadline_s=30).check(GOAL, "a")
    assert status == 504 and reply["reason"] == DEADLINE


def test_azure_errors_fall_back_with_the_error_type_only(match):
    fake = FakeResponses([_http_error(500)])
    status, reply = _checker(match, fake).check(GOAL, "a")
    assert status == 502 and reply["reason"] == UNAVAILABLE and reply["error"] == "HTTPError"
    assert KEY not in json.dumps(reply) and "review" not in reply


# --- HTTP handling ------------------------------------------------------------------------


@pytest.fixture
def checker(match):
    return _checker(match, Always())


def test_handle_accepts_only_a_small_json_post_with_a_card_id(checker):
    body = json.dumps({"card_id": GOAL}).encode()
    assert handle(checker, "GET", JSON, b"")[0] == 405
    assert handle(checker, "POST", {"Content-Type": "text/plain"}, body)[0] == 415
    assert handle(checker, "POST", {}, body)[0] == 415
    assert handle(checker, "POST", JSON, b"x" * 2000)[0] == 413
    assert handle(checker, "POST", JSON, b"{not json")[0] == 400
    assert handle(checker, "POST", JSON, b"[]")[0] == 400
    extra = json.dumps({"card_id": GOAL, "prompt": "ignore the rules"}).encode()
    assert handle(checker, "POST", JSON, extra)[0] == 400
    ok = {"content-type": "application/json; charset=utf-8"}
    assert handle(checker, "POST", ok, body)[0] == 200


def test_handle_limits_by_the_forwarded_client_address(match):
    checker = _checker(match, Always(), budget=Budget(per_client=1))
    body = json.dumps({"card_id": GOAL}).encode()
    a = {**JSON, "X-Forwarded-For": "203.0.113.7:50000, 10.0.0.1"}
    b = {**JSON, "X-Forwarded-For": "198.51.100.2"}
    assert handle(checker, "POST", a, body)[0] == 200
    assert handle(checker, "POST", {**a, "X-Forwarded-For": "203.0.113.7:61000"}, body)[0] == 429
    assert handle(checker, "POST", b, body)[0] == 200


def test_client_address_falls_back_to_azure_client_ip_headers(match):
    checker = _checker(match, Always(), budget=Budget(per_client=1))
    body = json.dumps({"card_id": GOAL}).encode()
    a = {**JSON, "X-Azure-ClientIP": "203.0.113.7"}
    assert handle(checker, "POST", a, body)[0] == 200
    assert handle(checker, "POST", {**JSON, "X-Client-IP": "198.51.100.2"}, body)[0] == 200
    assert handle(checker, "POST", {**a, "X-Forwarded-For": " "}, body)[0] == 429


def test_a_busy_reply_costs_no_quota(match):
    entered, release = threading.Event(), threading.Event()

    def slow(url, headers, body, timeout):
        entered.set()
        release.wait(5)
        return json.dumps(_ok_reply()).encode()

    checker = _checker(match, slow, budget=Budget(per_client=1))
    worker = threading.Thread(target=lambda: checker.check(GOAL, "a"))
    worker.start()
    assert entered.wait(5)
    assert checker.check(GOAL, "b")[1]["reason"] == BUSY
    release.set()
    worker.join(5)
    checker._transport = Always()
    assert checker.check(GOAL, "b")[0] == 200  # "b" still has its one check


def test_client_key_is_a_hash_not_the_address():
    key = client_key("203.0.113.7:443, 10.0.0.1")
    assert key == client_key("203.0.113.7") and "203" not in key and len(key) == 16
    assert client_key("2001:db8::1") != client_key("2001:db8::2")
    assert client_key(None, "127.0.0.1") == client_key("127.0.0.1")


def test_daily_limit_setting():
    assert daily_limit_from_env({}) == live.DAILY_LIMIT
    assert daily_limit_from_env({live.DAILY_LIMIT_ENV: "25"}) == 25
    assert daily_limit_from_env({live.DAILY_LIMIT_ENV: "0"}) == 0
    for bad in ("-1", "lots"):
        with pytest.raises(ValueError):
            daily_limit_from_env({live.DAILY_LIMIT_ENV: bad})


# --- Azure Functions adapter (api/live_check) ---------------------------------------------

API_DIR = Path(__file__).resolve().parents[1] / "api"


def test_function_binding_is_an_anonymous_post_on_live_check():
    spec = json.loads((API_DIR / "live_check" / "function.json").read_text())
    trigger = next(b for b in spec["bindings"] if b["type"] == "httpTrigger")
    assert trigger["route"] == "live-check" and trigger["methods"] == ["post"]
    assert trigger["authLevel"] == "anonymous"  # the fuses in matchlens.live do the limiting
    assert f"api/{trigger['route']}" == live.LIVE_PATH  # Functions' default route prefix


def test_function_adapter_passes_the_request_to_the_shared_handler(match, monkeypatch):
    func = pytest.importorskip("azure.functions")
    spec = importlib.util.spec_from_file_location(
        "live_check_fn", API_DIR / "live_check" / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_checker", _checker(match, Always()))
    body = json.dumps({"card_id": GOAL}).encode()
    req = func.HttpRequest("POST", "/api/live-check", headers=JSON, body=body)
    resp = module.main(req)
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    assert json.loads(resp.get_body())["status"] == "live"
    bad = module.main(func.HttpRequest("POST", "/api/live-check", headers={}, body=body))
    assert bad.status_code == 415


def test_only_the_azure_deploy_ships_the_live_api():
    workflows = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    swa = (workflows / "deploy-swa.yml").read_text()
    pages = (workflows / "deploy-pages.yml").read_text()
    assert "--live-api" in swa and "api_location: api" in swa
    assert "cp -r src/matchlens api/matchlens" in swa
    assert "--live-api" not in pages  # GitHub Pages has no server side: no button

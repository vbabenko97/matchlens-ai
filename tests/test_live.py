"""Live claim-check endpoint: fuses, deadline, fallbacks. Fake transports only — these
tests are not evidence of a live Azure call."""

import importlib.util
import io
import json
import logging
import re
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
    COUNTER_UNAVAILABLE,
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
from matchlens.quota import CounterError, MemoryCounter, TableCounter

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


# --- Shared daily quota ---------------------------------------------------------------------


def test_the_daily_reservation_is_taken_before_the_model_call_and_kept_on_failure(match):
    counter = MemoryCounter()

    def failing(url, headers, body, timeout):
        assert counter.reserve("probe", 1) == 1  # transport runs: is the day already reserved?
        assert counter._counts  # the real day's reservation exists before any model call
        raise _http_error(500)

    checker = _checker(match, failing, budget=Budget(daily=1, counter=counter))
    assert checker.check(GOAL, "a")[1]["reason"] == UNAVAILABLE
    status, reply = checker.check(GOAL, "b")  # the failed call still used the day's only check
    assert status == 429 and reply["reason"] == DAILY_LIMIT_REACHED


def test_a_restarted_instance_continues_the_shared_count(match):
    counter = MemoryCounter()  # stands in for the durable store shared by all instances
    first = _checker(match, Always(), budget=Budget(daily=2, counter=counter))
    assert first.check(GOAL, "a")[0] == 200
    second = _checker(match, Always(), budget=Budget(daily=2, counter=counter))
    status, reply = second.check(GOAL, "b")
    assert status == 200 and reply["remaining_today"] == 0
    assert second.check(GOAL, "c")[1]["reason"] == DAILY_LIMIT_REACHED


def test_no_live_call_when_the_quota_store_fails_or_is_missing(match):
    class Broken:
        def reserve(self, day, limit, time_left=None):
            raise CounterError("quota store read failed (HTTP 500)")

    fake = Always()
    status, reply = _checker(match, fake, budget=Budget(counter=Broken())).check(GOAL, "a")
    assert status == 429 and reply["reason"] == COUNTER_UNAVAILABLE and fake.calls == 0
    status, reply = _checker(match, fake, budget=Budget(counter=None)).check(GOAL, "a")
    assert reply["reason"] == NOT_CONFIGURED and fake.calls == 0


def test_from_env_needs_the_shared_store_for_live_calls():
    azure = {
        "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com",
        "AZURE_OPENAI_API_KEY": KEY,
        "AZURE_OPENAI_DEPLOYMENT": "gpt-4.1-mini",
    }
    assert live.checker_from_env(azure).budget.counter is None  # refuses: no shared quota
    assert isinstance(live.checker_from_env(azure, memory_quota=True).budget.counter, MemoryCounter)
    sas = "https://acct.table.core.windows.net/livechecks?sv=1&sig=abc"
    counter = live.checker_from_env({**azure, live.COUNTER_URL_ENV: sas}).budget.counter
    assert isinstance(counter, TableCounter)
    with pytest.raises(ValueError):
        live.checker_from_env({**azure, live.COUNTER_URL_ENV: "http://bad"})


# --- One deadline for the whole request -----------------------------------------------------


def test_a_slow_quota_store_uses_up_the_deadline_without_a_model_call(match):
    """The reproduced gap: with a slow store, the model part started at 31 s of a 30 s budget."""
    clock = FakeClock()

    def slow_store(method, url, headers, body, timeout):
        clock.t += 29.5  # a slow but successful read
        return (404, {}, b"{}") if method == "GET" else (204, {}, b"")

    fake = Always()
    counter = TableCounter("https://acct.table.core.windows.net/t?sig=s", slow_store, 30.0)
    checker = _checker(match, fake, budget=Budget(counter=counter), clock=clock, deadline_s=30)
    status, reply = checker.check(GOAL, "a")
    assert status == 504 and reply["reason"] == DEADLINE
    assert fake.calls == 0 and clock.t <= 30  # no insert, no model call, nothing past 30 s


def test_a_failing_store_is_reported_as_such_within_the_deadline(match):
    clock = FakeClock()
    timeouts = []

    def failing_store(method, url, headers, body, timeout):
        timeouts.append(timeout)
        clock.t += timeout
        raise TimeoutError("read timed out")

    fake = Always()
    counter = TableCounter("https://acct.table.core.windows.net/t?sig=s", failing_store, 25.0)
    checker = _checker(match, fake, budget=Budget(counter=counter), clock=clock, deadline_s=30)
    status, reply = checker.check(GOAL, "a")
    assert reply["reason"] == COUNTER_UNAVAILABLE and fake.calls == 0
    assert timeouts == [25.0] and clock.t <= 30


def test_store_calls_get_only_the_time_left(match):
    clock = FakeClock()
    seen = []

    def store(method, url, headers, body, timeout):
        seen.append(timeout)
        clock.t += 27  # a slow but successful read and insert
        return (404, {}, b"{}") if method == "GET" else (204, {}, b"")

    counter = TableCounter("https://acct.table.core.windows.net/t?sig=s", store, 25.0)
    checker = _checker(match, Always(), budget=Budget(counter=counter), clock=clock, deadline_s=30)
    status, reply = checker.check(GOAL, "a")
    assert seen == [25.0, 3.0]  # the insert only gets what is left of the 30 s
    assert status == 504 and reply["reason"] == DEADLINE  # reserved, but no time for the model


def test_a_quick_reservation_leaves_the_rest_of_the_deadline_to_the_model(match):
    clock = FakeClock()
    model_timeouts = []

    def store(method, url, headers, body, timeout):
        clock.t += 4
        return (404, {}, b"{}") if method == "GET" else (204, {}, b"")

    def model(url, headers, body, timeout):
        model_timeouts.append(timeout)
        return json.dumps(_ok_reply()).encode()

    counter = TableCounter("https://acct.table.core.windows.net/t?sig=s", store, 5.0)
    checker = _checker(match, model, budget=Budget(counter=counter), clock=clock, deadline_s=30)
    status, reply = checker.check(GOAL, "a")
    assert status == 200 and model_timeouts[0] == pytest.approx(22.0)  # 30 - 2 x 4 s


# --- Telemetry (Application Insights) -----------------------------------------------------

KQL = Path(__file__).resolve().parents[1] / "docs" / "telemetry.kql"


def _logged(caplog, checker, body, headers=JSON):
    """Handle one request; return its reply and the one telemetry line it left."""
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="matchlens.live"):
        status, reply = handle(checker, "POST", headers, body)
    lines = [r for r in caplog.records if r.name == "matchlens.live"]
    assert len(lines) == 1
    message = lines[0].getMessage()
    assert message.startswith(live.TELEMETRY_PREFIX) and "\n" not in message
    record = json.loads(message.removeprefix(live.TELEMETRY_PREFIX))
    assert record["schema"] == live.TELEMETRY_SCHEMA and record["http_status"] == status
    assert isinstance(record["elapsed_ms"], int) and record["elapsed_ms"] >= 0
    return reply, lines[0].levelno, record, message


def _card(card_id):
    return json.dumps({"card_id": card_id}).encode()


def test_a_live_check_logs_its_cost_its_verdicts_and_its_response_ids(match, caplog):
    checker = _checker(match, FakeResponses(_goal_script()))
    reply, level, record, _ = _logged(caplog, checker, _card(GOAL))
    assert level == logging.INFO and record["status"] == "live" and record["card_id"] == GOAL
    assert record["reason"] is None and record["error"] is None
    # what the transport counted equals what the agent recorded, call by call
    assert reply["spent"] == {
        "model_calls": 4,
        "input_tokens": 480,
        "output_tokens": 120,
        "rate_limited": 0,
    }
    assert reply["model_calls"] == 4 and reply["usage"] == {
        "input_tokens": 480,
        "output_tokens": 120,
    }
    assert {k: record[k] for k in reply["spent"]} == reply["spent"]
    # attempt 1 claimed 4 shots (rejected) and the retry corrected it to 3
    assert record["claims_proposed"] == [2, 2] and record["claims_rejected"] == [1, 0]
    assert record["published_facts"] == len(reply["review"]["published"])
    assert record["response_ids"] == ["resp_1", "resp_2", "resp_3", "resp_4"]
    assert record["latency_ms"] == reply["latency_ms"] and record["deployment"] == "gpt-4.1-mini"
    assert record["remaining_today"] == reply["remaining_today"] == live.DAILY_LIMIT - 1


def test_telemetry_never_holds_the_key_the_client_address_or_request_text(match, caplog):
    checker = _checker(match, FakeResponses(_goal_script()))
    headers = {**JSON, "X-Forwarded-For": "203.0.113.7, 10.0.0.1"}
    *_, live_line = _logged(caplog, checker, _card(GOAL), headers)
    _, level, record, unknown_line = _logged(caplog, checker, _card("<script>x</script>"))
    assert level == logging.INFO and record["status"] == "unknown_card"
    assert record["card_id"] is None
    *_, bad_line = _logged(caplog, checker, b'{"card_id": "a", "prompt": "say hi"}')
    for line in (live_line, unknown_line, bad_line):
        for secret in (KEY, "example.openai.azure.com", "203.0.113.7", "10.0.0.1"):
            assert secret not in line
        assert client_key("203.0.113.7") not in line and client_key(None) not in line
        assert "<script>" not in line and "say hi" not in line


def test_a_run_that_fails_half_way_logs_a_warning_with_what_it_spent(match, caplog):
    clock = FakeClock()

    def slow(url, headers, body, timeout):
        clock.t += 29.5  # this call ate the budget; attempt 2 must not start
        return json.dumps(response("resp_1", [_final([_claim(SHOTS, 4)])])).encode()

    checker = _checker(match, slow, clock=clock, deadline_s=30)
    reply, level, record, _ = _logged(caplog, checker, _card(GOAL))
    assert reply["reason"] == DEADLINE and level == logging.WARNING
    assert reply["spent"] == {
        "model_calls": 1,
        "input_tokens": 120,
        "output_tokens": 30,
        "rate_limited": 0,
    }
    assert {k: record[k] for k in reply["spent"]} == reply["spent"]
    assert "claims_proposed" not in record and "response_ids" not in record

    waits = FakeResponses([_http_error(429, {"retry-after": "40"})])
    checker = _checker(match, waits, sleep=lambda s: None, clock=FakeClock(), deadline_s=30)
    _, level, record, _ = _logged(caplog, checker, _card(GOAL))
    assert record["reason"] == DEADLINE and level == logging.WARNING
    assert record["rate_limited"] == 1 and record["model_calls"] == 0

    failing = _checker(match, FakeResponses([_http_error(500)]))
    _, level, record, _ = _logged(caplog, failing, _card(GOAL))
    assert (record["reason"], record["error"], level) == (UNAVAILABLE, "HTTPError", logging.WARNING)
    assert record["model_calls"] == 0 and record["rate_limited"] == 0


def test_refusals_before_any_model_call_log_no_spending(match, caplog):
    unconfigured = LiveChecker(None, match=match, transport=Always())
    _, level, record, _ = _logged(caplog, unconfigured, _card(GOAL))
    assert record["reason"] == NOT_CONFIGURED and level == logging.WARNING

    class Broken:
        def reserve(self, day, limit, time_left=None):
            raise CounterError("quota store read failed (HTTP 500)")

    broken = _checker(match, Always(), budget=Budget(counter=Broken()))
    _, level, record, _ = _logged(caplog, broken, _card(GOAL))
    assert record["reason"] == COUNTER_UNAVAILABLE and record["http_status"] == 429
    assert level == logging.WARNING  # a server-side fault, though the visitor gets a 429

    spent = _checker(match, Always(), budget=Budget(daily=0, counter=MemoryCounter()))
    _, level, record, _ = _logged(caplog, spent, _card(GOAL))
    assert record["reason"] == DAILY_LIMIT_REACHED and level == logging.INFO  # the cap works
    assert "model_calls" not in record and "input_tokens" not in record

    _, level, record, _ = _logged(caplog, spent, _card(GOAL), headers={})
    assert record["status"] == "unsupported_media_type" and level == logging.INFO


def test_host_json_lets_requests_and_the_telemetry_lines_through():
    levels = json.loads((API_DIR / "host.json").read_text())["logging"]["logLevel"]
    assert levels["default"] == "Warning"  # no runtime chatter
    assert levels["Host.Results"] == "Information"  # the requests table: duration, result code
    function = "live_check"  # a function.json app names each function after its folder
    assert (API_DIR / function / "function.json").is_file()
    assert levels[f"Function.{function}.User"] == "Information"  # traces from our logger


def test_the_ready_made_queries_read_only_fields_the_records_have(match, caplog):
    keys = set()
    checker = _checker(match, FakeResponses(_goal_script()))
    for body in (_card(GOAL), _card("nope")):
        keys |= set(_logged(caplog, checker, body)[2])
    failing = _checker(match, FakeResponses([_http_error(500)]))
    keys |= set(_logged(caplog, failing, _card(GOAL))[2])
    text = KQL.read_text()
    used = set(re.findall(r"\be\.([a-z_]+)", text))
    assert used and used <= keys, used - keys
    assert text.count(f'has "{live.TELEMETRY_PREFIX.strip()}"') >= 4  # every query filters


def test_a_broken_telemetry_record_never_breaks_the_reply(match, caplog, monkeypatch):
    def broken(status, reply, elapsed_ms):
        raise KeyError("latency_ms")

    monkeypatch.setattr(live, "telemetry_record", broken)
    checker = _checker(match, FakeResponses(_goal_script()))
    with caplog.at_level(logging.INFO, logger="matchlens.live"):
        status, reply = handle(checker, "POST", JSON, _card(GOAL))
    assert status == 200 and reply["status"] == "live"  # the paid answer still arrives
    (line,) = [r for r in caplog.records if r.name == "matchlens.live"]
    assert line.levelno == logging.WARNING
    assert line.getMessage() == "live-check telemetry failed: KeyError"
    assert live.TELEMETRY_PREFIX.strip() not in line.getMessage()  # the queries skip it

"""Bounded claim agent: causal tools, verdicts, retry, fallbacks and recorded replay.

The Foundry loop runs against a scripted fake Responses API; that exercises the protocol
handling only and is not integration evidence.
"""

import io
import json
import re
import urllib.error

import pytest

from matchlens import agent as agent_module
from matchlens import narrative
from matchlens.agent import (
    FOUNDRY,
    INJECTED,
    MAX_CLAIMS,
    MAX_MODEL_CALLS,
    NETWORK_RETRIES,
    NETWORK_RETRY_WAIT_S,
    OFFLINE,
    RATE_LIMIT_MAX_WAIT_S,
    RATE_LIMIT_RETRIES,
    RECORDED,
    REJECTED,
    TRACE_SCHEMA,
    AgentError,
    FoundryAgentProposer,
    OfflineProposer,
    RecordedProposer,
    check_claim,
    get_events,
    get_metric,
    list_metrics,
    live_review,
    parse_claims,
    retry_after_s,
    review_card,
    review_cards,
    review_context,
    run_tool,
    trace_problems,
)
from matchlens.cards import build_cards
from matchlens.claims import VERIFIED, Claim
from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.narrative import ANALYST, AzureOpenAIConfig, ResponseFormatError
from matchlens.replay import build_bundle, dumps

KEY = "test-key-never-logged-123"
GOAL = "analyst:goal:ml1-01112"  # 71' goal, published at 70:13 (2-1)
SHOTS = "home.shots@3600-4500:asof:ml1-01112"  # 3 as of the goal; the 4th is ml1-01166


@pytest.fixture(scope="module")
def match():
    return generate_match(1)


@pytest.fixture(scope="module")
def card_set(match):
    return build_cards(match, ANALYST)


@pytest.fixture(scope="module")
def goal(card_set):
    return next(c for c in card_set.cards if c.card_id == GOAL)


@pytest.fixture(scope="module")
def ctx(match, goal):
    return review_context(match, goal)


def _config():
    return AzureOpenAIConfig("https://example.openai.azure.com", KEY, "gpt-4.1-mini")


def _response(rid, output):
    usage = {"input_tokens": 120, "output_tokens": 30}
    return {
        "id": rid,
        "model": "gpt-4.1-mini",
        "status": "completed",
        "output": output,
        "usage": usage,
    }


def _call(name, args, call_id):
    return {
        "type": "function_call",
        "id": f"fc_{call_id}",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(args),
        "status": "completed",
    }


def _final(claims):
    text = json.dumps({"claims": claims})
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def _claim(metric_id=None, value=None, cited=()):
    return {"metric_id": metric_id, "value": value, "cited_event_ids": list(cited)}


class FakeResponses:
    """Replays scripted Responses API replies and records every request."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append((url, headers, json.loads(body)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return json.dumps(reply).encode()


def _goal_script():
    """Attempt 1 looks things up but claims 4 shots; the retry corrects it to 3."""
    events = ["ml1-01111", "ml1-01112"]
    return [
        _response("resp_1", [_call("list_metrics", {}, "c1")]),
        _response(
            "resp_2",
            [
                _call("get_metric", {"metric_id": SHOTS}, "c2"),
                _call("get_events", {"event_ids": [*events, "ml1-01166"]}, "c3"),
            ],
        ),
        _response("resp_3", [_final([_claim(SHOTS, 4), _claim(cited=events)])]),
        _response("resp_4", [_final([_claim(SHOTS, 3), _claim(cited=events)])]),
    ]


# --- Context and tools -------------------------------------------------------------------


def test_context_is_cut_at_publication(match, goal, ctx):
    assert ctx.match.events[-1].event_id == goal.evidence_event_ids[-1] == ctx.as_of_event_id
    assert "ml1-01166" in ctx.later_event_ids and len(ctx.match.events) + len(
        ctx.later_event_ids
    ) == len(match.events)
    assert all(m.endswith(":asof:ml1-01112") for m in ctx.metrics)
    assert ctx.metrics[SHOTS].value == 3


def test_tools_only_see_the_match_so_far(ctx):
    ids = list_metrics(ctx)
    assert ids == list(ctx.metrics) and SHOTS in ids
    shots = get_metric(ctx, SHOTS)
    assert shots["value"] == 3 and "ml1-01166" not in shots["source_event_ids"]
    assert "error" in get_metric(ctx, "home.shots@3600-4500")  # the full-window ID is unknown
    events = get_events(ctx, ["ml1-01112", "ml1-01166", "nope"])
    assert events[0]["event_id"] == "ml1-01112" and events[0]["outcome"] == "goal"
    assert events[0]["score"] == [2, 1] and events[0]["minute"] == 71
    # A later event and an unknown one look the same: the agent learns nothing about the future.
    assert events[1] == {"event_id": "ml1-01166", "error": "not available"}
    assert events[2] == {"event_id": "nope", "error": "not available"}


def test_run_tool_rejects_bad_arguments(ctx):
    assert "error" in run_tool(ctx, "get_metric", "not json")
    assert "error" in run_tool(ctx, "get_metric", json.dumps({"metric_id": 7}))
    assert "error" in run_tool(ctx, "get_events", json.dumps({"event_ids": "ml1-01112"}))
    assert "error" in run_tool(ctx, "delete_match", "{}")


# --- Verdicts ----------------------------------------------------------------------------


def test_verified_claim_is_rendered_from_the_template(ctx):
    v = check_claim(ctx, Claim(SHOTS, 3), FOUNDRY, 1)
    assert v.status == VERIFIED and v.reasons == () and v.correction is None
    assert v.fact.text.startswith("Northbridge Rovers — Shots: 3 (between 60' and 75', as of 71')")
    assert v.fact.metric_ids == (SHOTS,)


def test_wrong_value_is_rejected_and_corrected(ctx):
    v = check_claim(ctx, Claim(SHOTS, 4), FOUNDRY, 1)
    assert v.status == REJECTED and v.fact is None
    assert v.reasons == (f"claimed 4 but {SHOTS} = 3",)
    assert v.correction.text.startswith("Northbridge Rovers — Shots: 3")


def test_citing_a_later_event_is_rejected(ctx):
    v = check_claim(ctx, Claim(cited_event_ids=("ml1-01112", "ml1-01166")), FOUNDRY, 1)
    assert v.status == REJECTED and v.correction is None
    assert "cited events happen after the card is published: ['ml1-01166']" in v.reasons


def test_unknown_metric_is_rejected_without_correction(ctx):
    v = check_claim(ctx, Claim("home.shots@3600-4500", 4), FOUNDRY, 1)
    assert v.status == REJECTED and v.correction is None
    assert v.reasons == ("unknown metric 'home.shots@3600-4500'",)


def test_parse_claims_enforces_shape_and_limit():
    many = json.dumps({"claims": [_claim(SHOTS, 3)] * (MAX_CLAIMS + 2)})
    claims, dropped = parse_claims(many)
    assert len(claims) == MAX_CLAIMS and dropped == 2
    for bad in ("nope", "[]", json.dumps({"claims": [_claim(SHOTS, True)]})):
        with pytest.raises(ResponseFormatError):
            parse_claims(bad)


# --- Reviews -----------------------------------------------------------------------------


def test_offline_review_claims_the_card_metrics(match, card_set):
    reviews = review_cards(match, card_set, OfflineProposer())
    assert [r.card_id for r in reviews] == [c.card_id for c in card_set.cards]
    for review, card in zip(reviews, card_set.cards, strict=True):
        own = [v for v in review.verdicts if v.source == OFFLINE]
        assert own and all(v.verified for v in own) and len(review.attempts) == 1
        metric_facts = [f.text for f in card.facts if f.metric_ids]
        assert [f.text for f in review.published][: len(metric_facts)] == metric_facts


def test_injected_wrong_score_is_rejected_and_corrected(match, card_set):
    reviews = {r.card_id: r for r in review_cards(match, card_set, OfflineProposer())}
    injected = [(cid, v) for cid, r in reviews.items() for v in r.verdicts if v.source == INJECTED]
    assert [cid for cid, _ in injected] == [GOAL]  # first goal card only
    v = injected[0][1]
    assert (v.claim.metric_id, v.claim.value) == ("home.goals:asof:ml1-01112", 3)
    assert v.status == REJECTED and v.reasons == ("claimed 3 but home.goals:asof:ml1-01112 = 2",)
    assert v.correction.text.startswith("Northbridge Rovers — Goals: 2 (as of 71')")
    assert v.correction in reviews[GOAL].published


def test_foundry_agent_loop_retries_once_and_publishes_only_verified(match, goal):
    fake = FakeResponses(_goal_script())
    review = review_card(match, goal, FoundryAgentProposer(_config(), fake))
    assert review.proposer == FOUNDRY and review.fallback_reason is None
    first, second = review.attempts
    assert [c["response_id"] for c in first.calls] == ["resp_1", "resp_2", "resp_3"]
    assert [t["name"] for t in first.calls[1]["tool_calls"]] == ["get_metric", "get_events"]
    statuses = [(v.attempt, v.claim.value, v.status) for v in review.verdicts]
    assert statuses == [
        (1, 4, REJECTED),
        (1, None, VERIFIED),
        (2, 3, VERIFIED),
        (2, None, VERIFIED),
    ]
    assert len(review.published) == 2 and review.published[0].text.startswith(
        "Northbridge Rovers — Shots: 3"
    )

    bodies = [body for _, _, body in fake.requests]
    for url, headers, body in fake.requests:
        assert url == "https://example.openai.azure.com/openai/v1/responses"
        assert headers["api-key"] == KEY and body["store"] is False
        assert [t["name"] for t in body["tools"]] == ["list_metrics", "get_metric", "get_events"]
        assert body["text"]["format"]["type"] == "json_schema"
    # Tool results come from the match as of publication; calls are echoed without item ids.
    outputs = {
        i["call_id"]: json.loads(i["output"])
        for i in bodies[2]["input"]
        if i.get("type") == "function_call_output"
    }
    assert outputs["c2"]["value"] == 3
    assert outputs["c3"][2] == {"event_id": "ml1-01166", "error": "not available"}
    assert all("id" not in i for i in bodies[2]["input"] if i.get("type") == "function_call")
    # The retry starts fresh with the validator's reasons.
    retry_brief = bodies[3]["input"][1]["content"]
    assert f"rejected: claimed 4 but {SHOTS} = 3" in retry_brief and len(bodies[3]["input"]) == 2


@pytest.mark.parametrize(
    "replies, error",
    [
        ([OSError("down")], "OSError"),
        ([urllib.error.HTTPError("u", 401, "no", {}, None)], "HTTPError"),
        (
            [
                _response(
                    "r", [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}]
                )
            ],
            "ResponseFormatError",
        ),
        (
            [_response("r", [_call("list_metrics", {}, f"c{i}")]) for i in range(MAX_MODEL_CALLS)],
            "AgentError",
        ),
        (
            [_response("r", [{"type": "function_call", "name": "list_metrics"}])],
            "ResponseFormatError",
        ),
    ],
)
def test_agent_failures_fall_back_offline_unless_strict(match, goal, replies, error):
    review = review_card(match, goal, FoundryAgentProposer(_config(), FakeResponses(replies)))
    assert (
        review.proposer == OFFLINE
        and review.fallback_reason == f"foundry_agent unavailable ({error})"
    )
    assert all(v.verified for v in review.verdicts)
    with pytest.raises((OSError, ResponseFormatError, AgentError)):
        review_card(
            match, goal, FoundryAgentProposer(_config(), FakeResponses(replies)), strict=True
        )


def _rate_limited(headers, body=b""):
    return urllib.error.HTTPError("u", 429, "Too Many Requests", headers, io.BytesIO(body))


def test_rate_limits_wait_as_azure_asks_then_continue(match, goal):
    waits = []
    script = [_rate_limited({"retry-after": "7"}), _rate_limited({"retry-after-ms": "1500"})]
    fake = FakeResponses(script + _goal_script())
    proposer = FoundryAgentProposer(_config(), fake, sleep=waits.append)
    review = review_card(match, goal, proposer, strict=True)
    assert waits == [7.0, 1.5] and review.proposer == FOUNDRY
    assert review.attempts[0].calls[0]["retries"] == [
        {"cause": "HTTP 429", "wait_s": 7.0},
        {"cause": "HTTP 429", "wait_s": 1.5},
    ]
    assert review.attempts[0].calls[1]["retries"] == []


def test_network_errors_are_retried_twice_then_raised(match, goal):
    waits, lines = [], []
    down = urllib.error.URLError(TimeoutError("timed out"))
    fake = FakeResponses([down, ConnectionResetError("reset"), *_goal_script()])
    proposer = FoundryAgentProposer(_config(), fake, sleep=waits.append, log=lines.append)
    review = review_card(match, goal, proposer, strict=True)
    assert waits == [NETWORK_RETRY_WAIT_S] * 2
    causes = [r["cause"] for r in review.attempts[0].calls[0]["retries"]]
    assert causes == ["URLError", "ConnectionResetError"]
    assert lines[0] == f"{GOAL} attempt 1: model call 1"
    assert "  URLError: waiting 5 s, then retrying" in lines
    assert f"{GOAL} attempt 2: model call 1" in lines
    assert not any(KEY in line for line in lines)

    always_down = FakeResponses([urllib.error.URLError("down")] * (NETWORK_RETRIES + 1))
    with pytest.raises(urllib.error.URLError):
        review_card(
            match,
            goal,
            FoundryAgentProposer(_config(), always_down, sleep=waits.append),
            strict=True,
        )
    assert not always_down.replies


def test_rate_limit_waits_are_capped_and_bounded(match, goal):
    assert retry_after_s(_rate_limited({"retry-after": "900"})) == RATE_LIMIT_MAX_WAIT_S
    assert retry_after_s(_rate_limited({"retry-after": "soon"})) == 10.0
    assert retry_after_s(_rate_limited({})) == 10.0
    waits = []
    fake = FakeResponses(
        [_rate_limited({"retry-after": "1"}) for _ in range(RATE_LIMIT_RETRIES + 1)]
    )
    proposer = FoundryAgentProposer(_config(), fake, sleep=waits.append)
    with pytest.raises(urllib.error.HTTPError):
        review_card(match, goal, proposer, strict=True)
    assert waits == [1.0] * RATE_LIMIT_RETRIES and not fake.replies
    other = FakeResponses([urllib.error.HTTPError("u", 500, "err", {}, None)])
    with pytest.raises(urllib.error.HTTPError):  # only 429 is retried
        review_card(
            match, goal, FoundryAgentProposer(_config(), other, sleep=waits.append), strict=True
        )
    assert len(waits) == RATE_LIMIT_RETRIES


def test_failed_retry_drops_the_rejected_claims(match, goal):
    script = _goal_script()[:3] + [OSError("down")]
    review = review_card(match, goal, FoundryAgentProposer(_config(), FakeResponses(script)))
    assert review.proposer == FOUNDRY and len(review.attempts) == 1
    assert review.notes == ("retry unavailable (OSError); rejected claims dropped",)
    # The rejected 4-shot claim is not published; its correction from the metric is.
    texts = [f.text for f in review.published]
    assert not any("Shots: 4" in t for t in texts)
    assert any(t.startswith("Northbridge Rovers — Shots: 3") for t in texts)


# --- Recorded live runs -------------------------------------------------------------------


def test_recorded_trace_replays_with_recomputed_verdicts(match, card_set, tmp_path):
    goal_only = type(card_set)(
        card_set.mode, tuple(c for c in card_set.cards if c.card_id == GOAL), ()
    )
    live, trace = live_review(match, goal_only, _config(), FakeResponses(_goal_script()))
    assert trace["schema"] == TRACE_SCHEMA and trace["store"] is False
    assert trace["endpoint_host"] == "example.openai.azure.com" and KEY not in json.dumps(trace)
    path = tmp_path / "trace.json"
    path.write_text(json.dumps(trace), encoding="utf-8")

    proposer = RecordedProposer(json.loads(path.read_text(encoding="utf-8")), "trace.json")
    (replayed,) = review_cards(match, goal_only, proposer)
    assert replayed.proposer == RECORDED and "not a live call" in replayed.proposer_label
    assert [(v.source, v.status) for v in replayed.verdicts] == [
        (RECORDED if v.source == FOUNDRY else v.source, v.status) for v in live[0].verdicts
    ]
    assert replayed.published == live[0].published

    other = generate_match(2)
    other_card = build_cards(other, ANALYST).cards[0]
    fallback = review_card(other, other_card, proposer)
    assert fallback.proposer == OFFLINE and "ValueError" in fallback.fallback_reason


def test_bundle_carries_reviews_for_analyst_cards(match):
    bundle = json.loads(dumps(build_bundle(match)))
    assert set(bundle["reviews"]) == {c["card_id"] for c in bundle["cards"]["analyst"]}
    review = bundle["reviews"][GOAL]
    injected = [v for v in review["verdicts"] if v["source"] == INJECTED]
    assert len(injected) == 1 and injected[0]["status"] == REJECTED
    assert injected[0]["correction"]["metric_ids"] == ["home.goals:asof:ml1-01112"]
    for review in bundle["reviews"].values():
        for v in review["verdicts"]:
            for fact in (v["fact"], v["correction"]):
                assert fact is None or all(m in bundle["metrics"] for m in fact["metric_ids"])
    assert dumps(build_bundle(match)) == dumps(build_bundle(generate_match(1)))


# --- CLI ---------------------------------------------------------------------------------


def test_cli_offline_review(capsys):
    assert main(["agent-review", "--seed", "1", "--offline"]) == 0
    out = capsys.readouterr().out
    assert "Offline deterministic proposer" in out
    assert "reason: claimed 3 but home.goals:asof:ml1-01112 = 2" in out


def test_cli_live_review_needs_azure_settings(capsys, monkeypatch):
    for var in narrative.ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    assert main(["agent-review", "--seed", "1"]) == 2
    assert "or pass --offline" in capsys.readouterr().err
    assert main(["agent-review", "--seed", "1", "--offline", "--out", "x.json"]) == 2


class EvidenceAgent:
    """Fake agent: looks up the card's evidence events, then cites them (or claims nothing)."""

    def __init__(self, claims=True, usage=30):
        self.claims, self.usage = claims, usage
        self.calls = 0

    def __call__(self, url, headers, body, timeout):
        self.calls += 1
        items = json.loads(body)["input"]
        brief = items[1]["content"]
        events = re.search(r"Evidence events: (.*)\.", brief).group(1).split(", ")
        if not any(i.get("type") == "function_call_output" for i in items):
            output = [_call("get_events", {"event_ids": events}, f"c{self.calls}")]
        else:
            output = [_final([_claim(cited=events)] if self.claims else [])]
        reply = _response(f"resp_{self.calls}", output)
        reply["usage"]["output_tokens"] = self.usage
        return json.dumps(reply).encode()


def _azure_env(monkeypatch):
    values = ("https://example.openai.azure.com", KEY, "gpt-4.1-mini")
    for var, value in zip(narrative.ENV_VARS, values, strict=True):
        monkeypatch.setenv(var, value)


def test_cli_live_review_writes_redacted_trace(capsys, monkeypatch, tmp_path):
    _azure_env(monkeypatch)
    monkeypatch.setattr(narrative, "_urllib_transport", EvidenceAgent())
    out = tmp_path / "trace.json"
    assert main(["agent-review", "--seed", "1", "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert KEY not in text and KEY not in capsys.readouterr().out
    trace = json.loads(text)
    assert len(trace["reviews"]) == 4 and trace_problems(trace) == []
    # The committed trace is what a public build would replay.
    site = tmp_path / "site"
    assert main(["export-site", "--offline", "--agent-trace", str(out), "--out", str(site)]) == 0
    bundle = json.loads((site / "data" / "bundle.json").read_text(encoding="utf-8"))
    assert {r["proposer"] for r in bundle["reviews"].values()} == {RECORDED}

    monkeypatch.setattr(narrative, "_urllib_transport", FakeResponses([ValueError("bad")]))
    assert main(["agent-review", "--seed", "1", "--limit", "1"]) == 1  # strict: no fallback
    assert "live agent run failed: ValueError" in capsys.readouterr().err

    monkeypatch.setattr(agent_module.time, "sleep", lambda s: None)
    down = [urllib.error.URLError(TimeoutError("timed out"))] * (NETWORK_RETRIES + 1)
    monkeypatch.setattr(narrative, "_urllib_transport", FakeResponses(down))
    assert main(["agent-review", "--seed", "1", "--limit", "1"]) == 1
    err = capsys.readouterr().err
    assert "[agent] analyst:" in err and "attempt 1: model call 1" in err
    assert "live agent run failed: URLError (TimeoutError: timed out)" in err

    body = json.dumps({"error": {"code": "RateLimitReached", "message": "x"}}).encode()
    limited = [_rate_limited({"retry-after": "0"}, body) for _ in range(RATE_LIMIT_RETRIES + 1)]
    monkeypatch.setattr(narrative, "_urllib_transport", FakeResponses(limited))
    assert main(["agent-review", "--seed", "1", "--limit", "1"]) == 1
    err = capsys.readouterr().err
    assert "HTTP 429 (error code RateLimitReached, retry-after 0 s, still rate-limited" in err


@pytest.mark.parametrize(
    "agent, problem",
    [
        (EvidenceAgent(claims=False), ": no model claim was accepted"),  # empty claim list
        (EvidenceAgent(usage=0), "lacks positive token usage"),
    ],
)
def test_cli_live_run_that_is_not_evidence_records_nothing(
    capsys, monkeypatch, tmp_path, agent, problem
):
    _azure_env(monkeypatch)
    monkeypatch.setattr(narrative, "_urllib_transport", agent)
    out = tmp_path / "trace.json"
    assert main(["agent-review", "--seed", "1", "--limit", "1", "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert "live run is not acceptable evidence" in err and problem in err
    assert not out.exists()


def test_trace_problems_need_tool_calls_and_no_fallback(match, card_set):
    goal_only = type(card_set)(
        card_set.mode, tuple(c for c in card_set.cards if c.card_id == GOAL), ()
    )
    _, trace = live_review(match, goal_only, _config(), FakeResponses(_goal_script()))
    assert trace_problems(trace) == []
    no_tools = json.loads(json.dumps(trace))
    for attempt in no_tools["reviews"][0]["attempts"]:
        for call in attempt["calls"]:
            call["tool_calls"] = []
    assert trace_problems(no_tools) == ["the agent made no tool calls"]
    fallback = json.loads(json.dumps(trace))
    fallback["reviews"][0]["verdicts"][0]["source"] = OFFLINE
    assert f"{GOAL}: holds offline fallback claims" in trace_problems(fallback)
    with pytest.raises(ValueError, match="not acceptable evidence"):
        RecordedProposer(no_tools, "x.json")
    assert trace_problems({"schema": "other"}) == [f"not a {TRACE_SCHEMA} trace"]

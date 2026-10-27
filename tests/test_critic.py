"""Critic agent: meaning review in the personalizer loop, trace v2, and the evaluation against
the editorial review. Fake transports only: not evidence of a live call or of model quality."""

import json
from pathlib import Path

import pytest

from matchlens import narrative
from matchlens.cards import build_cards
from matchlens.cli import main
from matchlens.critic import (
    CRITIC_EVAL,
    EVAL_SCHEMA,
    FoundryCritic,
    critic_eval,
    eval_metrics,
    parse_verdicts,
)
from matchlens.generator import generate_match
from matchlens.narrative import CASUAL, ResponseFormatError
from matchlens.personalize import (
    KEPT,
    PERSONALIZE_REVIEW,
    PERSONALIZE_TRACE,
    PUBLISHED_PERSONALIZE_REVIEW,
    PUBLISHED_PERSONALIZE_TRACE,
    TITLE,
    TRACE_SCHEMA,
    TRACE_SCHEMA_V2,
    VERIFIED,
    FoundryPersonalizer,
    RecordedPersonalizer,
    build_trace,
    known_names,
    personalize_card,
    personalize_cards,
    trace_problems,
)

from .test_personalize import GOAL, KEY, FakeTranslator, _config, _reply


@pytest.fixture(scope="module")
def match():
    return generate_match(1)


@pytest.fixture(scope="module")
def cards(match):
    return build_cards(match, CASUAL)


@pytest.fixture(scope="module")
def card(cards):
    return next(c for c in cards.cards if c.card_id == GOAL)


class FakeCritic:
    """Answers critic requests: ``reject`` maps an attempt number to rejected indexes; the
    attempt is told apart by the rewrite text (the personalizer's retry marks it)."""

    def __init__(self, reject=None, drop=()):
        self.reject, self.drop, self.requests = reject or {}, set(drop), []

    def __call__(self, url, headers, body, timeout):
        req = json.loads(body)
        self.requests.append(req)
        brief = json.loads(req["input"][1]["content"])
        n = len(self.requests)
        verdicts = []
        for line in brief["lines"]:
            if line["index"] in self.drop:
                continue
            bad = line["index"] in self.reject.get(n, ())
            verdicts.append(
                {
                    "index": line["index"],
                    "faithful": not bad,
                    "issue": "added_detail" if bad else "none",
                    "reason": "adds a detail the fact does not state" if bad else "",
                }
            )
        return json.dumps(_reply(f"resp_critic_{n}", {"verdicts": verdicts})).encode()


class Router:
    """One transport for both agents, as in production (same Foundry deployment)."""

    def __init__(self, personalizer, critic):
        self.personalizer, self.critic = personalizer, critic

    def __call__(self, url, headers, body, timeout):
        name = json.loads(body)["text"]["format"]["name"]
        target = self.critic if name == "critic_verdicts" else self.personalizer
        return target(url, headers, body, timeout)


def _run(match, card, critic_fake, strict=False, translator=None, lang="de"):
    translator = translator or FakeTranslator(known_names(match))
    proposer = FoundryPersonalizer(_config(), translator)
    critic = FoundryCritic(_config(), critic_fake)
    return personalize_card(match, card, lang, "away", proposer, strict, critic), translator


# --- Verdict parsing ------------------------------------------------------------------------


def test_verdicts_are_parsed_conservatively():
    text = json.dumps(
        {
            "verdicts": [
                {"index": -1, "faithful": True, "issue": "none", "reason": ""},
                {"index": 0, "faithful": True, "issue": "added_detail", "reason": "fierce"},
                {"index": 1, "faithful": False, "issue": "none", "reason": "odd"},
                {"index": 2, "faithful": False, "issue": "made_up", "reason": "x"},
                {"index": "3", "faithful": True, "issue": "none", "reason": ""},
            ]
        }
    )
    v = parse_verdicts(text)
    assert v[-1].faithful and v[-1].issue == "none"
    assert not v[0].faithful  # "faithful" with an issue is treated as unsure: rejected
    assert v[1].issue == "other" and v[2].issue == "other"
    assert 3 not in v  # malformed index: no verdict, which rejects the line downstream
    with pytest.raises(ResponseFormatError):
        parse_verdicts("not json")


# --- In the personalizer loop ---------------------------------------------------------------


def test_a_critic_rejection_goes_back_to_the_personalizer(match, card):
    critic = FakeCritic(reject={1: (0,)})
    result, translator = _run(match, card, critic)
    by_index = {ln.index: ln for ln in result.lines}
    assert all(ln.status == VERIFIED for ln in result.lines)
    assert by_index[0].attempt == 2 and by_index[1].attempt == 1
    retry = json.loads(translator.requests[1]["input"][1]["content"])
    assert [r["index"] for r in retry["rejected"]] == [0]
    assert retry["rejected"][0]["reasons"] == [
        "critic: added detail: adds a detail the fact does not state"
    ]
    assert len(critic.requests) == 2  # one review per attempt
    second = json.loads(critic.requests[1]["input"][1]["content"])
    assert [ln["index"] for ln in second["lines"]] == [0]  # only the retried line
    assert result.critic == "foundry_critic" and len(result.critic_reviews) == 2


def test_rejected_twice_by_the_critic_keeps_english(match, card):
    result, _ = _run(match, card, FakeCritic(reject={1: (0,), 2: (0,)}))
    line = next(ln for ln in result.lines if ln.index == 0)
    assert line.status == KEPT and line.text == card.facts[0].text
    assert line.reasons == ("critic: added detail: adds a detail the fact does not state",)


def test_the_critic_only_sees_lines_that_passed_the_checker(match, card):
    translator = FakeTranslator(known_names(match), spoil={1: (1,)})  # line 1 gets "99"
    critic = FakeCritic()
    _run(match, card, critic, translator=translator)
    first = json.loads(critic.requests[0]["input"][1]["content"])
    assert 1 not in [ln["index"] for ln in first["lines"]]
    assert "ml1-" not in critic.requests[0]["input"][1]["content"]  # no IDs
    req = critic.requests[0]
    assert req["store"] is False and req["text"]["format"]["name"] == "critic_verdicts"
    assert "tools" not in req


def test_no_verdict_or_a_failed_critic_rejects_the_line(match, card):
    result, _ = _run(match, card, FakeCritic(drop={TITLE}))
    title = next(ln for ln in result.lines if ln.index == TITLE)
    assert title.status == KEPT and title.reasons == ("critic: no verdict",)

    def broken(url, headers, body, timeout):
        raise ResponseFormatError("critic reply is not JSON")

    result, _ = _run(match, card, broken)
    assert all(ln.status == KEPT for ln in result.lines)
    assert all(
        "foundry_critic unavailable (ResponseFormatError)" in ln.reasons for ln in result.lines
    )
    with pytest.raises(ResponseFormatError):
        _run(match, card, broken, strict=True)


# --- Trace v2 -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def recorded(match, cards):
    router = Router(FakeTranslator(known_names(match)), FakeCritic(reject={1: (0,), 5: (TITLE,)}))
    proposer = FoundryPersonalizer(_config(), router)
    critic = FoundryCritic(_config(), router)
    variants = [("de", "neutral"), ("uk", "away")]
    live = personalize_cards(match, cards, proposer, variants, strict=True, critic=critic)
    return live, build_trace(match, live, _config()), variants


def test_a_v2_trace_replays_with_its_recorded_critic(match, cards, recorded):
    live, trace, variants = recorded
    assert trace["schema"] == TRACE_SCHEMA_V2 and trace_problems(trace) == []
    assert KEY not in json.dumps(trace)
    assert any(a.get("critic") for r in trace["runs"] for a in r["attempts"])
    proposer = RecordedPersonalizer(trace, "trace.json")
    assert proposer.critic is not None and "critic agent" in proposer.label
    replay = personalize_cards(match, cards, proposer, variants)
    assert [r.lines for r in replay] == [r.lines for r in live]
    assert any(ln.attempt == 2 for r in replay for ln in r.lines)  # a critic-driven retry


def test_v2_trace_problems_check_the_critic_calls(recorded):
    _, trace, _ = recorded
    bad = json.loads(json.dumps(trace))
    review = next(a["critic"] for r in bad["runs"] for a in r["attempts"] if a.get("critic"))
    review["calls"][0]["response_id"] = ""
    assert any("critic call without a response ID" in p for p in trace_problems(bad))


def test_v1_traces_still_replay_without_a_critic():
    trace = json.loads(Path(PERSONALIZE_TRACE).read_text("utf-8"))
    assert trace["schema"] == TRACE_SCHEMA
    assert RecordedPersonalizer(trace, "t").critic is None


# --- Evaluation against the editorial review ------------------------------------------------


def _labels():
    review = json.loads(Path(PERSONALIZE_REVIEW).read_text("utf-8"))
    return {
        (r["card_id"], r["lang"], r["focus"], int(r["index"]), r["text"])
        for r in review["rejections"]
    }


def _eval_inputs(match, cards):
    trace = json.loads(Path(PERSONALIZE_TRACE).read_text("utf-8"))
    replay = personalize_cards(match, cards, RecordedPersonalizer(trace, PERSONALIZE_TRACE))
    out = []
    for r in replay:
        items = [
            {"index": ln.index, "source": ln.source, "text": ln.text}
            for ln in r.lines
            if ln.status == VERIFIED
        ]
        if items:
            out.append(((r.card_id, r.lang, r.focus), items, "German", "neutral viewers"))
    return out


class OracleCritic:
    """Rejects exactly the labelled lines, plus ``extra`` (line keys) as false alarms."""

    name, label = "oracle", "oracle"

    def __init__(self, labelled, extra=()):
        self.labelled, self.extra = labelled, set(extra)

    def review(self, key, items, language, audience):
        from matchlens.critic import CriticReview, Verdict

        card_id, lang, focus, _ = key
        verdicts = {}
        for it in items:
            k = (card_id, lang, focus, it["index"])
            bad = (*k, it["text"]) in self.labelled or k in self.extra
            verdicts[it["index"]] = Verdict(it["index"], not bad, "other" if bad else "none", "")
        calls = ({"response_id": "resp_x", "usage": {"input_tokens": 1, "output_tokens": 1}},)
        return CriticReview(verdicts, calls)


def test_eval_scores_the_critic_against_the_editorial_labels(match, cards):
    labelled = _labels()
    inputs = _eval_inputs(match, cards)
    # Every line the checker passes: 220 when the run was recorded, 212 since the 56' move line
    # was corrected (its 8 rewrites still say "7 passes" and are now rejected by the checker).
    assert sum(len(items) for _, items, _, _ in inputs) == 212
    extra = {("casual:goal:ml1-00089", "de", "neutral", 0)}
    doc = critic_eval(inputs, OracleCritic(labelled, extra), _config(), "t", "r")
    assert doc["schema"] == EVAL_SCHEMA and len(doc["runs"]) == len(inputs)
    m = eval_metrics(doc, labelled)
    assert (m["lines"], m["caught"], m["missed"], m["false_alarms"]) == (212, 12, 0, 1)
    assert m["recall"] == 1.0 and m["precision"] == round(12 / 13, 3)
    assert m["false_alarm_lines"][0]["line"] == extra.pop()


def test_critic_eval_cli_scores_a_recorded_evaluation(tmp_path, match, cards, capsys):
    labelled = _labels()
    doc = critic_eval(_eval_inputs(match, cards), OracleCritic(labelled), _config(), "t", "r")
    path = tmp_path / "eval.json"
    path.write_text(json.dumps(doc), "utf-8")
    assert main(["critic-eval", "--from", str(path)]) == 0
    summary = json.loads(capsys.readouterr().out.split("\nmissed")[0].split("\nfalse")[0])
    assert summary["caught"] == 12 and summary["missed"] == 0 and summary["recall"] == 1.0


# --- CLI recording with the critic ----------------------------------------------------------


def test_personalize_review_records_the_critic(tmp_path, monkeypatch, match):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", KEY)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
    router = Router(FakeTranslator(known_names(match)), FakeCritic())
    monkeypatch.setattr(narrative, "_urllib_transport", router)
    base = ["personalize-review", "--variants", "uk|away", "--limit", "2"]
    with_critic, without = tmp_path / "v2.json", tmp_path / "v1.json"
    assert main([*base, "--out", str(with_critic)]) == 0
    assert main([*base, "--no-critic", "--out", str(without)]) == 0
    assert json.loads(with_critic.read_text())["schema"] == TRACE_SCHEMA_V2
    assert json.loads(without.read_text())["schema"] == TRACE_SCHEMA
    out = tmp_path / "site"
    args = ["export-site", "--offline", "--single-match", "--personalize-trace", str(with_critic)]
    assert main([*args, "--out", str(out)]) == 0
    personalized = json.loads((out / "data" / "bundle.json").read_text())["personalized"]
    assert personalized["critics"] == ["recorded_foundry_critic"]


# --- The committed runs -----------------------------------------------------------------------


def test_the_recorded_evaluation_scores_as_documented(capsys):
    doc = json.loads(Path(CRITIC_EVAL).read_text("utf-8"))
    assert doc["schema"] == EVAL_SCHEMA and doc["review"] == PERSONALIZE_REVIEW
    m = eval_metrics(doc, _labels())
    assert (m["lines"], m["caught"], m["missed"], m["false_alarms"]) == (220, 8, 4, 13)
    assert (m["recall"], m["precision"]) == (0.667, 0.381)
    assert main(["critic-eval", "--from", CRITIC_EVAL]) == 0
    assert '"caught": 8' in capsys.readouterr().out


def _kept_by(lines):
    by = {"checker": 0, "critic": 0, "editorial": 0}
    for ln in lines:
        if ln.status == KEPT:
            first = ln.reasons[-1] if ln.reasons else ""
            if any(x.startswith("editorial review:") for x in ln.reasons):
                by["editorial"] += 1
            elif first.startswith("critic:"):
                by["critic"] += 1
            else:
                by["checker"] += 1
    return by


def test_the_published_run_replays_with_the_critic_and_the_review(match, cards):
    from .test_personalize import BAD_PHRASES

    trace = json.loads(Path(PUBLISHED_PERSONALIZE_TRACE).read_text("utf-8"))
    review = json.loads(Path(PUBLISHED_PERSONALIZE_REVIEW).read_text("utf-8"))
    assert trace["schema"] == TRACE_SCHEMA_V2 and trace_problems(trace) == []
    assert (
        review["trace"] == PUBLISHED_PERSONALIZE_TRACE
        and "not checked by a human" in (review["reviewer"])
    )
    proposer = RecordedPersonalizer(trace, PUBLISHED_PERSONALIZE_TRACE, review)
    results = personalize_cards(match, cards, proposer)
    lines = [ln for r in results for ln in r.lines]
    assert len(lines) == 232
    # Checker: 4 in the recorded run, plus the 8 rewrites of the corrected 56' move line.
    assert _kept_by(lines) == {"checker": 12, "critic": 10, "editorial": 30}
    assert all(r.fallback_reason is None for r in results)
    assert all(r.critic == "recorded_foundry_critic" for r in results)
    rewritten = [(r.lang, ln.text) for r in results for ln in r.lines if ln.status == VERIFIED]
    bad = (*BAD_PHRASES, "precise passes", "ein Heuristik")
    assert not [t for _, t in rewritten if any(p in t for p in bad)]
    english = ("Full time:", "synthetic")  # fine in English variants, not in German or Ukrainian
    assert not [t for lang, t in rewritten if lang != "en" and any(p in t for p in english)]
    assert "critic agent" in proposer.label and "editorial review" in proposer.label

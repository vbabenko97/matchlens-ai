"""Personalizer agent and its line checker. Fake transports only: not evidence of a live call."""

import json
import re

import pytest

from matchlens import narrative
from matchlens.cards import build_cards
from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.narrative import CASUAL, AzureOpenAIConfig, ResponseFormatError
from matchlens.personalize import (
    FOUNDRY,
    KEPT,
    RECORDED,
    TITLE,
    TRACE_SCHEMA,
    VARIANTS,
    VERIFIED,
    FoundryPersonalizer,
    RecordedPersonalizer,
    build_trace,
    check_line,
    known_names,
    personalize_card,
    personalize_cards,
    personalized_record,
    trace_problems,
)
from matchlens.replay import build_bundle, dumps

KEY = "test-key-never-logged-456"
FT = "casual:full_time:ml1-01399"
GOAL = "casual:goal:ml1-01253"  # 81': Ugo Vale, 2-3
XG = (
    "Northbridge Rovers had more shots (14–11), but Eastvale Athletic had the better chances "
    "(synthetic xG, a heuristic: 3.04 vs 2.05)."
)


@pytest.fixture(scope="module")
def match():
    return generate_match(1)


@pytest.fixture(scope="module")
def names(match):
    return known_names(match)


@pytest.fixture(scope="module")
def cards(match):
    return build_cards(match, CASUAL)


def _card(cards, card_id):
    return next(c for c in cards.cards if c.card_id == card_id)


def _config():
    return AzureOpenAIConfig("https://example.openai.azure.com", KEY, "gpt-4.1-mini")


# --- The line checker ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "text"),
    [
        (
            "de",
            "Northbridge Rovers hatte mehr Schüsse (14–11), aber Eastvale Athletic die besseren "
            "Chancen (synthetisches xG, eine Heuristik: 3,04 zu 2,05).",
        ),
        (
            "uk",
            "Northbridge Rovers мали більше ударів (14–11), але Eastvale Athletic мали кращі "
            "моменти (синтетичний xG, евристика: 3,04 проти 2,05).",
        ),
        (
            "en",
            "Gutted: Northbridge Rovers outshot them 14–11, yet Eastvale Athletic made the better "
            "chances (synthetic xG, a heuristic: 3.04 vs 2.05).",
        ),
    ],
)
def test_faithful_rewrites_pass(lang, text, names):
    assert check_line(XG, text, lang, names) == []


def test_score_and_minute_formats_may_change(names):
    source = "Ugo Vale scores in minute 81: 2-3."
    assert check_line(source, "Ugo Vale trifft in der 81. Minute zum 2:3!", "de", names) == []
    assert check_line(source, "Ugo Vale забиває на 81-й хвилині: 2:3!", "uk", names) == []


@pytest.mark.parametrize(
    ("lang", "text", "reason"),
    [
        ("de", "Ugo Vale trifft in der 82. Minute zum 2:3!", "numbers differ"),
        ("de", "Ugo Vale trifft zum 2:3!", "missing 81"),
        ("de", "Ugo Vale trifft in der 81. Minute zum 2:3, sein 2. Tor!", "not in the fact: 2"),
        ("de", "Teo Kestrel trifft in der 81. Minute zum 2:3!", "adds a name not in the fact"),
        ("uk", "Уго Вале забиває на 81-й хвилині: 2:3!", "name changed or missing: Ugo Vale"),
        ("de", "Ugo Vale trifft in der 81. Minute zum 2:3! <b>Wow</b>", "markup or a link"),
        ("de", "Ugo Vale trifft in der 81. Minute zum 2:3! https://x.example", "markup or a link"),
        ("de", "Ugo Vale забиває на 81-й хвилині: 2:3!", "Cyrillic letters in a German text"),
        ("uk", "Ugo Vale scores in minute 81: 2-3, what a goal!", "not written in Ukrainian"),
        ("de", "Ugo Vale scores in minute 81: 2-3.", "not translated"),
        ("de", "   ", "empty"),
        ("de", "Ugo Vale trifft in der 81. Minute zum 2:3! " + "Wahnsinn! " * 20, "too long"),
    ],
)
def test_unfaithful_rewrites_are_rejected(lang, text, reason, names):
    reasons = check_line("Ugo Vale scores in minute 81: 2-3.", text, lang, names)
    assert any(reason in r for r in reasons), reasons


def test_counting_in_words_is_fine_but_a_new_digit_is_not(names):
    source = "The lead changed hands twice."
    assert check_line(source, "Die Führung wechselte zweimal.", "de", names) == []
    assert check_line(source, "Die Führung wechselte 2 Mal.", "de", names) != []


def test_xg_must_stay(names):
    text = (
        "Northbridge Rovers hatte mehr Schüsse (14–11), aber Eastvale Athletic die besseren "
        "Chancen (3,04 zu 2,05)."
    )
    assert "drops 'xG'" in check_line(XG, text, "de", names)


def test_a_shared_surname_is_not_mistaken_for_the_other_player(names):
    assert "Rafe Pembrook" in names and "Sami Pembrook" in names
    source = "Rafe Pembrook scores in minute 5: 0-1."
    assert check_line(source, "Rafe Pembrook trifft in Minute 5: 0:1.", "de", names) == []
    reasons = check_line(source, "Sami Pembrook trifft in Minute 5: 0:1.", "de", names)
    assert "adds a name not in the fact: Sami Pembrook" in reasons


# --- The agent loop (fake Responses API) ---------------------------------------------------

_WORD = re.compile(r"[A-Za-z]+")


def fake_rewrite(text, lang, names):
    """A stand-in translator that keeps names, digits and 'xG' and replaces other words."""
    kept = {}
    for i, name in enumerate(sorted(names, key=len, reverse=True)):
        if name in text:
            kept[f"\x00{i}\x00"] = name
            text = text.replace(name, f"\x00{i}\x00")
    word = {"de": "Wort", "uk": "слово", "en": "word"}[lang]
    text = _WORD.sub(lambda m: m.group(0) if m.group(0) == "xG" else word, text)
    for token, name in kept.items():
        text = text.replace(token, name)
    return text


def _reply(rid, payload):
    return {
        "id": rid,
        "model": "gpt-4.1-mini",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(payload)}],
            }
        ],
        "usage": {"input_tokens": 300, "output_tokens": 120},
    }


class FakeTranslator:
    """Reads the brief from each request and answers like a faithful model; ``spoil`` maps an
    attempt number to the line indexes it gets wrong (a changed minute)."""

    def __init__(self, names, spoil=None):
        self.names, self.spoil, self.requests = names, spoil or {}, []

    def __call__(self, url, headers, body, timeout):
        req = json.loads(body)
        self.requests.append(req)
        brief = json.loads(req["input"][1]["content"])
        lang = {"English": "en", "German": "de", "Ukrainian": "uk"}[brief["language"]]
        attempt = 2 if "rejected" in brief else 1
        bad = self.spoil.get(attempt, ())

        def line(i, text):
            out = fake_rewrite(text, lang, self.names)
            return out + " 99" if i in bad else out

        payload = {
            "title": line(TITLE, brief["title"]),
            "lines": [
                {"index": f["index"], "text": line(f["index"], f["text"])} for f in brief["facts"]
            ],
        }
        return json.dumps(_reply(f"resp_{len(self.requests)}", payload)).encode()


def test_every_line_is_checked_and_rejections_go_back_once(match, cards, names):
    fake = FakeTranslator(names, spoil={1: (0, TITLE)})
    card = _card(cards, GOAL)
    result = personalize_card(match, card, "uk", "away", FoundryPersonalizer(_config(), fake))
    assert result.proposer == FOUNDRY and result.fallback_reason is None
    assert [ln.status for ln in result.lines] == [VERIFIED] * (len(card.facts) + 1)
    by_index = {ln.index: ln for ln in result.lines}
    assert by_index[TITLE].attempt == 2 and by_index[0].attempt == 2 and by_index[1].attempt == 1
    retry = json.loads(fake.requests[1]["input"][1]["content"])
    assert [r["index"] for r in retry["rejected"]] == [TITLE, 0]
    assert all("not in the fact: 99" in " ".join(r["reasons"]) for r in retry["rejected"])
    assert retry["audience"] == "fans of Eastvale Athletic" and retry["language"] == "Ukrainian"


def test_a_line_rejected_twice_keeps_its_english_source(match, cards, names):
    fake = FakeTranslator(names, spoil={1: (1,), 2: (1,)})
    card = _card(cards, GOAL)
    result = personalize_card(match, card, "de", "neutral", FoundryPersonalizer(_config(), fake))
    kept = [ln for ln in result.lines if ln.status == KEPT]
    assert [ln.index for ln in kept] == [1] and kept[0].text == card.facts[1].text
    assert any("99" in r for r in kept[0].reasons) and kept[0].attempt == 0
    assert len(fake.requests) == 2


def test_the_request_carries_only_the_cards_verified_text(match, cards, names):
    fake = FakeTranslator(names)
    card = _card(cards, FT)
    personalize_card(match, card, "de", "home", FoundryPersonalizer(_config(), fake))
    req = fake.requests[0]
    assert req["store"] is False and req["text"]["format"]["type"] == "json_schema"
    assert "tools" not in req
    brief = json.loads(req["input"][1]["content"])
    assert brief["audience"] == "fans of Northbridge Rovers"
    assert [f["text"] for f in brief["facts"]] == [f.text for f in card.facts]
    assert "ml1-" not in req["input"][1]["content"]  # no IDs: the agent never cites


def test_a_missing_line_is_rejected_and_then_kept(match, cards, names):
    def drop_first(url, headers, body, timeout):
        reply = json.loads(FakeTranslator(names)(url, headers, body, timeout))
        payload = json.loads(reply["output"][0]["content"][0]["text"])
        payload["lines"] = payload["lines"][1:]
        return json.dumps(_reply(reply["id"], payload)).encode()

    card = _card(cards, GOAL)
    result = personalize_card(
        match, card, "de", "neutral", FoundryPersonalizer(_config(), drop_first)
    )
    line0 = next(ln for ln in result.lines if ln.index == 0)
    assert line0.status == KEPT and line0.reasons == ("missing from the reply",)


def test_a_broken_reply_fails_a_strict_run_and_keeps_english_otherwise(match, cards, names):
    def garbage(url, headers, body, timeout):
        reply = _reply("resp_x", {})
        reply["output"][0]["content"][0]["text"] = "not json"
        return json.dumps(reply).encode()

    card = _card(cards, GOAL)
    proposer = FoundryPersonalizer(_config(), garbage)
    with pytest.raises(ResponseFormatError):
        personalize_card(match, card, "de", "neutral", proposer, strict=True)
    result = personalize_card(match, card, "de", "neutral", proposer)
    assert all(ln.status == KEPT for ln in result.lines)
    assert result.fallback_reason == "foundry_personalizer unavailable (ResponseFormatError)"


# --- Recorded runs and the bundle -----------------------------------------------------------


@pytest.fixture(scope="module")
def recorded(match, cards, names):
    fake = FakeTranslator(names, spoil={1: (0,), 2: ()})
    live = personalize_cards(match, cards, FoundryPersonalizer(_config(), fake), strict=True)
    return live, build_trace(match, live, _config())


def test_a_recorded_run_replays_to_the_same_lines(match, cards, recorded):
    live, trace = recorded
    assert trace["schema"] == TRACE_SCHEMA and trace_problems(trace) == []
    assert KEY not in json.dumps(trace)
    assert len(trace["runs"]) == len(VARIANTS) * len(cards.cards)
    replay = personalize_cards(match, cards, RecordedPersonalizer(trace, "trace.json"))
    assert [r.lines for r in replay] == [r.lines for r in live]
    assert {r.proposer for r in replay} == {RECORDED}


def test_trace_problems_refuse_weak_evidence(recorded):
    _, trace = recorded
    bad = json.loads(json.dumps(trace))
    bad["runs"][0]["attempts"][0]["calls"][0]["response_id"] = ""
    bad["runs"][1]["attempts"][0]["calls"][0]["usage"]["output_tokens"] = 0
    bad["runs"][2]["lang"] = "fr"
    bad["runs"][3]["attempts"][0]["calls"][0]["note"] = "api-key: x"
    problems = trace_problems(bad)
    assert any("response ID" in p for p in problems)
    assert any("token usage" in p for p in problems)
    assert any("unknown variant" in p for p in problems)
    assert any("key or auth" in p for p in problems)
    with pytest.raises(ValueError):
        RecordedPersonalizer(bad, "bad.json")


def test_bundle_carries_checked_variants_with_the_facts_ids(match, cards, recorded):
    _, trace = recorded
    bundle = json.loads(dumps(build_bundle(match, personalizer=RecordedPersonalizer(trace, "t"))))
    p = bundle["personalized"]
    assert set(p["variants"]) == {f"{a}|{b}" for a, b in VARIANTS}
    assert p["proposers"] == [RECORDED] and "not a live call" in p["label"]
    card = bundle["cards"]["casual"][0]
    lines = p["variants"]["uk|away"]["cards"][card["card_id"]]["lines"]
    assert lines[0]["index"] == TITLE and lines[0]["source"] == card["title"]
    assert [ln["source"] for ln in lines[1:]] == [f["text"] for f in card["facts"]]
    assert all(ln["status"] == VERIFIED for ln in lines)
    assert "personalized" not in build_bundle(match)  # nothing without a recorded run


def test_personalized_record_lists_response_ids(match, cards, recorded):
    live, _ = recorded
    rec = personalized_record(live[:1], "label")
    ids = rec["variants"][f"{live[0].lang}|{live[0].focus}"]["cards"][live[0].card_id][
        "response_ids"
    ]
    assert ids and all(i.startswith("resp_") for i in ids)


# --- CLI -------------------------------------------------------------------------------------


def test_personalize_review_records_a_trace_the_site_can_replay(tmp_path, monkeypatch, names):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", KEY)
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")
    monkeypatch.setattr(narrative, "_urllib_transport", FakeTranslator(names))
    trace = tmp_path / "trace.json"
    args = ["personalize-review", "--variants", "de|neutral,uk|away", "--limit", "2"]
    assert main([*args, "--out", str(trace)]) == 0
    doc = json.loads(trace.read_text())
    assert {(r["lang"], r["focus"]) for r in doc["runs"]} == {("de", "neutral"), ("uk", "away")}
    assert KEY not in trace.read_text()
    out = tmp_path / "site"
    cmd = ["export-site", "--offline", "--single-match", "--personalize-trace", str(trace)]
    assert main([*cmd, "--out", str(out)]) == 0
    bundle = json.loads((out / "data" / "bundle.json").read_text())
    assert set(bundle["personalized"]["variants"]) == {"de|neutral", "uk|away"}


def test_personalize_review_needs_azure_and_known_variants(monkeypatch):
    for var in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(var, raising=False)
    assert main(["personalize-review"]) == 2
    assert main(["personalize-review", "--variants", "fr|neutral"]) == 2


# --- Score order, Russian letters, and the committed run -----------------------------------


def test_a_score_keeps_its_home_first_order(names):
    source = "Eastvale Athletic were 2-1 down in minute 71 and won 2-3."
    ok = "Eastvale Athletic lagen in der 71. Minute 2:1 zurück und gewannen 2:3."
    flipped = "Eastvale Athletic lagen in der 71. Minute mit 1-2 zurück und gewannen 2-3."
    assert check_line(source, ok, "de", names) == []
    reasons = check_line(source, flipped, "de", names)
    assert reasons == ["score order changed (keep 2-1, 2-3, home first)"]
    # "minute 81: 2-3" holds one score, not "81-2".
    assert (
        check_line("Ugo Vale scores in minute 81: 2-3.", "Ugo Vale trifft (81.): 2:3", "de", names)
        == []
    )


def test_russian_letters_are_not_ukrainian(names):
    source = "Goals in minutes 77 and 81 turned it around."
    assert check_line(source, "Голи на 77-й і 81-й хвилинах змінили хід гри.", "uk", names) == []
    reasons = check_line(source, "Голы в 77 і 81 хвилинах змінили хід гри.", "uk", names)
    assert reasons == ["Russian letters in a Ukrainian text"]


def test_the_committed_run_replays_with_the_flipped_scores_kept_in_english(match, cards):
    from pathlib import Path

    from matchlens.personalize import PERSONALIZE_TRACE

    trace = json.loads((Path(__file__).parents[1] / PERSONALIZE_TRACE).read_text("utf-8"))
    assert trace_problems(trace) == [] and len(trace["runs"]) == len(VARIANTS) * len(cards.cards)
    results = personalize_cards(match, cards, RecordedPersonalizer(trace, PERSONALIZE_TRACE))
    lines = [(r, ln) for r in results for ln in r.lines]
    kept = [(r, ln) for r, ln in lines if ln.status == KEPT]
    assert len(lines) == 232 and len(kept) == 12
    # The worst slip of the run: "Eastvale Athletic перемагають 3-2" on a 2-3 scoreboard.
    title = next(
        ln
        for r, ln in kept
        if (r.lang, r.focus, r.card_id) == ("uk", "away", FT) and ln.index == TITLE
    )
    assert "3-2" not in title.text and title.text == title.source
    assert all("3-2" not in ln.text for _, ln in lines)
    assert not any("ы" in ln.text for _, ln in lines)


# --- Editorial review of the recorded run -----------------------------------------------------


def _committed():
    from pathlib import Path

    from matchlens.personalize import PERSONALIZE_REVIEW, PERSONALIZE_TRACE

    root = Path(__file__).parents[1]
    trace = json.loads((root / PERSONALIZE_TRACE).read_text("utf-8"))
    review = json.loads((root / PERSONALIZE_REVIEW).read_text("utf-8"))
    return trace, review


BAD_PHRASES = (
    "fierce",
    "quick passes",
    "sharp",
    "dominated",
    "knapp vergeben",
    "Нічия",
    "Голі",
    "Повний час",
    "відійшли назад",
    "Впевненість",
)


def test_the_editorial_review_keeps_the_rejected_lines_in_english(match, cards):
    trace, review = _committed()
    proposer = RecordedPersonalizer(trace, "trace", review)
    results = personalize_cards(match, cards, proposer)
    lines = [ln for r in results for ln in r.lines]
    reviewed = [ln for ln in lines if any(x.startswith("editorial review:") for x in ln.reasons)]
    assert len(lines) == 232 and len(reviewed) == len(review["rejections"]) == 12
    assert sum(ln.status == KEPT for ln in lines) == 24
    assert all(ln.text == ln.source and ln.attempt == 0 for ln in reviewed)
    shown = " ".join(ln.text for ln in lines)
    assert not [p for p in BAD_PHRASES if p in shown]
    assert "editorial review" in proposer.label


def test_a_review_must_match_the_replayed_text(match, cards):
    trace, review = _committed()
    changed = json.loads(json.dumps(review))
    changed["rejections"][0]["text"] += " (edited)"
    with pytest.raises(ValueError, match="does not match"):
        personalize_cards(match, cards, RecordedPersonalizer(trace, "t", changed))
    stale = json.loads(json.dumps(review))
    stale["rejections"][0]["card_id"] = "casual:goal:ml1-99999"
    with pytest.raises(ValueError, match="does not have"):
        personalize_cards(match, cards, RecordedPersonalizer(trace, "t", stale))


def test_review_documents_are_validated():
    from matchlens.personalize import load_review

    _, review = _committed()
    with pytest.raises(ValueError):
        load_review({"schema": "other", "rejections": []})
    twice = {**review, "rejections": review["rejections"][:1] * 2}
    with pytest.raises(ValueError, match="repeated"):
        load_review(twice)
    no_reason = {**review, "rejections": [{**review["rejections"][0], "reason": " "}]}
    with pytest.raises(ValueError, match="reason"):
        load_review(no_reason)


def test_editorial_review_needs_a_trace(tmp_path):
    from matchlens.personalize import PERSONALIZE_REVIEW

    args = ["export-site", "--offline", "--single-match", "--editorial-review", PERSONALIZE_REVIEW]
    assert main([*args, "--out", str(tmp_path / "s")]) == 2

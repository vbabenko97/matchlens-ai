"""Browser end-to-end tests of the replay UI (Playwright + Chromium).

Targets, in order:
- ``MATCHLENS_BASE_URL`` set (e.g. the public deployment): only that URL; the expected
  data is read from its own ``data/bundle.json``.
- otherwise, two local targets: the ``matchlens serve`` app server, and a plain static file
  server hosting ``export-site`` output under a sub-path (as GitHub Pages / Azure Static
  Web Apps would), proving the demo needs no Python server.

Skipped when Playwright or a Chromium build is unavailable. Locally, set
MATCHLENS_CHROMIUM to a Chromium executable if Playwright's bundled browser is missing.
"""

import functools
import http.server
import json
import os
import re
import threading
import urllib.request
from pathlib import Path

import pytest

from matchlens.agent import DEMO_TRACE, RecordedProposer
from matchlens.generator import generate_match
from matchlens.personalize import (
    PUBLISHED_PERSONALIZE_REVIEW,
    PUBLISHED_PERSONALIZE_TRACE,
    RecordedPersonalizer,
)
from matchlens.recap import format_recap
from matchlens.replay import DEFAULT_DEMO_SEED, build_bundle, demo_extra_bundles, dumps
from matchlens.server import export_site, make_server

playwright_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.e2e
PUBLIC_URL = os.environ.get("MATCHLENS_BASE_URL", "").strip()
ROOT = Path(__file__).resolve().parent.parent
TARGETS = ["public"] if PUBLIC_URL else ["app-server", "static-subpath"]


@functools.cache
def _extras():
    """The extra matches of the production build (built once per test session)."""
    return json.loads(dumps(demo_extra_bundles()))


def _start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture(scope="module", params=TARGETS)
def target(request, tmp_path_factory):
    """Yield ``(base_url, bundle)`` for one deployment target."""
    if request.param == "public":
        base = PUBLIC_URL if PUBLIC_URL.endswith("/") else PUBLIC_URL + "/"
        with urllib.request.urlopen(base + "data/bundle.json", timeout=30) as resp:
            yield base, json.load(resp)
        return
    # The same bundle the public build serves: replaying the recorded Foundry agent run.
    trace = json.loads((ROOT / DEMO_TRACE).read_text(encoding="utf-8"))
    proposer = RecordedProposer(trace, DEMO_TRACE)
    personalizer = RecordedPersonalizer(
        json.loads((ROOT / PUBLISHED_PERSONALIZE_TRACE).read_text(encoding="utf-8")),
        PUBLISHED_PERSONALIZE_TRACE,
        json.loads((ROOT / PUBLISHED_PERSONALIZE_REVIEW).read_text(encoding="utf-8")),
    )
    match = generate_match(DEFAULT_DEMO_SEED)
    bundle = json.loads(dumps(build_bundle(match, proposer=proposer, personalizer=personalizer)))
    extras = _extras()
    if request.param == "app-server":
        srv = _start(make_server(bundle, port=0, extras=extras))
        yield f"http://127.0.0.1:{srv.server_address[1]}/", bundle
    else:
        root = tmp_path_factory.mktemp("static")
        export_site(bundle, root / "matchlens-ai", extras)
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        handler.log_message = lambda *args: None
        srv = _start(http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler))
        yield f"http://127.0.0.1:{srv.server_address[1]}/matchlens-ai/", bundle
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def base_url(target):
    return target[0]


@pytest.fixture(scope="module")
def bundle(target):
    return target[1]


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:
            path = os.environ.get("MATCHLENS_CHROMIUM")
            if not path:
                pytest.skip("no Playwright Chromium available")
            b = p.chromium.launch(executable_path=path)
        yield b
        b.close()


@pytest.fixture
def page(browser, base_url):
    pg = browser.new_page(viewport={"width": 1400, "height": 900}, accept_downloads=True)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.base = base_url
    pg.expected_errors = []  # substrings of console errors a test provokes on purpose
    yield pg
    assert [e for e in errors if not any(x in e for x in pg.expected_errors)] == []
    pg.close()


def _open(page, query=""):
    page.goto(page.base + query)
    page.wait_for_selector("body[data-ready=true]")


def test_initial_state(page, bundle):
    _open(page)
    assert page.text_content("#home-name") == bundle["meta"]["home"]["name"]
    assert page.text_content("#home-score") == "0" and page.text_content("#away-score") == "0"
    assert page.text_content("#clock") == "00:00"
    assert page.locator("#cards .card").count() == 0  # no spoilers before kick-off
    disclosure = page.text_content("#disclosure")
    if bundle["provider"] == "offline_template":
        assert "wording comes from deterministic templates, not from a model" in disclosure
        assert "not an Azure response" not in disclosure  # template wording is the design here
    else:
        assert "NOT verified" in disclosure
    assert page.locator("#markers .marker").count() >= 4


def test_seek_to_full_time_shows_final_score_and_cards(page, bundle):
    _open(page)
    page.evaluate("window.matchlens.seek(5400)")
    home, away = bundle["final_score"]
    assert page.text_content("#home-score") == str(home)
    assert page.text_content("#away-score") == str(away)
    assert page.text_content("#period") == "FT"
    assert page.locator("#cards .card").count() == len(bundle["cards"]["casual"])
    # Seek via the timeline slider back to the start hides the cards again.
    page.fill("#seek", "0")
    page.dispatch_event("#seek", "input")
    assert page.locator("#cards .card").count() == 0


def test_stats_change_with_the_scoreboard(page, bundle):
    """Shots and xG count a goal from the moment the scoreboard shows it, not a minute later."""
    events = bundle["events"]
    goal = next(e for e in events if e["type"] == "shot" and e["outcome"] == "goal")
    _open(page)
    page.evaluate(f"window.matchlens.seek({goal['clock_s']})")
    shots = [e for e in events if e["type"] == "shot" and e["clock_s"] <= goal["clock_s"]]
    counts = [sum(e["team"] == side for e in shots) for side in ("home", "away")]
    assert page.text_content("#stat-shots") == f"{counts[0]} – {counts[1]}"
    assert page.text_content("#home-score") == str(goal["home_score"])
    xg = [sum(e["xg_heuristic"] for e in shots if e["team"] == s) for s in ("home", "away")]
    assert page.text_content("#stat-xg") == f"{xg[0]:.2f} – {xg[1]:.2f}"


def test_play_pause_and_speed(page):
    _open(page)
    page.select_option("#speed", "240")
    page.click("#play")
    # The page CSP forbids eval, so poll with evaluate() instead of wait_for_function().
    for _ in range(100):
        if page.evaluate("window.matchlens.state.t") > 120:
            break
        page.wait_for_timeout(50)
    assert page.evaluate("window.matchlens.state.t") > 120
    page.click("#play")
    t1 = page.evaluate("window.matchlens.state.t")
    page.wait_for_timeout(300)
    assert page.evaluate("window.matchlens.state.t") == t1  # paused
    assert page.evaluate("window.matchlens.state.speed") == 240
    assert page.get_attribute("#play", "aria-label") == "Play"


def test_cards_sync_with_moment_markers(page, bundle):
    first = bundle["cards"]["casual"][0]
    _open(page)
    page.click("#next")
    assert page.evaluate("window.matchlens.state.t") == first["publish_clock_s"]
    card = page.locator(f'#cards .card[data-card-id="{first["card_id"]}"]')
    assert card.count() == 1 and "active" in card.get_attribute("class")
    assert page.is_visible("#lower-third")
    assert page.text_content("#lt-title") == first["title"]


def test_card_waits_for_its_last_evidence_event(page, bundle):
    """A card whose evidence ends after its anchor stays hidden until that event happens."""
    card = next(
        c
        for mode in ("casual", "analyst")
        for c in bundle["cards"][mode]
        if c["publish_clock_s"] > c["anchor_clock_s"]
    )
    _open(page, f"?mode={card['mode']}")
    locator = page.locator(f'#cards .card[data-card-id="{card["card_id"]}"]')
    page.evaluate(f"window.matchlens.seek({card['anchor_clock_s']})")
    assert locator.count() == 0
    page.evaluate(f"window.matchlens.seek({card['publish_clock_s']})")
    assert locator.count() == 1


def test_modes_show_different_content(page, bundle):
    _open(page, "?t=5400")
    casual_text = page.text_content("#cards")
    assert page.locator("#cards .chip").count() == 0 and not page.is_visible("#inspector")
    page.click(".mode-toggle button[data-mode=analyst]")
    assert page.get_attribute(".mode-toggle button[data-mode=analyst]", "aria-checked") == "true"
    analyst_text = page.text_content("#cards")
    assert casual_text != analyst_text and "[evidence:" in analyst_text
    assert page.locator("#cards .card").count() == len(bundle["cards"]["analyst"])


def test_analyst_evidence_is_inspectable(page, bundle):
    _open(page, "?mode=analyst&t=5400")
    chip = page.locator("#cards .chip[data-event-id]").first
    event_id = chip.get_attribute("data-event-id")
    chip.click()
    shown = json.loads(page.text_content("#inspector-json"))
    expected = next(e for e in bundle["events"] if e["event_id"] == event_id)
    assert shown == expected
    assert page.locator("#highlight > *").count() >= 1  # highlighted on the pitch
    page.locator("#cards .chip.metric").first.click()
    assert "Definition" in page.text_content("#inspector-body")


def test_claim_check_shows_rejection_correction_and_sources(page, bundle):
    review = next(
        r
        for r in bundle["reviews"].values()
        if any(v["source"] == "injected_test_claim" for v in r["verdicts"])
    )
    injected = next(v for v in review["verdicts"] if v["source"] == "injected_test_claim")
    _open(page, "?mode=analyst&t=5400")
    card = page.locator(f'#cards .card[data-card-id="{review["card_id"]}"]')
    assert card.locator(".review .explain").text_content() == " ".join(review["explanation"])
    claims = card.locator(".review details.claims")
    assert not claims.evaluate("d => d.open")  # the claim list is folded under the summary
    claims.locator("summary").first.click()
    row = card.locator('.verdicts li[data-source="injected_test_claim"]')
    assert "rejected" in row.get_attribute("class")
    reasons = row.locator(".reason").all_text_contents()
    assert reasons == [f"Rejected: {r}" for r in injected["reasons"]]
    assert injected["correction"]["text"] in row.locator(".correction").text_content()
    assert card.locator(".review .hint").first.text_content() == review["proposer_label"]
    metric_id = injected["correction"]["metric_ids"][0]
    row.locator(f'.chip[data-metric-id="{metric_id}"]').click()
    assert f"Metric {metric_id}" in page.text_content("#inspector-body")
    assert "Unknown metric" not in page.text_content("#inspector-body")
    assert claims.evaluate("d => d.open")  # stays open when the cards re-render
    assert page.locator("#cards .verdicts li.ok").count() >= 1
    retried = [r for r in bundle["reviews"].values() if len(r["attempts"]) > 1]
    for r in retried:  # a claim fixed by the agent's retry is not shown as a published correction
        rows = page.locator(f'#cards .card[data-card-id="{r["card_id"]}"] .verdicts li.rejected')
        assert rows.count() >= 1
        for i in range(rows.count()):
            assert rows.nth(i).locator(".correction").count() == 0
            assert "Sent back to the agent" in rows.nth(i).text_content()
    if review["proposer"] == "recorded_foundry_agent":
        assert "replay a recorded Microsoft Foundry agent run" in page.text_content("#disclosure")
        assert "not a live call" in review["proposer_label"]
    _open(page, "?t=5400")
    assert page.locator("#cards .review").count() == 0  # casual mode shows no claim check


def test_disclosure_states_the_stream_check(page, bundle):
    _open(page)
    text = page.text_content("#disclosure")
    if "stream" not in bundle:
        pytest.skip("this build predates the stream check")
    assert f"this match gives exactly these {bundle['stream']['cards']} cards" in text
    assert "(matchlens stream)" in text


def test_narrative_is_labelled_not_verified_as_fact(page):
    _open(page, "?t=5400")
    statuses = page.locator("#cards .narrative .status").all_text_contents()
    allowed = ("Template wording · no AI", "AI wording · NOT VERIFIED")
    assert statuses and all(s in allowed for s in statuses)
    template = page.locator("#cards .narrative .status.template").first
    assert "no model wrote this wording" in template.get_attribute("title")
    checks = page.locator("#cards .narrative .check").count()
    assert checks == 0  # ✓ marks only verified facts, never the narrative


def test_download_overlay_json(page, bundle):
    _open(page)
    with page.expect_download() as info:
        page.click("#download")
    download = info.value
    assert download.suggested_filename == f"matchlens-overlays-{bundle['meta']['match_id']}.json"
    with open(download.path(), encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc == bundle["overlays"]


def test_mobile_layout_has_no_horizontal_scroll(browser, base_url):
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(base_url + "?t=3000")  # base_url fixture comes from the parametrized target
    pg.wait_for_selector("body[data-ready=true]")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.close()


# --- Accessibility (docs/ACCESSIBILITY.md) ---------------------------------------------


def test_structure_names_and_live_regions(page):
    _open(page, "?mode=analyst&t=5400")
    assert page.locator("h1").count() == 1
    # Ticking content must not be a live region; one polite status region instead.
    assert page.locator("[aria-live]").count() == 1
    assert page.get_attribute("#announcer", "role") == "status"
    unnamed = page.evaluate(
        """[...document.querySelectorAll('button, a[href], input, select')]
             .filter((e) => !(e.getAttribute('aria-label') || e.textContent.trim()
                              || (e.labels && e.labels.length)))
             .map((e) => e.outerHTML)"""
    )
    assert unnamed == []


def test_mode_radiogroup_arrow_keys_and_roving_tabindex(page):
    _open(page)
    casual = page.locator(".mode-toggle button[data-mode=casual]")
    analyst = page.locator(".mode-toggle button[data-mode=analyst]")
    assert (casual.get_attribute("tabindex"), analyst.get_attribute("tabindex")) == ("0", "-1")
    casual.focus()
    page.keyboard.press("ArrowRight")
    assert analyst.get_attribute("aria-checked") == "true"
    assert page.evaluate("document.activeElement.dataset.mode") == "analyst"
    assert (casual.get_attribute("tabindex"), analyst.get_attribute("tabindex")) == ("-1", "0")
    page.keyboard.press("Home")
    assert casual.get_attribute("aria-checked") == "true"


def test_chip_activation_keeps_keyboard_focus(page):
    _open(page, "?mode=analyst&t=5400")
    chip = page.locator("#cards .chip[data-event-id]").first
    event_id = chip.get_attribute("data-event-id")
    chip.focus()
    page.keyboard.press("Enter")
    focused = page.evaluate(
        "(a => [a.dataset.eventId, a.getAttribute('aria-pressed')])(document.activeElement)"
    )
    assert focused == [event_id, "true"]  # same chip, not <body>, after the cards re-render


def test_new_moment_is_announced_once(page, bundle):
    _open(page)
    page.click("#next")
    first = bundle["cards"]["casual"][0]
    page.wait_for_function(
        "(t) => document.getElementById('announcer').textContent.includes(t)", arg=first["title"]
    )


def test_seek_slider_speaks_match_clock(page):
    _open(page, "?t=600")
    assert page.get_attribute("#seek", "aria-valuetext") == "10:00, 1H"


def test_shortcuts_leave_native_controls_alone(page):
    _open(page, "?t=600")
    page.focus("#play")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("window.matchlens.state.t") == 600
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("ArrowRight")
    assert page.evaluate("window.matchlens.state.t") == 630


@pytest.mark.parametrize("query", ["?mode=analyst&t=5400", "?t=5400&view=lineups"])
def test_interactive_targets_are_at_least_24px(page, query):
    _open(page, query)
    targets = (
        "#markers button, #cards .chip, .shortcuts button, #view-toggle button, "
        "#cards details.more > summary, #lineup .lp-dot"
    )
    small = page.evaluate(
        f"""[...document.querySelectorAll('{targets}')]
             .filter((e) => e.getClientRects().length)  // rendered (not in a closed <details>)
             .map((e) => e.getBoundingClientRect())
             .filter((r) => r.width < 24 || r.height < 24).length"""
    )
    assert small == 0


@pytest.mark.parametrize("width", [320, 390, 500])
def test_narrow_screens_reflow_without_overlapping_markers(browser, base_url, width):
    pg = browser.new_page(viewport={"width": width, "height": 800})  # 320px = 1280px at 400%
    pg.goto(base_url + "?t=5400")
    pg.wait_for_selector("body[data-ready=true]")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    overlaps = pg.evaluate(
        """(() => {
             const r = [...document.querySelectorAll('#markers .marker')]
               .map((e) => e.getBoundingClientRect());
             let n = 0;
             for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++)
               if (r[i].left < r[j].right && r[j].left < r[i].right
                   && r[i].top < r[j].bottom && r[j].top < r[i].bottom) n++;
             return n;
           })()"""
    )
    assert overlaps == 0  # buttons and non-interactive goal dots alike
    pg.goto(base_url + "?t=60&mode=analyst")  # long "Last event": 1' <team>: kick off (complete)
    pg.wait_for_selector("body[data-ready=true]")
    clipped = pg.evaluate(
        """[...document.querySelectorAll('.stats > div > span:last-child')]
             .filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.id)"""
    )
    assert clipped == []  # stat values wrap instead of being cut off with an ellipsis
    pg.goto(base_url + "?t=5400")  # casual: the comparison bars, at their widest values
    pg.wait_for_selector("body[data-ready=true]")
    clipped = pg.evaluate(
        """[...document.querySelectorAll('#bars .bar-val, #bars .bar-label')]
             .filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent)"""
    )
    assert clipped == []
    pg.close()


def test_goal_dot_only_for_goals_without_a_card(page, bundle):
    _open(page)
    goals = [e for e in bundle["events"] if e["type"] == "shot" and e.get("outcome") == "goal"]
    carded = {c["anchor_clock_s"] for c in bundle["cards"]["casual"] if c["kind"] == "goal"}
    expected = sum(1 for e in goals if e["clock_s"] not in carded)
    assert page.locator("#markers .goal-tick").count() == expected


# --- Story and shortcuts -----------------------------------------------------------------


def _poll(page, expr, timeout_ms=8000):
    for _ in range(timeout_ms // 50):
        if page.evaluate(expr):
            return True
        page.wait_for_timeout(50)
    return False


def test_every_card_leads_with_why_it_matters(page, bundle):
    _open(page, "?t=5400")
    cards = bundle["cards"]["casual"]
    goals = [e for e in bundle["events"] if e["type"] == "shot" and e["outcome"] == "goal"]
    assert sum(c["kind"] == "goal" for c in cards) == len(goals)  # every goal has a card
    for card in cards:
        why = [f["text"] for f in card["facts"] if f["role"] == "why"]
        shown = page.locator(
            f'#cards .card[data-card-id="{card["card_id"]}"] li.why div > span:last-child'
        )
        assert why and shown.all_text_contents() == why
    ft = page.locator("#cards .card.full_time")
    assert ft.count() == 1 and "Full time" in ft.locator("h3").text_content()
    assert page.text_content("#lt-fact") == next(
        f["text"] for f in cards[-1]["facts"] if f["role"] == "why"
    )


def test_turning_point_shortcut_plays_into_the_story(page, bundle):
    tp = bundle["highlights"]["turning_point"]
    _open(page, "?speed=240")
    page.click("#jump-turning")
    assert page.evaluate("window.matchlens.state.playing")
    assert tp["start_s"] <= page.evaluate("window.matchlens.state.t") < tp["start_s"] + 60
    first = next(c for c in bundle["cards"]["casual"] if c["publish_clock_s"] > tp["start_s"])
    assert _poll(page, f"window.matchlens.state.t >= {first['publish_clock_s']}")
    assert page.locator(f'#cards .card[data-card-id="{first["card_id"]}"] li.why').count() >= 1


def test_ai_check_shortcut_opens_the_claim_check(page, bundle):
    target = bundle["highlights"]["ai_check"]
    review = bundle["reviews"][target["card_id"]]
    _open(page)
    page.click("#jump-ai")
    assert page.get_attribute(".mode-toggle button[data-mode=analyst]", "aria-checked") == "true"
    assert page.evaluate("window.matchlens.state.t") == target["publish_s"]
    assert not page.evaluate("window.matchlens.state.playing")
    sec = page.locator(f'.review.spotlight[data-review="{target["card_id"]}"]')
    assert sec.count() == 1 and sec.is_visible()
    assert page.evaluate("document.activeElement.dataset.review") == target["card_id"]
    assert review["proposer_label"] == sec.locator(".hint").first.text_content()
    # The plain summary leads; the claim list is open for the spotlighted card.
    explain = sec.locator(".explain")
    assert explain.is_visible() and explain.text_content() == " ".join(review["explanation"])
    assert sec.locator("details.claims").evaluate("d => d.open")
    if target["self_correction"]:  # the recorded Foundry run: rejected, sent back, fixed
        assert "rejected and sent back" in sec.locator(".summary").text_content()
        assert sec.locator("h5").first.text_content().startswith("Attempt 1")
        assert sec.locator(".verdicts li.rejected").first.is_visible()
        assert "The checker rejected" in explain.text_content()
        assert "on its second try" in explain.text_content()


def _recap_lines(page):
    """The text of each recap line, without the ✓ and the screen-reader prefix."""
    return page.locator("#recap-body .facts li > div > span:not(.sr-only)").all_text_contents()


def test_recap_shortcut_opens_the_recap_at_full_time(page, bundle):
    recap = bundle["recap"]
    _open(page, "?t=600")
    assert page.locator("#recap").is_hidden()
    page.click("#jump-recap")
    assert page.evaluate("window.matchlens.state.t") == recap["publish_clock_s"]
    assert not page.evaluate("window.matchlens.state.playing")
    assert page.locator("#recap").is_visible()
    assert page.evaluate("document.activeElement.id") == "recap"
    assert page.text_content("#recap-body h3") == recap["headline"]["text"]
    lines = [ln["text"] for ln in [*recap["summary"], *recap["goals"]]]
    assert _recap_lines(page) == lines
    assert page.evaluate("window.matchlens.recapText()") == format_recap(recap)
    assert page.locator("#recap-body .chip").count() == 0  # casual: no ID chips
    page.click("#recap-close")
    assert page.locator("#recap").is_hidden()
    assert page.evaluate("document.activeElement.id") == "jump-recap"


def test_recap_is_never_shown_before_full_time(page, bundle):
    _open(page)
    page.click("#jump-recap")
    page.evaluate("window.matchlens.seek(60)")
    assert page.locator("#recap").is_hidden()
    page.evaluate(f"window.matchlens.seek({bundle['duration_s']})")
    assert page.locator("#recap").is_hidden()  # it was closed, not deferred


def test_full_time_card_opens_the_recap_in_both_modes(page):
    for mode in ("casual", "analyst"):
        _open(page, f"?mode={mode}&t=5400")
        page.click("#cards .card.full_time [data-recap-open]")
        assert page.locator("#recap").is_visible()


def test_copy_recap_puts_plain_text_on_the_clipboard(page, bundle):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    _open(page, "?t=5400")
    page.click("#jump-recap")
    page.click("#recap-copy")
    page.wait_for_selector("#recap-status:not(:empty)")
    assert page.text_content("#recap-status") == "Recap copied as plain text."
    assert page.evaluate("navigator.clipboard.readText()") == format_recap(bundle["recap"])


def test_recap_json_keeps_every_line_with_its_sources(page, bundle):
    recap = bundle["recap"]
    _open(page, "?mode=analyst&t=5400")
    page.click("#jump-recap")
    assert page.locator("#recap-body .chip").count() > 0  # analyst: every line shows its IDs
    with page.expect_download() as info:
        page.click("#recap-download")
    download = info.value
    assert download.suggested_filename == f"matchlens-recap-{recap['match_id']}-en-neutral.json"
    with open(download.path(), encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc["schema"] == "matchlens.recap-export/v1" and doc["synthetic"] is True
    assert doc["text"] == format_recap(recap)
    for got, want in zip(
        [doc["headline"], *doc["summary"], *doc["goals"]],
        [recap["headline"], *recap["summary"], *recap["goals"]],
        strict=True,
    ):
        assert (got["text"], got["status"], got["lang"]) == (want["text"], "verified_fact", "en")
        assert (got["event_ids"], got["metric_ids"]) == (want["event_ids"], want["metric_ids"])


def test_recap_follows_the_language_and_club(page, bundle):
    if "personalized" not in bundle:
        pytest.skip("this build has no personalized variants")
    variant = bundle["personalized"]["variants"]["uk|away"]
    recap = bundle["recap"]
    _open(page, "?t=5400&lang=uk&follow=away")
    page.click("#jump-recap")
    items = page.locator("#recap-body .facts li")
    for i, ln in enumerate([*recap["summary"], *recap["goals"]]):
        (shown,) = [
            x for x in variant["cards"][ln["card_id"]]["lines"] if x["index"] == ln["index"]
        ]
        assert shown["text"] in items.nth(i).text_content()
        assert items.nth(i).get_attribute("lang") == (
            "uk" if shown["status"] == "verified" else "en"
        )
    assert page.get_attribute("#recap-body h4", "lang") == "uk"
    note = page.text_content("#recap-body .personalized-note")
    assert "AI rewrites by the Microsoft Foundry personalizer (recorded run)" in note
    text = page.evaluate("window.matchlens.recapText()")
    assert "\nГоли\n" in text and note in text and text.endswith("not calibrated.\n")


def test_recap_reflows_at_320px(browser, base_url):
    pg = browser.new_page(viewport={"width": 320, "height": 800})
    pg.goto(base_url + "?mode=analyst&t=5400")
    pg.wait_for_selector("body[data-ready=true]")
    pg.click("#jump-recap")
    assert pg.locator("#recap").is_visible()
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.close()


def test_event_chain_stays_open_while_inspecting(page):
    _open(page, "?mode=analyst&t=5400")
    chain = page.locator("#cards details.chain").first
    card_id = chain.evaluate("(d) => d.closest('[data-card-id]').dataset.cardId")
    assert not chain.evaluate("(d) => d.open")
    chain.locator("summary").click()
    chip = chain.locator(".chip[data-event-id]").first
    event_id = chip.get_attribute("data-event-id")
    chip.click()  # re-renders the cards; the chain must stay open and the chip focused
    again = page.locator(f'#cards .card[data-card-id="{card_id}"] details.chain')
    assert again.evaluate("(d) => d.open")
    assert page.evaluate("document.activeElement.dataset.eventId") == event_id


# --- Broadcast lower-third (broadcast.html): a browser source fed by data/overlays.json ---


def _open_broadcast(page, query):
    page.goto(page.base + "broadcast.html" + query)
    page.wait_for_selector("body[data-ready=true]")


def _cues(bundle, mode):
    return [c for c in bundle["overlays"]["cues"] if c["mode"] == mode]


def _on_air(text):
    """The graphic's text with evidence brackets dropped and the score's hyphen unbreakable."""
    return re.sub(r"(\d)-(\d)", "\\1\u2011\\2", text.split(" [evidence")[0])


def _why(cue):
    return next((f for f in cue["facts"] if f["role"] == "why"), cue["facts"][0])


def test_broadcast_cue_shows_the_verified_why_line_on_a_transparent_page(page, bundle):
    cue = next(c for c in _cues(bundle, "casual") if c["kind"] == "goal")
    _open_broadcast(page, f"?cue={cue['cue_id']}")
    lt = page.locator("#lower-third")
    assert lt.is_visible() and lt.get_attribute("data-cue") == cue["cue_id"]
    assert page.text_content("#lt-title") == _on_air(cue["title"])
    assert page.text_content("#lt-fact") == _on_air(_why(cue)["text"])
    assert page.text_content("#lt-team") == bundle["overlays"]["teams"][cue["team"]]
    assert "Checked against match events" in page.text_content("#lt-note")
    assert cue["narrative"]["text"] not in page.text_content("body")  # wording never goes on air
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgba(0, 0, 0, 0)"  # composited over video
    assert "Synthetic match" in page.text_content("#bug")


def test_broadcast_analyst_cue_cites_events_without_evidence_brackets(page, bundle):
    cue = next(c for c in _cues(bundle, "analyst") if c["kind"] == "goal")
    _open_broadcast(page, f"?mode=analyst&cue={cue['cue_id']}&bg=green")
    fact = page.text_content("#lt-fact")
    assert "[evidence" not in fact and fact == _on_air(_why(cue)["text"])
    note = page.text_content("#lt-note")
    assert f"Events {_why(cue)['event_ids'][0]}" in note
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(0, 177, 64)"


def test_broadcast_follows_the_match_clock_and_never_shows_a_cue_early(page, bundle):
    cue = next(c for c in _cues(bundle, "casual") if c["kind"] == "goal")
    _open_broadcast(page, f"?from={cue['start_s'] - 1.5}&speed=1")
    assert page.locator("#lower-third").is_hidden()  # not before its publication time
    page.wait_for_selector(f"#lower-third[data-cue='{cue['cue_id']}']", timeout=5000)
    assert float(page.get_attribute("body", "data-clock")) >= cue["start_s"]


def test_broadcast_rundown_plays_each_cue_in_order(page, bundle):
    cues = sorted(_cues(bundle, "analyst"), key=lambda c: c["start_s"])
    _open_broadcast(page, "?mode=analyst&rundown=1&hold=2")
    page.wait_for_selector(f"#lower-third[data-cue='{cues[0]['cue_id']}']", timeout=2000)
    page.wait_for_selector(f"#lower-third[data-cue='{cues[1]['cue_id']}']", timeout=5000)


def test_broadcast_unknown_cue_reports_instead_of_guessing(page):
    _open_broadcast(page, "?cue=casual:goal:nope")
    assert page.locator("#lower-third").is_hidden()
    assert 'No cue "casual:goal:nope"' in page.text_content("#status-line")


def test_replay_links_to_the_broadcast_graphic_in_the_current_mode(page):
    _open(page)
    link = page.locator("#broadcast")
    assert link.get_attribute("href") == "broadcast.html?mode=casual&rundown=1&bg=dark"
    page.click("[data-mode=analyst]")
    assert link.get_attribute("href") == "broadcast.html?mode=analyst&rundown=1&bg=dark"


# --- Live Foundry check button (fake Responses API behind the local app server) ---

LIVE_CARD = "analyst:goal:ml1-01112"  # the 71' card scripted in tests/test_agent.py


class _ScriptedFoundry:
    """Fake Responses API: every live check replays the scripted 71' agent run."""

    def __init__(self):
        from tests.test_agent import _goal_script

        self._script, self.replies = _goal_script, []

    def __call__(self, url, headers, body, timeout):
        if not self.replies:
            self.replies = self._script()
        return json.dumps(self.replies.pop(0)).encode()


@pytest.fixture(scope="module")
def live_base(request, bundle):
    """App server with the live route: configured (fake Foundry) or not configured."""
    if PUBLIC_URL:
        pytest.skip("live-check tests use a fake Foundry; never spend real calls on smoke runs")
    from matchlens.live import LIVE_PATH, LiveChecker
    from tests.test_agent import _config

    servers = {}
    for name, checker in (
        ("fake", LiveChecker(_config(), transport=_ScriptedFoundry())),
        ("off", LiveChecker(None)),
    ):
        srv = _start(
            make_server({**bundle, "live_check": {"path": LIVE_PATH}}, port=0, checker=checker)
        )
        servers[name] = srv
    yield {n: f"http://127.0.0.1:{s.server_address[1]}/" for n, s in servers.items()}
    for srv in servers.values():
        srv.shutdown()
        srv.server_close()


def _card(page, card_id):
    return page.locator(f"#cards [data-card-id='{card_id}']")


def test_no_live_button_without_a_live_api(page, bundle):
    if "live_check" in bundle:
        pytest.skip("this deployment has a live-check API")
    _open(page, "?mode=analyst&t=5400")
    assert page.locator(".live-check").count() == 0


def test_live_check_replaces_the_recorded_run_with_the_live_reply(page, live_base):
    page.base = live_base["fake"]
    _open(page, "?mode=analyst&t=5400")
    card = _card(page, LIVE_CARD)
    assert "Recorded Microsoft Foundry agent run" in card.locator(".review").text_content()
    card.locator(".live-check").click()
    status = card.locator(".live-ok")
    status.wait_for(timeout=10000)
    assert (
        "Live result from Microsoft Foundry: 4 model call(s), 600 tokens" in status.text_content()
    )
    assert page.evaluate("document.activeElement.dataset.liveStatus") == LIVE_CARD
    review = card.locator(".review").text_content()
    assert "Live Microsoft Foundry agent call" in review and "Recorded" not in review
    assert "resp_1, resp_2, resp_3, resp_4" in review
    assert "Attempt 1: 3 of 4" not in review  # the recorded run's verdicts are gone
    assert "server-side function" in page.text_content("#disclosure")


def test_live_check_fallback_keeps_the_recorded_run_and_says_so(page, live_base):
    page.base = live_base["off"]
    page.expected_errors.append("status of 503")  # the fallback reply is an HTTP 503
    _open(page, "?mode=analyst&t=5400")
    card = _card(page, LIVE_CARD)
    card.locator(".live-check").click()
    warn = card.locator(".live .warn")
    warn.wait_for(timeout=10000)
    text = warn.text_content()
    assert "not configured" in text and "not a live call" in text
    assert "Recorded Microsoft Foundry agent run" in card.locator(".review").text_content()


# --- Match picker ---


@pytest.fixture(scope="module")
def index(base_url):
    with urllib.request.urlopen(base_url + "data/matches.json", timeout=30) as resp:
        return json.load(resp)


def _match_bundle(base_url, entry):
    with urllib.request.urlopen(base_url + entry["file"], timeout=30) as resp:
        return json.load(resp)


def test_match_picker_lists_every_match_without_spoilers(page, index):
    _open(page)
    options = page.locator("#match option")
    assert options.count() == len(index["matches"]) >= 2
    texts = options.all_text_contents()
    assert texts[0].startswith(f"Seed {index['matches'][0]['seed']}") and "demo" in texts[0]
    assert page.eval_on_selector("#match", "s => s.value") == str(index["matches"][0]["seed"])
    for text, entry in zip(texts, index["matches"], strict=True):
        h, a = entry["final_score"]
        assert f"{h}-{a}" not in text and f"{h}–{a}" not in text  # no result in the picker


def test_picking_a_match_loads_its_own_bundle_and_overlays(page, index, base_url):
    entry = index["matches"][1]
    other = _match_bundle(base_url, entry)
    _open(page, "?mode=analyst")
    with page.expect_navigation():
        page.select_option("#match", str(entry["seed"]))
    page.wait_for_selector("body[data-ready=true]")
    assert f"seed={entry['seed']}" in page.url and "mode=analyst" in page.url
    assert page.get_attribute(".mode-toggle button[data-mode=analyst]", "aria-checked") == "true"
    assert page.get_attribute("#download", "href") == entry["overlays"]
    assert "not picked or tuned" in page.text_content("#disclosure")
    page.evaluate(f"window.matchlens.seek({other['duration_s']})")
    shown = page.locator("#cards [data-card-id]").evaluate_all(
        "els => els.map(e => e.dataset.cardId)"
    )
    assert sorted(shown) == sorted(c["card_id"] for c in other["cards"]["analyst"])
    h, a = other["final_score"]
    assert (page.text_content("#home-score"), page.text_content("#away-score")) == (str(h), str(a))
    assert page.locator(".live-check").count() == 0  # live checks cover the demo match only
    assert f"seed={entry['seed']}" in page.get_attribute("#broadcast", "href")
    ai = other["highlights"].get("ai_check")
    if ai:
        assert "AI" not in page.text_content("#jump-ai")


def test_an_unknown_seed_falls_back_to_the_demo_match(page, bundle):
    _open(page, "?seed=999999")
    assert page.eval_on_selector("#match", "s => s.value") == str(bundle["meta"]["seed"])
    page.evaluate(f"window.matchlens.seek({bundle['duration_s']})")
    h, a = bundle["final_score"]
    assert (page.text_content("#home-score"), page.text_content("#away-score")) == (str(h), str(a))


def test_broadcast_plays_the_picked_match(page, index, base_url):
    entry = index["matches"][1]
    with urllib.request.urlopen(base_url + entry["overlays"], timeout=30) as resp:
        cue = json.load(resp)["cues"][0]
    _open_broadcast(page, f"?seed={entry['seed']}&mode={cue['mode']}&cue={cue['cue_id']}")
    assert page.get_attribute("#lower-third", "data-cue") == cue["cue_id"]
    page.expected_errors.append("status of 404")  # none expected, but a bad index would 404
    _open_broadcast(page, "?seed=999999")
    assert "no match with seed 999999" in page.text_content("#status-line")


# --- Personalizer: language and club (fake recorded run behind a local app server) ---


@pytest.fixture(scope="module")
def personalized_base(bundle):
    if PUBLIC_URL:
        pytest.skip("uses a fake personalizer trace")
    from matchlens.cards import build_cards
    from matchlens.personalize import (
        FoundryPersonalizer,
        RecordedPersonalizer,
        build_trace,
        known_names,
        personalize_cards,
    )
    from tests.test_personalize import FakeTranslator, _config

    match = generate_match(DEFAULT_DEMO_SEED)
    cards = build_cards(match, "casual")
    # Line 0 of every card fails both attempts, so each card shows one line kept in English;
    # a fake critic approves everything else, so the page shows the critic-reviewed labels.
    from matchlens.critic import FoundryCritic
    from tests.test_critic import FakeCritic, Router

    router = Router(FakeTranslator(known_names(match), spoil={1: (0,), 2: (0,)}), FakeCritic())
    live = personalize_cards(
        match,
        cards,
        FoundryPersonalizer(_config(), router),
        strict=True,
        critic=FoundryCritic(_config(), router),
    )
    trace = build_trace(match, live, _config())
    trace_proposer = RecordedProposer(json.loads((ROOT / DEMO_TRACE).read_text()), DEMO_TRACE)
    doc = build_bundle(
        match, proposer=trace_proposer, personalizer=RecordedPersonalizer(trace, "fake.json")
    )
    srv = _start(make_server(json.loads(dumps(doc)), port=0))
    yield f"http://127.0.0.1:{srv.server_address[1]}/", json.loads(dumps(doc))
    srv.shutdown()
    srv.server_close()


def test_no_language_picker_without_a_personalizer_run(page, bundle):
    if "personalized" in bundle:
        pytest.skip("this deployment has a personalizer run")
    _open(page, "?t=5400")
    assert page.locator("#personalize").is_hidden()


def test_language_and_club_rewrite_the_casual_cards(page, personalized_base):
    base, doc = personalized_base
    page.base = base
    _open(page, "?t=5400")
    assert page.locator("#personalize").is_visible()
    assert page.text_content("#p-focus-away") == doc["meta"]["away"]["name"]
    page.select_option("#p-lang", "uk")
    page.select_option("#p-focus", "away")
    card = doc["cards"]["casual"][-1]
    lines = doc["personalized"]["variants"]["uk|away"]["cards"][card["card_id"]]["lines"]
    art = page.locator(f"#cards [data-card-id='{card['card_id']}']")
    assert art.locator("h3").get_attribute("lang") == "uk"
    assert art.locator("h3").text_content() == lines[0]["text"] != card["title"]
    items = art.locator("ul.facts > li")
    assert items.nth(0).get_attribute("lang") == "en"  # rejected twice: the English source
    assert card["facts"][0]["text"] in items.nth(0).text_content()
    assert items.nth(1).get_attribute("lang") == "uk"
    assert lines[2]["text"] in items.nth(1).text_content()
    note = art.locator(".personalized-note").text_content()
    assert "Українська, for Eastvale Athletic fans" in note and "recorded run" in note
    assert "meaning reviewed by a critic agent (also AI); tone not checked" in note
    assert "1 line(s) kept in English: the checker, the critic or the editorial review" in note
    chip = art.locator(".narrative .status").text_content()
    assert chip == "AI wording (Foundry personalizer) · numbers & names checked · critic-reviewed"
    assert "Template wording" not in art.text_content()
    assert "AI rewrite" in items.nth(1).locator(".sr-only").text_content()
    assert items.nth(0).locator(".sr-only").text_content().startswith("Verified")
    page.click(".mode-toggle button[data-mode=analyst]")  # analyst cards are never rewritten
    assert page.locator("#personalize").is_hidden()
    assert page.locator("#cards .personalized-note").count() == 0


def test_language_can_be_set_by_url(page, personalized_base):
    base, doc = personalized_base
    page.base = base
    _open(page, "?t=5400&lang=de&follow=home")
    assert page.eval_on_selector("#p-lang", "s => s.value") == "de"
    assert page.eval_on_selector("#p-focus", "s => s.value") == "home"
    note = page.locator("#cards .personalized-note").first.text_content()
    assert note.startswith("Deutsch, for Northbridge Rovers fans")
    _open(page, "?t=5400&lang=fr")  # unknown: the English source
    assert page.eval_on_selector("#p-lang", "s => s.value") == "en"
    assert page.locator("#cards .personalized-note").count() == 0


def test_the_recorded_personalizer_run_is_live_on_the_page(page, bundle):
    """Production bundle: the committed run, re-checked; a flipped score stays English."""
    p = bundle.get("personalized")
    if not p:
        pytest.skip("this deployment has no personalizer run")
    ft = next(c for c in bundle["cards"]["casual"] if c["kind"] == "full_time")
    title = p["variants"]["uk|away"]["cards"][ft["card_id"]]["lines"][0]
    assert title["index"] == -1 and title["status"] == "kept_original"  # "3-2" was rejected
    _open(page, f"?t={bundle['duration_s']}&lang=uk&follow=away")
    art = page.locator(f"#cards [data-card-id='{ft['card_id']}']")
    assert art.locator("h3").text_content() == ft["title"]
    assert art.locator("h3").get_attribute("lang") == "en"
    note = art.locator(".personalized-note").text_content()
    assert "recorded run" in note and "kept in English" in note
    if not p.get("critics"):
        assert "meaning and tone not checked" in note
    assert "3-2" not in art.text_content()
    _open(page, f"?t={bundle['duration_s']}&lang=uk&follow=home")
    text = page.text_content("#cards")
    assert "Впевненість" not in text and "відійшли назад" not in text  # editorial review


# --- Player focus ------------------------------------------------------------------------------


def _scorer(bundle):
    goal = next(e for e in bundle["events"] if e["type"] == "shot" and e["outcome"] == "goal")
    player = next(p for p in bundle["players"]["players"] if p["player_id"] == goal["player_id"])
    return goal, player


def test_player_focus_counts_only_what_has_happened(page, bundle):
    goal, player = _scorer(bundle)
    _open(page, f"?player={player['player_id']}&t={goal['clock_s'] - 1}")
    assert page.input_value("#player") == player["player_id"]
    assert player["name"] in page.text_content("#player-heading")
    assert "1 goal" not in page.text_content("#player-body")  # the goal has not happened yet
    page.evaluate(f"window.matchlens.seek({goal['clock_s']})")
    body = page.text_content("#player-body")
    assert "1 goal" in body and "heuristic, not calibrated" in body
    badge = page.locator(".card .features")
    assert badge.count() >= 1 and player["name"] in badge.first.text_content()


def test_player_focus_in_analyst_mode_shows_evidence(page, bundle):
    goal, player = _scorer(bundle)
    _open(page, f"?player={player['player_id']}&mode=analyst&t={goal['clock_s']}")
    rows = page.locator("#player-body tbody tr")
    assert rows.count() == len(player["stats"])
    xg = page.locator("#player-body tr", has_text="Synthetic xG")
    assert "heuristic" in xg.text_content()
    goals = page.locator("#player-body tr", has_text="Goals").first
    goals.locator("button").click()
    assert page.evaluate("window.matchlens.state.selected") == [goal["event_id"]]
    assert page.locator("#highlight circle").count() >= 1


def test_player_picker_lists_both_squads_and_can_be_cleared(page, bundle):
    _open(page)
    assert page.locator("#player-panel").is_hidden()
    opts = page.locator("#player option")
    assert opts.count() == 1 + len(bundle["players"]["players"])
    assert page.locator("#player optgroup").count() == 2
    first = bundle["players"]["players"][0]
    page.select_option("#player", first["player_id"])
    assert page.locator("#player-panel").is_visible()
    assert page.locator("#legend-player").is_visible()
    page.select_option("#player", "")
    assert page.locator("#player-panel").is_hidden()
    assert page.locator(".card .features").count() == 0


@pytest.mark.parametrize("mode", ["casual", "analyst"])
def test_player_panel_reflows_at_320px(browser, base_url, bundle, mode):
    _, player = _scorer(bundle)
    pg = browser.new_page(viewport={"width": 320, "height": 800})
    pg.goto(base_url + f"?t=5400&player={player['player_id']}&mode={mode}")
    pg.wait_for_selector("body[data-ready=true]")
    assert pg.locator("#player-panel").is_visible()
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.close()


# --- Passing ----------------------------------------------------------------------------------


def _pass_final(bundle, side, name):
    p = bundle["passing"]
    names = [s["name"] for s in p["stats"]]
    return p["teams"][side][-1][1 + names.index(name)]


def test_pass_accuracy_tile_follows_the_match(page, bundle):
    _open(page)
    assert page.text_content("#stat-pass") == "– – –"
    page.evaluate(f"window.matchlens.seek({bundle['duration_s']})")
    home = round(100 * _pass_final(bundle, "home", "completion"))
    away = round(100 * _pass_final(bundle, "away", "completion"))
    assert page.text_content("#stat-pass") == f"{home}% – {away}%"
    assert page.locator("#passing-panel").is_hidden()  # analyst only


def test_passing_table_labels_heuristics_and_shows_its_passes(page, bundle):
    _open(page, f"?mode=analyst&t={bundle['duration_s']}")
    rows = page.locator("#passing-body tbody tr")
    assert rows.count() == len(bundle["passing"]["stats"])
    for label in ("Synthetic xPass", "Completed minus xPass"):
        assert "heuristic, not calibrated" in rows.filter(has_text=label).text_content()
    assert "Ball speed is not modelled" in page.text_content("#passing-panel")
    n = _pass_final(bundle, "away", "progressive_completed")
    page.click('[data-pass-stat="away.progressive_completed"]')
    selected = page.evaluate("window.matchlens.state.selected")
    assert len(selected) == n
    rows_by_id = {r[1]: r for r in bundle["passing"]["passes"]}
    assert all(
        rows_by_id[i][2] == "away" and rows_by_id[i][3] and rows_by_id[i][6] for i in selected
    )


def test_passing_panel_reflows_at_320px(browser, base_url, bundle):
    pg = browser.new_page(viewport={"width": 320, "height": 800})
    pg.goto(base_url + f"?mode=analyst&t={bundle['duration_s']}")
    pg.wait_for_selector("body[data-ready=true]")
    assert pg.locator("#passing-panel").is_visible()
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.close()


# --- Casual design: comparison bars, lineups, short cards ------------------------------------


def _bar(page, key):
    row = page.locator(f'#bars [data-bar="{key}"]')
    return row.locator(".bar-val.home").text_content(), row.locator(".bar-val.away").text_content()


def _team_final(bundle, side, name):
    """A team total summed from its players' final running stats (matchlens.players)."""
    total = 0
    for p in bundle["players"]["players"]:
        stat = next(s for s in p["stats"] if s["name"] == name)
        if p["team"] == side and stat["steps"]:
            total += stat["steps"][-1][1]
    return total


def test_casual_bars_show_the_team_stats_as_they_stood(page, bundle):
    _open(page, "?t=0")
    assert page.locator("#bars").is_visible() and page.locator("#stats").is_hidden()
    assert _bar(page, "shots") == ("0", "0") and _bar(page, "pass_acc") == ("–", "–")
    for t in (600, 2700, 4862, bundle["duration_s"]):  # in step with the analyst tiles
        page.evaluate(f"window.matchlens.seek({t})")
        assert " – ".join(_bar(page, "shots")) == page.text_content("#stat-shots")
        assert " – ".join(_bar(page, "xg")) == page.text_content("#stat-xg")
        assert " – ".join(_bar(page, "pass_acc")) == page.text_content("#stat-pass")
    for key, name in (
        ("on_target", "shots_on_target"),
        ("pressures", "pressures"),
        ("ball_wins", "ball_wins"),
    ):
        home, away = (_team_final(bundle, side, name) for side in ("home", "away"))
        assert _bar(page, key) == (str(home), str(away))
    sr = page.locator('#bars [data-bar="xg"] .sr-only').text_content()
    assert "synthetic heuristic, not calibrated" in sr and bundle["meta"]["home"]["name"] in sr
    page.click('.mode-toggle [data-mode="analyst"]')
    assert page.locator("#bars").is_hidden() and page.locator("#stats").is_visible()


def test_lineups_show_both_squads_and_only_goals_already_scored(page, bundle):
    goal, scorer = _scorer(bundle)
    _open(page, f"?view=lineups&t={goal['clock_s'] - 1}")
    assert page.locator("#lineup").is_visible() and page.locator("#pitch").is_hidden()
    assert page.get_attribute("#view-lineups", "aria-pressed") == "true"
    assert page.locator("#lower-third").is_hidden()  # the graphic belongs to the live view
    squads = [p for side in ("home", "away") for p in bundle["meta"][side]["players"]]
    assert page.locator("#lineup .lp").count() == len(squads) == 22
    shape = {
        side: "-".join(
            str(sum(p["position"] == pos for p in bundle["meta"][side]["players"]))
            for pos in ("DF", "MF", "FW")
        )
        for side in ("home", "away")
    }
    assert shape["home"] in page.text_content("#lineup-home")
    assert shape["away"] in page.text_content("#lineup-away")
    one = page.locator(f'#lineup .lp[data-player-id="{scorer["player_id"]}"]')
    assert f"#{scorer['number']} {scorer['name']}" in one.get_attribute("aria-label")
    assert one.locator(".lp-goal").count() == 0  # not yet
    page.evaluate(f"window.matchlens.seek({goal['clock_s']})")
    assert one.locator(".lp-goal").count() == 1
    assert "1 goal" in one.get_attribute("aria-label")
    page.evaluate(f"window.matchlens.seek({bundle['duration_s']})")
    assert page.locator("#lineup .lp-goal").count() == sum(bundle["final_score"])
    page.click("#view-live")
    assert page.locator("#pitch").is_visible() and page.locator("#lineup").is_hidden()


def test_a_lineup_player_can_be_followed_by_click_or_keyboard(page, bundle):
    goal, scorer = _scorer(bundle)
    _open(page, f"?view=lineups&t={goal['clock_s']}")
    sel = f'#lineup .lp[data-player-id="{scorer["player_id"]}"]'
    page.click(sel)
    assert page.input_value("#player") == scorer["player_id"]
    assert page.locator("#player-panel").is_visible()
    assert page.get_attribute(sel, "aria-pressed") == "true"
    assert scorer["name"] in page.locator(".card .features").first.text_content()
    page.focus(sel)  # the lineup re-rendered: the same player keeps the focus
    page.keyboard.press("Space")
    assert page.evaluate("window.matchlens.state.playing") is False  # Space acts on the player
    assert page.input_value("#player") == ""
    assert page.locator("#player-panel").is_hidden()
    page.keyboard.press("Enter")
    assert page.input_value("#player") == scorer["player_id"]
    assert page.evaluate("document.activeElement.dataset.playerId") == scorer["player_id"]
    page.click("#view-live")
    assert page.locator("#trail .player-pos").count() == 1  # their last touch, on the live pitch


def _lead(card):
    lines = [f for f in card["facts"] if f["role"] != "chain"]
    return next((f for f in lines if f["role"] == "why"), lines[0]), len(lines) - 1


def test_casual_cards_lead_with_why_and_fold_the_rest(page, bundle):
    _open(page, f"?t={bundle['duration_s']}")
    cards = bundle["cards"]["casual"]
    for card in cards:
        art = page.locator(f"#cards [data-card-id='{card['card_id']}']")
        lead, rest = _lead(card)
        shown = art.locator(":scope > ul.facts > li")
        assert shown.count() == 1
        assert lead["text"] in shown.first.text_content()
        more = art.locator("details.more")
        if rest:
            assert more.locator("summary").text_content() == f"More ({rest})"
            assert more.get_attribute("open") is None  # folded until asked
            assert more.locator("ul.facts > li").count() == rest
        else:
            assert more.count() == 0
    folded = next(c for c in cards if _lead(c)[1])
    summary = page.locator(f"#cards [data-card-id='{folded['card_id']}'] details.more > summary")
    summary.click()
    assert _poll(page, f"window.matchlens.state.openMore.has({folded['card_id']!r})")
    page.evaluate(f"window.matchlens.seek({bundle['duration_s'] - 1})")  # cards re-render
    reopened = page.locator(f"#cards [data-card-id='{folded['card_id']}'] details.more")
    assert reopened.get_attribute("open") is not None  # stays open across re-renders
    page.click('.mode-toggle [data-mode="analyst"]')
    assert page.locator("#cards details.more").count() == 0  # analyst cards show every line


def test_lineups_and_bars_reflow_at_320px(browser, base_url):
    pg = browser.new_page(viewport={"width": 320, "height": 800})
    pg.goto(base_url + "?t=5400&view=lineups")
    pg.wait_for_selector("body[data-ready=true]")
    assert pg.locator("#lineup").is_visible() and pg.locator("#bars").is_visible()
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # Names would be unreadably small: numbers only, each name still in the player's label.
    assert pg.evaluate("getComputedStyle(document.querySelector('.lp-name')).display") == "none"
    pg.close()

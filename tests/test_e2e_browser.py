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
import threading
import urllib.request

import pytest

from matchlens.generator import generate_match
from matchlens.replay import DEFAULT_DEMO_SEED, build_bundle, dumps
from matchlens.server import export_site, make_server

playwright_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.e2e
PUBLIC_URL = os.environ.get("MATCHLENS_BASE_URL", "").strip()
TARGETS = ["public"] if PUBLIC_URL else ["app-server", "static-subpath"]


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
    bundle = json.loads(dumps(build_bundle(generate_match(DEFAULT_DEMO_SEED))))
    if request.param == "app-server":
        srv = _start(make_server(bundle, port=0))
        yield f"http://127.0.0.1:{srv.server_address[1]}/", bundle
    else:
        root = tmp_path_factory.mktemp("static")
        export_site(bundle, root / "matchlens-ai")
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
    yield pg
    assert errors == []
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
        assert "offline deterministic template" in disclosure
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
    assert page.locator("#cards .verdicts li.ok").count() >= 1
    _open(page, "?t=5400")
    assert page.locator("#cards .review").count() == 0  # casual mode shows no claim check


def test_narrative_is_labelled_not_verified_as_fact(page):
    _open(page, "?t=5400")
    statuses = page.locator("#cards .narrative .status").all_text_contents()
    allowed = ("Offline template · not an Azure response", "AI wording · NOT VERIFIED")
    assert statuses and all(s in allowed for s in statuses)
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


def test_interactive_targets_are_at_least_24px(page):
    _open(page, "?mode=analyst&t=5400")
    small = page.evaluate(
        """[...document.querySelectorAll('#markers button, #cards .chip')]
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
    pg.goto(base_url + "?t=60")  # long "Last event": 1' <team>: kick off (complete)
    pg.wait_for_selector("body[data-ready=true]")
    clipped = pg.evaluate(
        """[...document.querySelectorAll('.stats > div > span:last-child')]
             .filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.id)"""
    )
    assert clipped == []  # stat values wrap instead of being cut off with an ellipsis
    pg.close()


def test_goal_dot_only_for_goals_without_a_card(page, bundle):
    _open(page)
    goals = [e for e in bundle["events"] if e["type"] == "shot" and e.get("outcome") == "goal"]
    carded = {c["anchor_clock_s"] for c in bundle["cards"]["casual"] if c["kind"] == "goal"}
    expected = sum(1 for e in goals if e["clock_s"] not in carded)
    assert page.locator("#markers .goal-tick").count() == expected

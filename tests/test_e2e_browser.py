"""Browser end-to-end tests of the replay UI (Playwright + Chromium).

Skipped when Playwright or a Chromium build is unavailable. Locally, set
MATCHLENS_CHROMIUM to a Chromium executable if Playwright's bundled browser is missing.
"""

import json
import os
import threading

import pytest

from matchlens.generator import generate_match
from matchlens.replay import DEFAULT_DEMO_SEED, build_bundle, dumps
from matchlens.server import make_server

playwright_api = pytest.importorskip("playwright.sync_api")

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def bundle():
    return json.loads(dumps(build_bundle(generate_match(DEFAULT_DEMO_SEED))))


@pytest.fixture(scope="module")
def base_url(bundle):
    srv = make_server(bundle, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()
    srv.server_close()


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


def test_initial_state(page):
    _open(page)
    assert page.text_content("#home-name") == "Northbridge Rovers"
    assert page.text_content("#home-score") == "0" and page.text_content("#away-score") == "0"
    assert page.text_content("#clock") == "00:00"
    assert page.locator("#cards .card").count() == 0  # no spoilers before kick-off
    assert "offline deterministic template" in page.text_content("#disclosure")
    assert page.locator("#markers .marker").count() >= 4


def test_seek_to_full_time_shows_final_score_and_cards(page, bundle):
    _open(page)
    page.evaluate("window.matchlens.seek(5400)")
    assert page.text_content("#home-score") == "2" and page.text_content("#away-score") == "3"
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
    assert page.evaluate("window.matchlens.state.t") == first["anchor_clock_s"]
    card = page.locator(f'#cards .card[data-card-id="{first["card_id"]}"]')
    assert card.count() == 1 and "active" in card.get_attribute("class")
    assert page.is_visible("#lower-third")
    assert page.text_content("#lt-title") == first["title"]


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


def test_narrative_is_labelled_not_verified_as_fact(page):
    _open(page, "?t=5400")
    statuses = page.locator("#cards .narrative .status").all_text_contents()
    assert statuses and all("not an Azure response" in s for s in statuses)
    checks = page.locator("#cards .narrative .check").count()
    assert checks == 0  # ✓ marks only verified facts, never the narrative


def test_download_overlay_json(page, bundle):
    _open(page)
    with page.expect_download() as info:
        page.click("#download")
    download = info.value
    assert download.suggested_filename == "matchlens-overlays-ml1.json"
    with open(download.path(), encoding="utf-8") as fh:
        doc = json.load(fh)
    assert doc == bundle["overlays"]


def test_mobile_layout_has_no_horizontal_scroll(browser, base_url):
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    pg.goto(base_url + "?t=3000")
    pg.wait_for_selector("body[data-ready=true]")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.close()

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

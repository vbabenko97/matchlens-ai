"""Integration tests: replay bundle, overlay document, static export and HTTP server."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.metrics import compute_metrics
from matchlens.narrative import AI_UNVERIFIED, TEMPLATE
from matchlens.overlays import OVERLAY_SCHEMA
from matchlens.replay import BUNDLE_SCHEMA, DEFAULT_DEMO_SEED, build_bundle, dumps, minute_series
from matchlens.server import WEB_FILES, export_site, make_server

from .test_cards import LyingProvider


@pytest.fixture(scope="module")
def match():
    return generate_match(DEFAULT_DEMO_SEED)


@pytest.fixture(scope="module")
def bundle(match):
    return json.loads(dumps(build_bundle(match)))  # as the browser sees it


def test_default_demo_is_seed_1_ending_2_3(bundle):
    assert DEFAULT_DEMO_SEED == 1
    assert bundle["schema"] == BUNDLE_SCHEMA and bundle["meta"]["seed"] == 1
    assert bundle["final_score"] == [2, 3]
    assert bundle["provider"] == "offline_template"
    assert bundle["duration_s"] == 5400 and len(bundle["events"]) == len(generate_match(1).events)


def test_bundle_is_deterministic(match):
    assert dumps(build_bundle(match)) == dumps(build_bundle(generate_match(DEFAULT_DEMO_SEED)))


def test_cards_and_overlays_agree(bundle):
    cues = bundle["overlays"]["cues"]
    assert bundle["overlays"]["schema"] == OVERLAY_SCHEMA
    for mode in ("casual", "analyst"):
        cards = bundle["cards"][mode]
        assert 2 <= len(cards) <= 4
        mode_cues = [c for c in cues if c["mode"] == mode]
        assert [c["cue_id"] for c in mode_cues] == [c["card_id"] for c in cards]
        for card, cue in zip(cards, mode_cues, strict=True):
            assert cue["start_s"] == card["anchor_clock_s"] < cue["end_s"] == card["end_clock_s"]
            assert cue["facts"] == card["facts"]
            assert cue["narrative"]["status"] == TEMPLATE
    assert [c["start_s"] for c in cues] == sorted(c["start_s"] for c in cues)


def test_every_cited_id_is_inspectable(bundle):
    event_ids = {e["event_id"] for e in bundle["events"]}
    for cards in bundle["cards"].values():
        for card in cards:
            assert set(card["evidence_event_ids"]) <= event_ids
            for fact in card["facts"]:
                assert set(fact["event_ids"]) <= event_ids
                assert all(m in bundle["metrics"] for m in fact["metric_ids"])
    for metric in bundle["metrics"].values():
        assert set(metric["source_event_ids"]) <= event_ids


def test_minute_series_matches_metric_totals(match, bundle):
    metrics = compute_metrics(match)
    series = bundle["series"]
    assert series == minute_series(match)
    for side in ("home", "away"):
        assert series[f"{side}.goals"][-1] == metrics[f"{side}.goals"].value
        assert series[f"{side}.shots"][-1] == metrics[f"{side}.shots"].value
        assert series[f"{side}.xg_heuristic"][-1] == pytest.approx(
            metrics[f"{side}.xg_heuristic"].value, abs=1e-3
        )
        assert series[f"{side}.shots"][0] == 0
        assert all(
            a <= b
            for a, b in zip(series[f"{side}.shots"], series[f"{side}.shots"][1:], strict=False)
        )


def test_ai_wording_keeps_unverified_label_in_bundle(match):
    b = build_bundle(match, LyingProvider())
    narratives = [c["narrative"] for cards in b["cards"].values() for c in cards]
    assert narratives and all(n["status"] == AI_UNVERIFIED for n in narratives)
    assert all("not verified" in n["label"] for n in narratives)
    assert all(
        n["status"] == AI_UNVERIFIED for n in (c["narrative"] for c in b["overlays"]["cues"])
    )
    offline = build_bundle(match)
    assert b["cards"]["casual"][0]["facts"] == offline["cards"]["casual"][0]["facts"]


def test_export_site(tmp_path, bundle):
    out = tmp_path / "site"
    export_site(bundle, out)
    for name in WEB_FILES:
        assert (out / name).stat().st_size > 0
    assert json.loads((out / "data" / "bundle.json").read_text())["final_score"] == [2, 3]
    overlays = json.loads((out / "data" / "overlays.json").read_text())
    assert overlays["schema"] == OVERLAY_SCHEMA


def test_export_site_never_deletes_user_files(tmp_path, bundle):
    victim = tmp_path / "project"
    victim.mkdir()
    (victim / "important.txt").write_text("keep me")
    with pytest.raises(FileExistsError):
        export_site(bundle, victim)
    assert (victim / "important.txt").read_text() == "keep me"
    assert not (victim / "index.html").exists()
    file_target = tmp_path / "file.txt"
    file_target.write_text("x")
    with pytest.raises(FileExistsError):
        export_site(bundle, file_target)


def test_export_site_overwrites_previous_export_in_place(tmp_path, bundle):
    out = tmp_path / "site"
    export_site(bundle, out)
    (out / "CNAME").write_text("demo.example")  # host files next to the export survive
    export_site(bundle, out)
    assert (out / "CNAME").read_text() == "demo.example"
    assert (out / "index.html").exists()


def test_cli_export_site_refuses_non_empty_dir(tmp_path, capsys):
    (tmp_path / "keep.txt").write_text("x")
    assert main(["export-site", "--offline", "--out", str(tmp_path)]) == 1
    assert "refusing to write" in capsys.readouterr().err
    assert (tmp_path / "keep.txt").exists()


@pytest.fixture(scope="module")
def server(bundle):
    srv = make_server(bundle, port=0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, dict(resp.headers), resp.read()


def test_server_serves_ui_and_data(server):
    status, headers, body = _get(server + "/")
    assert status == 200 and headers["Content-Type"].startswith("text/html")
    assert b'src="app.js"' in body and "script-src 'self'" in headers["Content-Security-Policy"]
    status, _, body = _get(server + "/data/bundle.json?nocache=1")
    assert status == 200 and json.loads(body)["final_score"] == [2, 3]
    _, headers, body = _get(server + "/data/overlays.json")
    assert headers["Content-Disposition"] == 'attachment; filename="matchlens-overlays-ml1.json"'
    assert json.loads(body)["schema"] == OVERLAY_SCHEMA


@pytest.mark.parametrize("path", ["/../pyproject.toml", "/%2e%2e/LICENSE", "/data/../app.py", "/x"])
def test_server_rejects_unknown_paths(server, path):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(server + path)
    assert exc.value.code == 404


def test_cli_export_site_and_overlays(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    assert main(["export-site", "--out", str(tmp_path / "site")]) == 0
    assert (tmp_path / "site" / "index.html").exists()
    match_file = tmp_path / "m.jsonl"
    assert main(["generate", "--seed", "1", "--out", str(match_file)]) == 0
    assert main(["overlays", str(match_file), "--out", str(tmp_path / "o.json"), "--offline"]) == 0
    doc = json.loads((tmp_path / "o.json").read_text())
    assert doc["final_score"] == [2, 3] and doc["cues"]
    assert main(["export-site", "--file", str(match_file), "--out", str(tmp_path / "s2")]) == 0
    same = json.loads((tmp_path / "s2" / "data" / "bundle.json").read_text())
    assert same == json.loads((tmp_path / "site" / "data" / "bundle.json").read_text())

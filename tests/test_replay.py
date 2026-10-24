"""Integration tests: replay bundle, overlay document, static export and HTTP server."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from matchlens.cli import main
from matchlens.generator import generate_match
from matchlens.metrics import as_of_id, compute_metrics
from matchlens.narrative import AI_UNVERIFIED, TEMPLATE
from matchlens.overlays import OVERLAY_SCHEMA
from matchlens.replay import BUNDLE_SCHEMA, DEFAULT_DEMO_SEED, build_bundle, dumps, stat_steps
from matchlens.server import WEB_FILES, asset_version, export_site, make_server, site_files

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
        # seed 1: goals at 5', 56', 71', 77', 81', the missed big chance at 38', full time
        assert [c["moment_id"].split(":")[1] for c in cards] == [
            "ml1-00089",
            "ml1-00591",
            "ml1-00871",
            "ml1-01112",
            "ml1-01206",
            "ml1-01253",
            "ml1-01399",
        ]
        mode_cues = [c for c in cues if c["mode"] == mode]
        assert [c["cue_id"] for c in mode_cues] == [c["card_id"] for c in cards]
        for card, cue in zip(cards, mode_cues, strict=True):
            assert cue["start_s"] == card["publish_clock_s"] < cue["end_s"] == card["end_clock_s"]
            assert cue["anchor_s"] == card["anchor_clock_s"] <= cue["start_s"]
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


def test_cues_cite_only_events_that_have_happened(bundle):
    """As the browser sees it: when a card appears, every event it relies on is in the past."""
    clock = {e["event_id"]: e["clock_s"] for e in bundle["events"]}
    cards = {c["card_id"]: c for cs in bundle["cards"].values() for c in cs}
    assert bundle["overlays"]["cues"]
    for cue in bundle["overlays"]["cues"]:
        cited = set(cue["evidence_event_ids"])
        for fact in cue["facts"]:
            cited.update(fact["event_ids"])
            for metric_id in fact["metric_ids"]:
                cited.update(bundle["metrics"][metric_id]["source_event_ids"])
        assert max(clock[i] for i in cited) <= cue["start_s"]
        assert cue["start_s"] == cards[cue["cue_id"]]["publish_clock_s"]


def test_stat_steps_match_metrics_as_of_each_shot(match, bundle):
    """The stats panel changes on the shot itself, never a minute later."""
    steps = bundle["stat_steps"]
    assert steps == stat_steps(match)
    shots = [e for e in match.events if e.type == "shot"]
    assert [s["seq"] for s in steps] == [e.seq for e in shots]
    for step, shot in zip(steps, shots, strict=True):
        metrics = compute_metrics(match, as_of_event_id=shot.event_id)
        for side in ("home", "away"):
            for name in ("goals", "shots"):
                assert (
                    step[f"{side}.{name}"]
                    == metrics[as_of_id(f"{side}.{name}", shot.event_id)].value
                )
            xg = metrics[as_of_id(f"{side}.xg_heuristic", shot.event_id)].value
            assert step[f"{side}.xg_heuristic"] == pytest.approx(xg, abs=1e-3)
    final = compute_metrics(match)
    assert steps[-1]["away.goals"] == final["away.goals"].value == bundle["final_score"][1]


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


def css_source():
    return (Path(__file__).parents[1] / "src/matchlens/web/style.css").read_bytes()


def test_assets_are_referenced_by_content_hash(tmp_path, bundle):
    files = site_files(bundle)
    for page, names in (
        ("index.html", ("style.css", "app.js")),
        ("broadcast.html", ("broadcast.css", "broadcast.js")),
    ):
        html = files[page].decode()
        for name in names:
            assert f"{name}?v={asset_version(files[name])}" in html
            assert f'="{name}"' not in html
    assert site_files(bundle) == files  # deterministic
    out = tmp_path / "site"
    export_site(bundle, out)
    assert (out / "index.html").read_bytes() == files["index.html"]
    assert (out / "broadcast.html").read_bytes() == files["broadcast.html"]


def test_versioned_page_rejects_a_template_without_the_reference():
    from matchlens.server import _versioned_page

    files = {
        "index.html": b'<link href="style.css"><script src="main.js">',
        "style.css": b"",
        "app.js": b"",
    }
    with pytest.raises(ValueError, match="app.js"):
        _versioned_page(files, "index.html")


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
    assert b'src="app.js?v=' in body and "script-src 'self'" in headers["Content-Security-Policy"]
    status, _, css = _get(server + "/style.css?v=" + asset_version(css_source()))
    assert status == 200 and css == css_source()  # query string is ignored when serving
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


# --- More matches for the picker ---------------------------------------------------------


@pytest.fixture(scope="module")
def extras():
    from matchlens.replay import demo_extra_bundles

    return json.loads(dumps(demo_extra_bundles()))


def test_extra_matches_are_offline_and_never_replay_the_recorded_run(extras):
    from matchlens.agent import OFFLINE, RECORDED
    from matchlens.replay import DEMO_EXTRA_SEEDS

    assert [b["meta"]["seed"] for b in extras] == list(DEMO_EXTRA_SEEDS)
    for b in extras:
        assert b["provider"] == "offline_template" and "live_check" not in b
        proposers = {r["proposer"] for r in b["reviews"].values()}
        assert proposers == {OFFLINE} and RECORDED not in proposers
        ai = b["highlights"].get("ai_check")
        if ai:  # only the injected test claim is rejected: the shortcut must not say "AI"
            assert ai["label"].startswith("See the validator reject a wrong claim")


def test_site_lists_every_match_and_serves_each_one(tmp_path, bundle, extras):
    from matchlens.server import MATCHES_SCHEMA

    files = site_files(bundle, extras)
    index = json.loads(files["data/matches.json"])
    assert index["schema"] == MATCHES_SCHEMA
    entries = index["matches"]
    assert [e["seed"] for e in entries] == [1] + [b["meta"]["seed"] for b in extras]
    assert (
        entries[0]["file"] == "data/bundle.json" and entries[0]["overlays"] == "data/overlays.json"
    )
    for entry, b in zip(entries[1:], extras, strict=True):
        assert json.loads(files[entry["file"]]) == b
        assert json.loads(files[entry["overlays"]]) == b["overlays"]
        assert entry["file"] == f"data/matches/{b['meta']['match_id']}.json"
        assert entry["final_score"] == b["final_score"] and not entry["recorded_foundry_run"]
    out = tmp_path / "site"
    export_site(bundle, out, extras)
    assert json.loads((out / "data" / "matches" / "ml121.json").read_text())["meta"]["seed"] == 121
    # A single-match site still has an index, so the page never requests a missing file.
    assert len(json.loads(site_files(bundle)["data/matches.json"])["matches"]) == 1


def test_duplicate_matches_are_refused(bundle, extras):
    with pytest.raises(ValueError, match="duplicate"):
        site_files(bundle, [extras[0], extras[0]])
    with pytest.raises(ValueError, match="duplicate"):
        site_files(bundle, [bundle])


def test_export_site_includes_extra_matches_only_for_the_demo_seed(tmp_path):
    from matchlens.replay import DEMO_EXTRA_SEEDS

    def seeds(out):
        return [
            m["seed"] for m in json.loads((out / "data" / "matches.json").read_text())["matches"]
        ]

    assert main(["export-site", "--offline", "--out", str(tmp_path / "a")]) == 0
    assert seeds(tmp_path / "a") == [1, *DEMO_EXTRA_SEEDS]
    assert main(["export-site", "--offline", "--single-match", "--out", str(tmp_path / "b")]) == 0
    assert seeds(tmp_path / "b") == [1]
    assert main(["export-site", "--offline", "--seed", "7", "--out", str(tmp_path / "c")]) == 0
    assert seeds(tmp_path / "c") == [7]


def test_overlay_downloads_are_named_after_their_match(bundle, extras):
    srv = make_server(bundle, port=0, extras=extras)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}/"
        for path, name in (
            ("data/overlays.json", "ml1"),
            ("data/matches/ml121-overlays.json", "ml121"),
        ):
            with urllib.request.urlopen(base + path) as resp:
                assert (
                    f'filename="matchlens-overlays-{name}.json"'
                    in resp.headers["Content-Disposition"]
                )
        with urllib.request.urlopen(base + "data/matches/ml121.json") as resp:
            assert "Content-Disposition" not in resp.headers
    finally:
        srv.shutdown()
        srv.server_close()

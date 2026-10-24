"""Command-line entry point: ``python -m matchlens <command>`` (see ``--help``)."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import urllib.error
from dataclasses import asdict
from pathlib import Path

from .agent import (
    AgentError,
    FoundryAgentProposer,
    OfflineProposer,
    RecordedProposer,
    format_review,
    live_review,
    review_cards,
    trace_problems,
)
from .azure_check import run_check
from .cards import build_cards, format_card
from .generator import FULL_PERIOD_S, generate_match
from .io import MatchFormatError, dumps_match, read_match
from .live import DEMO_SEED as LIVE_DEMO_SEED
from .live import LIVE_PATH, LiveChecker, checker_from_env
from .metrics import DEFAULT_WINDOW_S, compute_metrics
from .moments import describe_moment, detect_moments, select_key_moments
from .narrative import (
    ANALYST,
    MODES,
    AzureOpenAIConfig,
    OfflineTemplateProvider,
    ResponseFormatError,
    provider_from_env,
)
from .overlays import build_overlay_document
from .replay import DEFAULT_DEMO_SEED, build_bundle, dumps
from .server import export_site, make_server
from .validate import assert_valid, validate_match


class ConfigError(Exception):
    pass


def _provider(offline: bool):
    if offline:
        return OfflineTemplateProvider()
    try:
        return provider_from_env()
    except ValueError as exc:  # e.g. a non-https endpoint; never echo the key
        raise ConfigError(f"Azure OpenAI configuration error: {exc}") from None


def _azure_config() -> AzureOpenAIConfig | None:
    try:
        return AzureOpenAIConfig.from_env()
    except ValueError as exc:
        raise ConfigError(f"Azure OpenAI configuration error: {exc}") from None


def _proposer(offline: bool, trace: Path | None):
    if trace is not None:
        try:
            return RecordedProposer(json.loads(trace.read_text(encoding="utf-8")), trace.as_posix())
        except (OSError, ValueError, KeyError) as exc:
            raise ConfigError(
                f"cannot use agent trace {trace}: {type(exc).__name__}: {exc}"
            ) from None
    config = None if offline else _azure_config()
    return FoundryAgentProposer(config) if config else OfflineProposer()


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matchlens")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate", help="generate a synthetic match as JSONL")
    gen.add_argument("--seed", type=int, required=True)
    gen.add_argument(
        "--period-length",
        type=int,
        default=FULL_PERIOD_S,
        help="seconds per period (default: 2700)",
    )
    gen.add_argument("--out", type=Path, help="output path (default: stdout)")
    val = sub.add_parser("validate", help="check a JSONL match for format and invariants")
    val.add_argument("path", type=Path)
    met = sub.add_parser("metrics", help="compute event-grounded metrics as JSON")
    met.add_argument("path", type=Path)
    met.add_argument("--window", type=int, default=DEFAULT_WINDOW_S, help="window seconds")
    mom = sub.add_parser("moments", help="detect and select key moments as JSON")
    mom.add_argument("path", type=Path)
    mom.add_argument("--limit", type=int, default=None, help="first N card moments")
    crd = sub.add_parser("cards", help="build verified insight cards for one audience mode")
    crd.add_argument("path", type=Path)
    crd.add_argument("--mode", choices=MODES, required=True)
    crd.add_argument("--limit", type=int, default=None, help="first N card moments")
    crd.add_argument("--json", action="store_true", help="emit JSON instead of text")
    crd.add_argument("--offline", action="store_true", help="ignore Azure settings")
    demo = sub.add_parser("demo", help="generate a match and print casual and analyst cards")
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--limit", type=int, default=3)
    demo.add_argument("--offline", action="store_true", help="ignore Azure settings")
    chk = sub.add_parser(
        "azure-check", help="make ONE live Azure OpenAI/Foundry call (no fallback) and report"
    )
    chk.add_argument("--timeout", type=float, default=30.0)
    agr = sub.add_parser(
        "agent-review",
        help="bounded Foundry claim agent on the analyst cards; the validator checks every claim",
    )
    agr.add_argument("--seed", type=int, default=DEFAULT_DEMO_SEED)
    agr.add_argument("--limit", type=int, default=None, help="first N cards")
    agr.add_argument("--offline", action="store_true", help="deterministic offline proposer")
    agr.add_argument("--out", type=Path, help="write the redacted trace of a live run (JSON)")
    agr.add_argument("--timeout", type=float, default=30.0)
    ovl = sub.add_parser("overlays", help="timed overlay cues (both modes) as JSON")
    ovl.add_argument("path", type=Path)
    ovl.add_argument("--out", type=Path, help="output path (default: stdout)")
    ovl.add_argument("--offline", action="store_true", help="ignore Azure settings")
    for name, help_text in (
        ("serve", "serve the replay UI locally"),
        ("export-site", "write the replay UI as a static site"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--seed", type=int, default=DEFAULT_DEMO_SEED)
        p.add_argument("--file", type=Path, help="replay a JSONL match instead of a seed")
        p.add_argument("--offline", action="store_true", help="ignore Azure settings")
        p.add_argument("--agent-trace", type=Path, help="replay a recorded agent-review trace")
        p.add_argument(
            "--live-api",
            action="store_true",
            help=f"show 'Check live' buttons that POST to {LIVE_PATH} (demo seed only)"
            + ("; served here from the Azure settings" if name == "serve" else ""),
        )
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8000)
        else:
            p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "demo":
        match = assert_valid(generate_match(args.seed))
        provider = _provider(args.offline)
        h, a = match.final_score
        meta = match.meta
        print(f"Synthetic match {meta.match_id}: {meta.home.name} {h}-{a} {meta.away.name}")
        print(f"Narrative provider: {provider.name}\n")
        for mode in MODES:
            card_set = build_cards(match, mode, provider, args.limit)
            print("\n\n".join(format_card(c) for c in card_set.cards) + "\n")
        return 0

    if args.command == "azure-check":
        code, report = run_check(os.environ, timeout_s=args.timeout)
        json.dump(report, sys.stdout, indent=1, ensure_ascii=False)
        sys.stdout.write("\n")
        return code

    if args.command == "agent-review":
        return _agent_review(args)

    if args.command in ("serve", "export-site"):
        if args.file:
            try:
                match = read_match(args.file)
            except MatchFormatError as exc:
                print("\n".join(exc.errors), file=sys.stderr)
                return 1
            match = assert_valid(match)
        else:
            match = assert_valid(generate_match(args.seed))
        provider = _provider(args.offline)
        bundle = build_bundle(match, provider, proposer=_proposer(args.offline, args.agent_trace))
        checker = None
        if args.live_api:
            if args.file or args.seed != LIVE_DEMO_SEED:
                print(f"--live-api works only with the demo seed {LIVE_DEMO_SEED}", file=sys.stderr)
                return 2
            bundle["live_check"] = {"path": LIVE_PATH}
            if args.command == "serve":
                try:
                    checker = (
                        LiveChecker(None) if args.offline else checker_from_env(memory_quota=True)
                    )
                except ValueError as exc:
                    print(f"invalid live-check settings: {exc}", file=sys.stderr)
                    return 2
        if args.command == "export-site":
            try:
                export_site(bundle, args.out)
            except FileExistsError as exc:
                print(f"refusing to write: {exc}", file=sys.stderr)
                return 1
            print(f"wrote static site to {args.out} (provider: {provider.name})", file=sys.stderr)
            return 0
        server = make_server(bundle, args.host, args.port, checker)
        host, port = server.server_address[:2]
        print(
            f"MatchLens replay: http://{host}:{port}/ (provider: {provider.name}; Ctrl+C to stop)"
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0

    if args.command == "generate":
        match = assert_valid(generate_match(args.seed, period_length_s=args.period_length))
        text = dumps_match(match)
        if args.out:
            args.out.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {len(match.events)} events to {args.out}", file=sys.stderr)
        else:
            sys.stdout.write(text)
        return 0

    try:
        match = read_match(args.path)
    except MatchFormatError as exc:
        print("\n".join(exc.errors), file=sys.stderr)
        return 1
    issues = validate_match(match)
    if issues:
        print("\n".join(issues), file=sys.stderr)
        return 1
    if args.command == "metrics":
        records = [m.to_record() for m in compute_metrics(match, args.window).values()]
        json.dump(records, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    if args.command == "cards":
        provider = _provider(args.offline)
        card_set = build_cards(match, args.mode, provider, args.limit)
        if args.json:
            json.dump(asdict(card_set), sys.stdout, indent=1, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print("\n\n".join(format_card(c) for c in card_set.cards))
            for moment_id, reason in card_set.suppressed:
                print(f"suppressed {moment_id}: {reason}")
        return 0
    if args.command == "overlays":
        provider = _provider(args.offline)
        card_sets = [build_cards(match, mode, provider) for mode in MODES]
        text = dumps(build_overlay_document(match, card_sets)) + "\n"
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
        return 0
    if args.command == "moments":
        chosen = select_key_moments(detect_moments(match), args.limit)
        records = [{**asdict(m), "text": describe_moment(m, match)} for m in chosen]
        json.dump(records, sys.stdout, indent=1)
        sys.stdout.write("\n")
        return 0
    h, a = match.final_score
    print(f"OK: {len(match.events)} events, final score {h}-{a}")
    return 0


def _progress(line: str) -> None:
    print(f"[agent] {line}", file=sys.stderr, flush=True)


def _short_token(value: object) -> str | None:
    """Echo a header or error code only when it is a short plain token (never free text)."""
    text = str(value) if value is not None else ""
    return text if text and len(text) <= 64 and text.replace(".", "").isalnum() else None


def _http_detail(exc: urllib.error.HTTPError) -> str:
    code = None
    with contextlib.suppress(Exception):  # the body may be absent or not JSON
        code = _short_token(json.loads(exc.read()).get("error", {}).get("code"))
    after = _short_token(exc.headers.get("retry-after")) if exc.headers else None
    parts = [f"error code {code}" if code else "", f"retry-after {after} s" if after else ""]
    detail = ", ".join(p for p in parts if p)
    if exc.code == 429:
        detail += (
            ", " if detail else ""
        ) + "still rate-limited after waiting; see AZURE_VERIFICATION.md 5b"
    return f" ({detail})" if detail else ""


def _agent_review(args: argparse.Namespace) -> int:
    match = assert_valid(generate_match(args.seed))
    card_set = build_cards(match, ANALYST, OfflineTemplateProvider(), args.limit)
    if args.offline:
        if args.out:
            raise ConfigError("--out records live runs only; drop --offline")
        reviews = review_cards(match, card_set, OfflineProposer())
    else:
        config = _azure_config()
        if config is None:
            raise ConfigError(
                "set AZURE_OPENAI_ENDPOINT, _API_KEY and _DEPLOYMENT, or pass --offline"
            )
        try:
            reviews, trace = live_review(
                match, card_set, config, timeout_s=args.timeout, log=_progress
            )
        except urllib.error.HTTPError as exc:
            print(f"live agent run failed: HTTP {exc.code}{_http_detail(exc)}", file=sys.stderr)
            return 1
        except urllib.error.URLError as exc:  # no HTTP response; the reason holds no headers
            reason = exc.reason
            detail = (
                f"{type(reason).__name__}: {reason}" if isinstance(reason, Exception) else reason
            )
            print(f"live agent run failed: URLError ({str(detail)[:200]})", file=sys.stderr)
            return 1
        except (ResponseFormatError, AgentError) as exc:  # fixed messages, no content
            print(f"live agent run failed: {type(exc).__name__} ({exc})", file=sys.stderr)
            return 1
        except Exception as exc:  # never echo exception text that could contain headers
            print(f"live agent run failed: {type(exc).__name__}", file=sys.stderr)
            return 1
        problems = trace_problems(trace)
        if problems:  # completed, but not acceptable evidence: show it, record nothing
            print("\n\n".join(format_review(r) for r in reviews))
            print("live run is not acceptable evidence:", file=sys.stderr)
            print("\n".join(f"- {p}" for p in problems), file=sys.stderr)
            return 1
        if args.out:
            args.out.write_text(json.dumps(trace, indent=1, ensure_ascii=False) + "\n", "utf-8")
            print(f"wrote agent trace to {args.out}", file=sys.stderr)
    print("\n\n".join(format_review(r) for r in reviews))
    return 0

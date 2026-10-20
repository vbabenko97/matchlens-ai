"""Command-line entry point: ``python -m matchlens <command>`` (see ``--help``)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .cards import build_cards, format_card
from .generator import FULL_PERIOD_S, generate_match
from .io import MatchFormatError, dumps_match, read_match
from .metrics import DEFAULT_WINDOW_S, compute_metrics
from .moments import describe_moment, detect_moments, select_key_moments
from .narrative import MODES, OfflineTemplateProvider, provider_from_env
from .overlays import build_overlay_document
from .replay import DEFAULT_DEMO_SEED, build_bundle, dumps
from .server import export_site, make_server
from .validate import assert_valid, validate_match


def main(argv: list[str] | None = None) -> int:
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
    mom.add_argument("--limit", type=int, default=4)
    crd = sub.add_parser("cards", help="build verified insight cards for one audience mode")
    crd.add_argument("path", type=Path)
    crd.add_argument("--mode", choices=MODES, required=True)
    crd.add_argument("--limit", type=int, default=4)
    crd.add_argument("--json", action="store_true", help="emit JSON instead of text")
    crd.add_argument("--offline", action="store_true", help="ignore Azure settings")
    demo = sub.add_parser("demo", help="generate a match and print casual and analyst cards")
    demo.add_argument("--seed", type=int, default=42)
    demo.add_argument("--limit", type=int, default=3)
    demo.add_argument("--offline", action="store_true", help="ignore Azure settings")
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
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8000)
        else:
            p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "demo":
        match = assert_valid(generate_match(args.seed))
        provider = OfflineTemplateProvider() if args.offline else provider_from_env()
        h, a = match.final_score
        meta = match.meta
        print(f"Synthetic match {meta.match_id}: {meta.home.name} {h}-{a} {meta.away.name}")
        print(f"Narrative provider: {provider.name}\n")
        for mode in MODES:
            card_set = build_cards(match, mode, provider, args.limit)
            print("\n\n".join(format_card(c) for c in card_set.cards) + "\n")
        return 0

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
        provider = OfflineTemplateProvider() if args.offline else provider_from_env()
        bundle = build_bundle(match, provider)
        if args.command == "export-site":
            export_site(bundle, args.out)
            print(f"wrote static site to {args.out} (provider: {provider.name})", file=sys.stderr)
            return 0
        server = make_server(bundle, args.host, args.port)
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
        provider = OfflineTemplateProvider() if args.offline else provider_from_env()
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
        provider = OfflineTemplateProvider() if args.offline else provider_from_env()
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

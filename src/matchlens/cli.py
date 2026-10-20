"""Command-line entry point: ``python -m matchlens {generate,validate}``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .generator import FULL_PERIOD_S, generate_match
from .io import MatchFormatError, dumps_match, read_match
from .metrics import DEFAULT_WINDOW_S, compute_metrics
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
    args = parser.parse_args(argv)

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
    h, a = match.final_score
    print(f"OK: {len(match.events)} events, final score {h}-{a}")
    return 0

# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [ ] M2 (Oct 21-22): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [ ] M3 (Oct 23-24): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [ ] M4 (Oct 25): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-20)
- **M0 verified:** stdlib-only Python package (`src/matchlens`), `pyproject.toml` with `[dev]` extras (pytest, ruff), GitHub Actions CI (`.github/workflows/ci.yml`, Python 3.11 + 3.13: ruff check, ruff format --check, pytest). Deployment/credentials plan in `docs/DEPLOYMENT.md` (no credentials confirmed or stored). CI has not yet run on GitHub — confirm on first push.
- **M1 verified:** `matchlens.generator` (seeded, original state machine; fictional teams), `matchlens.io` (JSONL + malformed-record errors), `matchlens.validate` (IDs, seq, clock/periods, score, kick-off after goal, restarts, possession chains, 1.0 m ball continuity, xG-heuristic placement). CLI: `python -m matchlens generate|validate`. Checked-in fixture `data/fixtures/demo_seed7_5min.jsonl` (seed 7, 2x300 s, 161 events) is a golden byte-for-byte test. Conventions in `docs/DATA_MODEL.md`.
- **Verified results:** `python -m pytest -q` -> 53 passed (Python 3.13 and 3.11); `ruff check .` and `ruff format --check .` clean. Seeds 1-60 produce 0 invariant violations (~24 shots, ~3.6 goals avg). Full match seed 42 SHA-256 identical on 3.11 and 3.13.
- **Next smallest task:** M2 — metric engine (passes/completion, shots, goals, xG heuristic totals, pressures, turnovers, rolling windows) where every metric has an ID and lists its source event IDs; hand-checked tests on the 5-minute fixture.
- **Last verified command:** `python -m pytest -q && ruff check . && ruff format --check .`
- **Known blockers / operator decisions:** Azure/Foundry access, region, model deployment and spend cap unconfirmed; hosting choice unconfirmed; project license not yet chosen (needed for definition of done).

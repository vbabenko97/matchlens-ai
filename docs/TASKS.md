# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [ ] M2 (Oct 21-22, metrics + claim checks done; detectors pending): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [ ] M3 (Oct 23-24): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [ ] M4 (Oct 25): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-20)
- **M0/M1 verified on GitHub:** PR https://github.com/vbabenko97/matchlens-ai/pull/1 — GitHub Actions `test (3.11)` and `test (3.13)` passed on head `cb4b149` (push and pull_request runs). MIT license added after checking third-party licenses (no runtime deps; dev tools MIT/BSD/Apache, not redistributed).
- **M2 (partial) verified:** `matchlens.metrics` computes per-team passes, completions, completion rate (`null` if undefined), shots, shots on target, goals, pressures, turnovers, possessions, and the synthetic xG heuristic (labelled `heuristic`), plus 15-minute window counts. Every metric has a stable ID, a definition and its source event IDs. `matchlens.claims.verify_claim` marks claims `unsupported` with reasons (no evidence, unknown/undefined metric, wrong value, nonexistent or non-source events, undisclosed heuristic). CLI: `python -m matchlens metrics <file>`. Hand-built 24-event fixture (`tests/hand_match.py`) with hand-counted expected values and sources.
- **Verified results:** `python -m pytest -q` -> 89 passed (Python 3.13 and 3.11 locally); `ruff check .` and `ruff format --check .` clean. GitHub CI for the M2 push: check the PR.
- **Remaining M2:** 2-4 deterministic key-moment detectors (e.g. goal with build-up chain, xG-heuristic swing window, pressing spell leading to a turnover, big chance missed) with hand-checked tests.
- **Next smallest task:** key-moment detector for goals, citing the goal's possession chain events and the metrics they affect.
- **Last verified command:** `python -m pytest -q && ruff check . && ruff format --check .`
- **Blockers / decisions:** Azure access, region, model deployment and spend cap unconfirmed — configuration stays environment-based (`.env.example`), no paid resources provisioned. Hosting decision (Azure App Service acceptable in principle) deferred until Azure availability, costs and hackathon requirements are verified.

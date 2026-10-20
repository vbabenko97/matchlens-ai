# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [x] M2 (Oct 21-22): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [ ] M3 (Oct 23-24): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [ ] M4 (Oct 25): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-20)
- **M0/M1 verified on GitHub:** PR https://github.com/vbabenko97/matchlens-ai/pull/1 — Actions `test (3.11)` and `test (3.13)` green on `cb4b149` and `5751d67`. MIT license after third-party license check.
- **M2 verified (locally; GitHub CI for this push: see PR):**
  - `matchlens.metrics`: per-team and 15-min-window metrics with stable IDs, definitions, `fact`/`heuristic` kind and source event IDs; undefined ratios are `null`.
  - `matchlens.claims`: **fixed the free-text loophole.** `Claim` no longer has a text field; verified statements are rendered from templates using only verified data (`ClaimCheck.text`); `assess_prose` always returns `unverified`. Negative tests: a `Claim(text=...)` is rejected, wording cannot depend on caller prose, and false, true and fake-citation prose all stay unverified.
  - `matchlens.moments`: three deterministic detectors (`goal` with build-up chain, `big_chance_missed` at xG heuristic >= 0.2, `press_win` = pressure -> win <= 8 s -> shot <= 20 s), deterministic selection of up to 4 with a reserved non-goal slot, and template descriptions citing evidence IDs. CLI: `python -m matchlens moments <file>`.
  - Hand-verified tests on the 24-event `tests/hand_match.py` (every expected value and evidence ID counted by hand), plus property tests on generated matches.
- **Verified results:** `python -m pytest -q` -> 106 passed on Python 3.13 and 3.11 locally; `ruff check .` and `ruff format --check .` clean.
- **Next smallest task (M3):** narrative cards built from selected moments + verified metric claims, two audience modes (casual vs analyst) differing in content; then the env-configured Azure/Foundry rendering path with the labelled deterministic fallback (no provisioning).
- **Last verified command:** `python -m pytest -q && ruff check . && ruff format --check .`
- **Blockers / decisions:** Azure access, region, model deployment and spend cap unconfirmed — configuration stays environment-based (`.env.example`), nothing provisioned. Hosting deferred until Azure availability, costs and hackathon requirements are verified. Any LLM text in M3 must be labelled unverified (see `docs/DATA_MODEL.md`).

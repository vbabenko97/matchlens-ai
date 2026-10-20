# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [x] M2 (Oct 21-22): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [ ] M3 (Oct 23-24; code done, live Azure run blocked on access): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [x] M4 (Oct 20, local; public hosting is M5): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-20)
- **PR #1 merged** (`6d8e589`). **PR #2** (https://github.com/vbabenko97/matchlens-ai/pull/2) open with M3 (CI green at `5fcbcec`) and now M4. Only one branch is authorized, so both milestones share the PR.
- **M3 (code) verified:** insight cards, Casual/Analyst modes, `AzureOpenAIProvider` + offline fallback. A live Azure run is still blocked on access.
- **M4 verified locally:**
  - **UI:** static HTML/CSS/vanilla JS (`src/matchlens/web/`), no new runtime dependencies. It has a scoreboard, a pitch view of recent ball events, timeline markers, synchronized insight cards with a lower-third overlay, a Casual/Analyst toggle, and an Analyst evidence inspector (event JSON, metric definition and sources, pitch highlight).
  - **Replay controls:** play/pause, seek, previous/next moment, 15–240× speed, keyboard shortcuts, and an Overlay JSON download.
  - **Data:** `matchlens.replay` bundle (precomputed, deterministic), `matchlens.overlays` (`matchlens.overlays/v1`), and `matchlens.server` (stdlib, localhost by default, strict CSP, fixed file set).
  - **CLI:** `serve` (default seed 1 → 2-3), `export-site`, `overlays`.
  - Runs fully offline without Azure credentials. AI wording, if configured, keeps its NOT VERIFIED label; `unsupported_numbers` warnings are labelled as heuristic only.
- **Verified results:**
  - `python -m pytest -q` -> 157 passed locally on Python 3.13 including 9 Playwright/Chromium e2e tests (148 passed + e2e module skipped on 3.11 without Playwright). The e2e suite passed 5/5 consecutive runs.
  - `ruff check .` and `ruff format --check .` clean.
  - Manual check: `python -m matchlens serve` serves the UI and the 2-3 bundle; unknown paths return 404. Screenshots were checked at 1400×900 and 390×844 (no horizontal scroll).
  - GitHub Actions green at `c7938c5`: `test (3.11)`, `test (3.13)` and the new `e2e` job (Playwright Chromium) in both push (39471601403) and pull_request (39471608254) runs.
- **Next smallest task (M5, needs operator approval):** choose and approve hosting (static export fits any static host; Azure Static Web Apps or App Service if Azure is approved), accessibility pass, demo rehearsal and video.
- **Last verified command:** `python -m pytest -q && ruff check . && ruff format --check .`
- **Blockers / decisions:** Azure access, region, model deployment and spend cap unconfirmed; nothing provisioned. Hosting deferred until Azure availability, costs and hackathon requirements are verified. Live Azure narrative run pending access.

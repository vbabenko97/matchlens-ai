# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [x] M2 (Oct 21-22): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [ ] M3 (Oct 23-24; code done, live Azure run blocked on access): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [x] M4 (Oct 20, local; public hosting is M5): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26; deployment prepared, waiting on operator Azure/Pages steps): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-20)
- **PR #2 merged** (`ad818eb`) after review. Two blocking fixes landed first (CI green at `d11dad6`):
  - `export-site` used to `rmtree` its `--out` directory (`--out .` would wipe the working tree); it now refuses non-empty non-export directories and never deletes;
  - an invalid Azure config gives exit code 2 with a message instead of a traceback.
- **M5 prepared (not deployed):**
  - Static export with a CSP meta tag, plus `deploy/azure-static-web-apps/staticwebapp.config.json` (security headers).
  - `.github/workflows/deploy-swa.yml` (Azure Static Web Apps Free, manual, needs `AZURE_STATIC_WEB_APPS_API_TOKEN`).
  - `.github/workflows/deploy-pages.yml` (GitHub Pages fallback, manual).
  - `.github/workflows/public-smoke.yml` (Playwright against the deployed URL after each deploy, on demand, and daily through 2026-11-10 via the `PUBLIC_DEMO_URL` variable).
  - `actionlint` clean.
  - `python -m matchlens azure-check`: exactly one live call, no fallback, redacted JSON evidence.
  - `docs/AZURE_VERIFICATION.md` (concrete Foundry checklist) and `docs/DEPLOYMENT.md` (operator steps).
- **Verified results:**
  - `python -m pytest -q` -> 175 passed (incl. 18 e2e: app server + static sub-path) on Python 3.13; 3.11 without Playwright passes with e2e skipped.
  - `MATCHLENS_BASE_URL=<static export on an independent host> python -m pytest -q -m e2e` -> 9 passed, so the suite can test a public URL.
  - `ruff check .`, `ruff format --check .` clean.
  - Azure tests use a fake transport and are **not** integration evidence.
- **Stopped at operator steps:**
  - (1) create the Free Static Web App and add its deployment token secret — or enable Pages (Source: GitHub Actions);
  - (2) run the deploy workflow and set `PUBLIC_DEMO_URL`;
  - (3) approve spend and create a Foundry model deployment, then run `azure-check` per `docs/AZURE_VERIFICATION.md`.
- **Next after operator steps:** confirm the public smoke run is green, record the Azure evidence, then the accessibility pass and demo rehearsal (video not started, per instruction).
- **Last verified command:** `python -m pytest -q && ruff check . && ruff format --check .`

# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [x] M2 (Oct 21-22): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [ ] M3 (Oct 23-24; code done on Responses API v1, live Foundry call confirmed by operator outside MatchLens, recorded `azure-check` run pending): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [x] M4 (Oct 20, local; public hosting is M5): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26; deployment prepared, waiting on operator Azure/Pages steps): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-24): Responses API v1 migration
- **Operator-reported:** a live Microsoft Foundry Responses API call succeeded (`gpt-4.1-mini` deployment, `status: completed`, response ID, 17 input / 27 output tokens). It was made outside MatchLens and no live request ran in Claude Code.
- **Changed:**
  - `AzureOpenAIProvider` now calls `POST {endpoint}/openai/v1/responses` with `model` (= deployment), `input`, `max_output_tokens: 200` and `store: false`. The legacy chat-completions path, `temperature` and `api-version` are gone.
  - `AZURE_OPENAI_API_VERSION` is no longer required. The endpoint may be given as the base URL, `.../openai/v1` or the full `.../openai/v1/responses`.
  - `extract_output_text` joins the `output_text` parts of `message` items in `output`. Non-`completed` status, missing or ill-typed `output`/`content`/`text`, refusal-only replies and the legacy chat shape raise `ResponseFormatError`, and cards fall back to the labelled offline template.
  - `azure-check` requires `status == completed`, a response ID, a model and positive `input_tokens`/`output_tokens`. Otherwise it exits 1 with `error: InvalidResponse` and a `problems` list. The success report adds `api`, `request_path`, `response_status`, `usage` and `store: false`.
- **Verified:** `python -m pytest -q` -> 216 passed, 1 skipped (e2e module skipped here because Playwright is not installed; CI runs it). `ruff check .` and `ruff format --check .` clean. All Azure tests use mocked transports and are **not** integration evidence.
- **Next:** the operator runs one real `python -m matchlens azure-check` (docs/AZURE_VERIFICATION.md §2) and commits the redacted JSON as `docs/evidence/azure-check-<date>.json`; then M5 deployment.
- **Next command:** `python -m matchlens azure-check | tee azure-check.json` (operator, key entered with `read -rs`).

## Checkpoint (2026-10-20)
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

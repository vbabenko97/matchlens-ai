# Deployment and Microsoft integration

Status (2026-10-24): **public demo live on Azure Static Web Apps at <https://wonderful-hill-04967dd0f.5.azurestaticapps.net/> (with the live-check API) and on GitHub Pages at <https://vbabenko97.github.io/matchlens-ai/> (static mirror).** Every step marked **[operator]** needs your account, credentials or approval.

| check | result |
|---|---|
| Pages deploy | `Deploy demo (GitHub Pages, fallback)` [run 39476401211](https://github.com/vbabenko97/matchlens-ai/actions/runs/39476401211) on `main` @ `e7dc926`: **success** (2026-10-20) |
| `PUBLIC_DEMO_URL` | set to `https://vbabenko97.github.io/matchlens-ai/` (operator) |
| Public smoke test (Playwright e2e against the URL) | [run 1](https://github.com/vbabenko97/matchlens-ai/actions/runs/39478441723) (manual, 2026-10-20) and daily scheduled runs [2](https://github.com/vbabenko97/matchlens-ai/actions/runs/39604056321), [3](https://github.com/vbabenko97/matchlens-ai/actions/runs/39783144846), [4](https://github.com/vbabenko97/matchlens-ai/actions/runs/39955061358) (2026-10-21 to 2026-10-23): **all success**. The `pytest -q -m e2e` step ran in each run and was not skipped. Verified via the GitHub Actions API on 2026-10-24. |
| Manual replay test of the public URL | **passed** (operator-reported, 2026-10-24) |
| Redeploy with cache-busting | [run 40082219742](https://github.com/vbabenko97/matchlens-ai/actions/runs/40082219742) on `main` @ `0aa672c`: build, deploy and post-deploy smoke **success** (2026-10-24 09:30 UTC) |
| Redeploy with accessibility pass | [run 40081567480](https://github.com/vbabenko97/matchlens-ai/actions/runs/40081567480) on `main` @ `2731814`: build, deploy and post-deploy smoke **success** (2026-10-24 09:21 UTC) |
| Azure Static Web Apps | **deployed** (operator created `matchlens-demo` in `rg-matchlens-demo`); latest from `main` @ `14f1b88` by [run 40118847924](https://github.com/vbabenko97/matchlens-ai/actions/runs/40118847924), deploy and smoke **success**. The managed API builds on Python 3.11. GitHub Pages stays the static mirror. |

The deployed site is the offline static export of `main` @ `0aa672c`. It does not depend on the Responses API migration (the public site never calls Azure). Re-run the Pages workflow after merges only if the UI or bundle changes.

## What gets deployed
The public demo is the **static export** of the replay UI: `python -m matchlens export-site --offline --out site`. It has:
- `index.html`, `app.js`, `style.css`;
- `data/bundle.json` (precomputed seed-1 match, cards and metrics);
- `data/overlays.json`;
- `data/matches.json` (the match picker's index) and `data/matches/<match_id>.json` plus `-overlays.json` for the extra matches (seeds 121, 106, 103, 123, 124; offline, untuned; `--single-match` leaves them out).

It runs entirely in the browser, needs no server code or account, and contains **no credentials**. Its wording comes from the deterministic offline template, labelled as such. It therefore stays fully functional without Azure, and hosting costs nothing beyond the free tier.

Caching: GitHub Pages serves assets with a 10-minute cache. `index.html` therefore loads `style.css?v=<hash>` and `app.js?v=<hash>` (content hashes added by `export-site`), so a new deploy never pairs new HTML with old cached CSS/JS. `bundle.json` is fetched with `cache: "no-cache"`.

Security on static hosts:
- `index.html` carries a CSP meta tag (`script-src 'self'`, no inline script or eval).
- Azure Static Web Apps additionally applies the headers in `deploy/azure-static-web-apps/staticwebapp.config.json` (CSP incl. `frame-ancestors 'none'`, `nosniff`, `no-referrer`).

## Option A (alternative, not used): Azure Static Web Apps — Free plan
Workflow: `.github/workflows/deploy-swa.yml` (manual `workflow_dispatch` only). It builds the export, copies `staticwebapp.config.json`, uploads with `Azure/static-web-apps-deploy@v1`, then runs the Playwright smoke suite against the returned URL.

Operator steps:
1. **[operator]** Azure portal → *Create a resource* → **Static Web App**. Choose:
   - **Plan type: Free**;
   - any resource group and region;
   - **Deployment source: Other**, so Azure does not add its own workflow to the repo.
   The Free plan has no hosting charge.
2. **[operator]** In the new resource → *Manage deployment token* → copy it, then in GitHub add it under *Settings → Secrets and variables → Actions → New repository secret* as `AZURE_STATIC_WEB_APPS_API_TOKEN`.
3. **[operator]** *Actions → Deploy demo (Azure Static Web Apps, Free) → Run workflow* on `main`.
4. **[operator]** Copy the site URL (`https://<name>.azurestaticapps.net`) into the repository **variable** `PUBLIC_DEMO_URL` (not a secret). This enables the daily smoke test.

## Option B (in use): GitHub Pages
Workflow: `.github/workflows/deploy-pages.yml` (manual only). It builds the same export, deploys with `actions/deploy-pages`, then runs the Playwright smoke suite. The repository is public, so Pages is free.
1. [x] **[operator]** *Settings → Pages → Build and deployment → Source: **GitHub Actions***.
2. [x] **[operator]** *Actions → Deploy demo (GitHub Pages, fallback) → Run workflow* on `main` (done 2026-10-20).
3. [x] **[operator]** Set `PUBLIC_DEMO_URL` to `https://vbabenko97.github.io/matchlens-ai/`.

The UI uses only relative URLs, so it works under Pages' `/matchlens-ai/` sub-path. This is covered by the e2e `static-subpath` target.

## Availability through 2026-11-10
- Neither host expires a Free/Pages site.
- `.github/workflows/public-smoke.yml` runs the full Playwright suite against `PUBLIC_DEMO_URL` daily (07:23 UTC) through 2026-11-10. A failure emails the repository owner (GitHub's default for scheduled-workflow failures). It skips when the variable is unset.
- Do not disable Pages or make the repository private before 2026-11-11.

## Testing the public URL with Playwright
The same e2e suite runs against a public URL; the expected data is read from the deployment's own `data/bundle.json`:
```bash
pip install -e ".[dev,e2e]" && python -m playwright install chromium
MATCHLENS_BASE_URL=https://<public-url>/ python -m pytest -q -m e2e
```
Verified locally against independent static hosts: the app server, a plain static server under a sub-path, and a separately hosted export via `MATCHLENS_BASE_URL`. The public URL `https://vbabenko97.github.io/matchlens-ai/` passes the same suite in the scheduled smoke workflow (see the status table above).

## Personalizer run (language and club variants) — operator step
**Status (2026-10-24): run recorded and wired in.** The operator recorded `docs/evidence/personalize-trace-2026-10-24.json` at 17:53 UTC (`gpt-4.1-mini`, Responses API v1).
- The run: 56 card runs, 62 calls (6 cards needed a retry), about 22K input / 6.8K output tokens.
- `trace_problems` passes and the file has no key-like strings.
- Both deploy workflows replay it with `--personalize-trace`.
- On replay, the stricter checker added after reading the run (score order, Russian-only letters) keeps 12 of 232 lines in English, among them "Eastvale Athletic перемагають 3-2" on a 2-3 scoreboard.

1. **[operator]** With the same `AZURE_OPENAI_*` variables as for `azure-check`, record all 8 variants of the 7 demo casual cards:
   ```bash
   python -m matchlens personalize-review --out docs/evidence/personalize-trace-2026-10-25.json
   ```
   - That is 56 Responses API calls, plus one retry per card with a rejected line; at the recorded run's sizes, about 50K tokens in total.
   - The command prints every line with ✓ or "kept (reason)" and exits 1 without writing a file if the trace is not acceptable evidence.
2. Check the output (no key in the file: `grep -c "$AZURE_OPENAI_API_KEY" <file>` prints 0), commit it, then add `--personalize-trace docs/evidence/personalize-trace-2026-10-25.json` to the `export-site` lines of `deploy-pages.yml` and `deploy-swa.yml` (one PR), merge and redeploy.

## Live Foundry check on the public demo (Azure Static Web Apps managed API)
**Status (2026-10-24): deployed on SWA; the Foundry app settings are set (the operator read them back for `personalize-review`). Not yet verified: one live call and a forced `daily_limit` (step 7), and whether `MATCHLENS_LIVE_COUNTER_URL` is set.**

What it is: on Azure Static Web Apps, each analyst card gets a **Check this card live with Microsoft Foundry** button. It sends `POST /api/live-check` with `{"card_id": ...}` and nothing else. The function in `api/live_check/` (code: `src/matchlens/live.py`) builds the request itself from the demo match and runs the same bounded agent and validator as `agent-review`. The reply replaces the recorded claim check on that card and is labelled as a live call, with response IDs, token use and latency. The key stays in the app settings; the browser never sees it. GitHub Pages builds have no button (no server side).

Limits, in the order they apply:
1. Only the 7 analyst card IDs of the demo match are accepted, and any other field is a 400, so the endpoint cannot carry a free-text prompt. JSON POST only: a page on another site cannot call it from a visitor's browser. Scripts and `curl` can still call it directly; only the daily cap below limits them.
2. One live run at a time per instance; a second request gets "busy" and costs no quota. This protects capacity, not cost.
3. A courtesy limit of 3 checks per visitor per 10 minutes. It is kept in memory per instance and keyed by a hash of `X-Forwarded-For` / `X-Azure-ClientIP` / `X-Client-IP`. A script can forge these headers, so this is not a cost control.
4. **The spend cap is the shared daily count.** `MATCHLENS_LIVE_DAILY_LIMIT` (default 100) card checks per UTC day, counted in one Azure Table Storage entity per day (`src/matchlens/quota.py`).
   - A check is reserved before any model call, with an ETag-conditional write, so restarts and parallel instances never exceed the cap.
   - The reservation is kept even when the call fails.
   - If the table cannot be read or updated, or `MATCHLENS_LIVE_COUNTER_URL` is missing, no live call is made.
5. A 30 s deadline over all model calls, rate-limit waits included.

Whenever a check is not made or does not finish, the reply gives a reason (`not_configured`, `client_limit`, `daily_limit`, `counter_unavailable`, `busy`, `deadline`, `foundry_unavailable`). The page keeps the recorded run and says it is not a live call.

What the Azure-side settings do and do not do:
- **Budget:** an alert only. Azure budgets do not stop spending, and cost data arrives with a delay ([Microsoft Learn: budgets](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/tutorial-acm-create-budgets)).
- **TPM limit:** caps the request rate, not the total.
- **The daily count (4) is what bounds spend.** One check is at most 12 model calls. The recorded run averaged ≈ 10K input / 0.5K output tokens per card. So 100 checks a day is about 1M input tokens, under $1/day at `gpt-4.1-mini` list prices (check current pricing); a runaway worst case is a few dollars a day.

Operator steps:
1. **[operator]** Create the **Free** Static Web App (Option A steps 1–2 below) and add `AZURE_STATIC_WEB_APPS_API_TOKEN`.
2. **[operator]** Create the shared quota store:
   - Create a Storage account (Standard, LRS; cost is cents a month), then *Storage browser → Tables → + Add table* `livechecks`.
   - Make a SAS for that table only: *Shared access signature* on the table with permissions **Read, Add, Update**, HTTPS only, expiring after the judging period (e.g. 2026-11-30).
   - Its URL looks like `https://<account>.table.core.windows.net/livechecks?sv=…&sig=…`. Treat it as a secret.
3. **[operator]** Static Web App → *Settings → Environment variables* (production):
   - `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` (the same values as for `azure-check`);
   - `MATCHLENS_LIVE_COUNTER_URL` (the table SAS URL);
   - optionally `MATCHLENS_LIVE_DAILY_LIMIT`.
4. **[operator]** Foundry portal → the `gpt-4.1-mini` deployment → *Edit* → tokens-per-minute limit. The recorded run hit 13 HTTP 429s at the current limit; about 50K TPM lets a check finish within 30 s. Change this only after the counter is verified (step 7).
5. **[operator]** Azure portal → *Cost Management → Budgets*: a monthly budget with an alert (e.g. $10), as an early warning.
6. **[operator]** *Actions → Deploy demo (Azure Static Web Apps, Free) → Run workflow*. The workflow copies `src/matchlens` into `api/matchlens` and uploads `api/` as the managed API. Its runtime is `python:3.11` (`staticwebapp.config.json`), which SWA managed functions list as supported ([Microsoft Learn: languages and runtimes](https://learn.microsoft.com/en-us/azure/static-web-apps/languages-runtimes)).
7. **[operator]** Verify in this order (each live call costs tokens):
   - **Live call:**
     ```bash
     curl -s -X POST https://<name>.azurestaticapps.net/api/live-check \
       -H 'Content-Type: application/json' -d '{"card_id":"analyst:goal:ml1-01206"}' | head -c 600
     ```
     Expect `"status":"live"`, response IDs, `usage`, `latency_ms` and `remaining_today`. The `livechecks` table should now hold today's row with `Count` 1.
   - **Fallback:** temporarily set `MATCHLENS_LIVE_DAILY_LIMIT` to the current `Count`. The same call must return `"reason":"daily_limit"` without a model call. Then restore it.
   - `not_configured` means step 3 is incomplete; `counter_unavailable` means the SAS URL is wrong, expired or lacks a permission.

Local run with your own key: `python -m matchlens serve --live-api` (reads the `AZURE_OPENAI_*` variables; uses the table if `MATCHLENS_LIVE_COUNTER_URL` is set, else an in-memory count, acceptable only on your own machine). `--offline` serves the button with `not_configured` replies. Tests use fake transports only (`tests/test_live.py`, the live-button e2e tests); the public smoke test never presses the button.

## Microsoft Foundry / Azure OpenAI inference
- **Code path:** `src/matchlens/narrative.py` (`AzureOpenAIProvider`), Responses API v1 (`/openai/v1/responses`), configured by the three `AZURE_OPENAI_*` variables in `.env.example`. Real values live only in your shell or a secret store.
- **Strict check:** `python -m matchlens azure-check` (one call, no fallback, redacted evidence).
- **Checklist:** [docs/AZURE_VERIFICATION.md](AZURE_VERIFICATION.md).
- **Status (2026-10-24): verified.** `azure-check` made one live Foundry Responses API v1 call to the `gpt-4.1-mini` deployment: `status: completed`, 137 input / 41 output tokens, `store: false`. The redacted report is [evidence/azure-check-2026-10-24.json](evidence/azure-check-2026-10-24.json). Mocked tests do not count as evidence.

The public static demo never calls Azure at runtime (that would need the key in the browser). A build-time Azure-worded bundle, with the key held only in a CI secret, is a possible later step. It needs operator approval and would keep the "not verified" label.

## Credentials inventory (no values in git)
| item | status |
|---|---|
| Azure subscription | **active** (hosts the Foundry deployment) |
| Static Web App (Free) + `AZURE_STATIC_WEB_APPS_API_TOKEN` secret | **created and in use** |
| GitHub Pages source = GitHub Actions | **enabled**; site deployed |
| Foundry / Azure OpenAI model deployment + key | **created** (`gpt-4.1-mini`), verified by `azure-check` 2026-10-24; key held by the operator only |
| `PUBLIC_DEMO_URL` repository variable | **set** to `https://vbabenko97.github.io/matchlens-ai/` |

## Operator decisions (2026-10-20)
- Azure Static Web Apps Free preferred, GitHub Pages fallback. No paid resources without approval.
- 2026-10-24: hosting settled on GitHub Pages; no Static Web App will be created.
- Configuration stays environment-based. Credentials are never committed, logged or shown.

# Deployment and Microsoft integration

Status: **deployment prepared, nothing deployed or provisioned.** Every step marked **[operator]** needs your account, credentials or approval.

## What gets deployed
The public demo is the **static export** of the replay UI: `python -m matchlens export-site --offline --out site`. It has:
- `index.html`, `app.js`, `style.css`;
- `data/bundle.json` (precomputed seed-1 match, cards and metrics);
- `data/overlays.json`.

It runs entirely in the browser, needs no server code or account, and contains **no credentials**. Its wording comes from the deterministic offline template, labelled as such. It therefore stays fully functional without Azure, and hosting costs nothing beyond the free tier.

Security on static hosts:
- `index.html` carries a CSP meta tag (`script-src 'self'`, no inline script or eval).
- Azure Static Web Apps additionally applies the headers in `deploy/azure-static-web-apps/staticwebapp.config.json` (CSP incl. `frame-ancestors 'none'`, `nosniff`, `no-referrer`).

## Option A (preferred): Azure Static Web Apps — Free plan
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

## Option B (fallback): GitHub Pages
Workflow: `.github/workflows/deploy-pages.yml` (manual only). It builds the same export, deploys with `actions/deploy-pages`, then runs the Playwright smoke suite. The repository is public, so Pages is free.
1. **[operator]** *Settings → Pages → Build and deployment → Source: **GitHub Actions***.
2. **[operator]** *Actions → Deploy demo (GitHub Pages, fallback) → Run workflow* on `main`.
3. **[operator]** Set `PUBLIC_DEMO_URL` to `https://vbabenko97.github.io/matchlens-ai/`.

The UI uses only relative URLs, so it works under Pages' `/matchlens-ai/` sub-path. This is covered by the e2e `static-subpath` target.

## Availability through 2026-11-10
- Neither host expires a Free/Pages site.
- `.github/workflows/public-smoke.yml` runs the full Playwright suite against `PUBLIC_DEMO_URL` daily (07:23 UTC) through 2026-11-10. A failure emails the repository owner (GitHub's default for scheduled-workflow failures). It skips when the variable is unset.
- Do not delete the Static Web App, disable Pages, or make the repository private before 2026-11-11.

## Testing the public URL with Playwright
The same e2e suite runs against a public URL; the expected data is read from the deployment's own `data/bundle.json`:
```bash
pip install -e ".[dev,e2e]" && python -m playwright install chromium
MATCHLENS_BASE_URL=https://<public-url>/ python -m pytest -q -m e2e
```
Verified locally against independent static hosts: the app server, a plain static server under a sub-path, and a separately hosted export via `MATCHLENS_BASE_URL`. A real public URL has not been tested yet because nothing is deployed.

## Microsoft Foundry / Azure OpenAI inference
- **Code path:** `src/matchlens/narrative.py` (`AzureOpenAIProvider`), Responses API v1 (`/openai/v1/responses`), configured by the three `AZURE_OPENAI_*` variables in `.env.example`. Real values live only in your shell or a secret store.
- **Strict check:** `python -m matchlens azure-check` (one call, no fallback, redacted evidence).
- **Checklist:** [docs/AZURE_VERIFICATION.md](AZURE_VERIFICATION.md).
- **Status (2026-10-24): verified.** `azure-check` made one live Foundry Responses API v1 call to the `gpt-4.1-mini` deployment: `status: completed`, 137 input / 41 output tokens, `store: false`. The redacted report is [evidence/azure-check-2026-10-24.json](evidence/azure-check-2026-10-24.json). Mocked tests do not count as evidence.

The public static demo never calls Azure at runtime (that would need the key in the browser). A build-time Azure-worded bundle, with the key held only in a CI secret, is a possible later step. It needs operator approval and would keep the "not verified" label.

## Credentials inventory (no values in git)
| item | status |
|---|---|
| Azure subscription | **unconfirmed** — operator |
| Static Web App (Free) + `AZURE_STATIC_WEB_APPS_API_TOKEN` secret | **not created** — operator |
| GitHub Pages source = GitHub Actions | **not enabled** — operator |
| Foundry / Azure OpenAI model deployment + key | **created** (`gpt-4.1-mini`, operator-reported 2026-10-24); key held by the operator only |
| `PUBLIC_DEMO_URL` repository variable | **not set** — after first deploy |

## Operator decisions (2026-10-20)
- Azure Static Web Apps Free preferred, GitHub Pages fallback. No paid resources without approval.
- Configuration stays environment-based. Credentials are never committed, logged or shown.

# Deployment and Microsoft integration plan

Status: **plan only — nothing deployed.** Deployment and any paid resource require explicit operator approval.

## Credentials inventory (no values in git)
| item | status |
|---|---|
| Azure subscription / Microsoft Foundry project | **unconfirmed** — operator to confirm availability and budget |
| Azure OpenAI / Foundry model deployment (endpoint, deployment name, API version) | **unconfirmed** |
| Hosting account for public judge demo | **unconfirmed** |

Configuration is read from environment variables listed in `.env.example`; real values live only in a local `.env` (git-ignored) or the host's secret store.

## Plan
1. **Inference (M3):** one small client module calling a Microsoft Foundry / Azure OpenAI chat deployment to render narrative text from structured, evidence-cited facts. Claims are re-checked against event/metric IDs after generation. If the endpoint is not configured or fails, a deterministic template renderer is used and labelled "Offline fallback — not an Azure response".
2. **Demo hosting (M5):** prefer Azure App Service (Linux, Python) for the Streamlit/browser UI so the Microsoft story is end-to-end; fall back to a free host if Azure hosting is unavailable. Judges must not need an account or API key; the server holds the key, with request caps to bound cost.
3. **Evidence:** README section showing the exact code path, the env vars, and a screenshot/recording of a live Azure-rendered card next to the labelled fallback.

## Operator decisions (2026-10-20)
- Azure access/region/deployment not yet confirmed: keep configuration environment-based, document requirements, provision nothing paid.
- Azure App Service acceptable in principle; hosting deferred until Azure availability, costs and hackathon requirements are verified.

## Open questions for the operator
- Is an Azure subscription with Foundry/Azure OpenAI access available, and what is the spend cap?
- Which region/model deployment should be used?
- What do the hackathon rules require of the hosted demo (to confirm before choosing hosting)?

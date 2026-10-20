# Checklist: verify one real Microsoft Foundry / Azure OpenAI inference

Goal: record evidence of **one live chat-completions call** from MatchLens to a Microsoft Foundry (Azure OpenAI) model deployment. Mocked transports in the test suite do **not** count. Neither does a `demo` run that silently used the offline fallback.

Every step marked **[operator]** needs your Azure account, credentials or approval. Claude must stop there.

## 0. Before you start
- [ ] **[operator]** Approve the spend. A Standard (pay-as-you-go) deployment bills per token. The check sends one request of about 250 input and at most 200 output tokens, a fraction of a cent on small models, but it is not free.
- [ ] **[operator]** Set a budget alert on the subscription or resource group (Cost Management → Budgets), e.g. USD 5.
- [ ] Never paste the key into chat, issues, commits, screenshots or CI logs. MatchLens never prints it.

## 1. Create the model deployment [operator]
- [ ] In the Microsoft Foundry portal (ai.azure.com), create or select a project in a region that has quota for your chosen model.
- [ ] Deploy a **non-reasoning chat model**, e.g. `gpt-4o-mini` or `gpt-4.1-mini`, deployment type *Standard* or *Global Standard*.
  - MatchLens sends `temperature` and `max_tokens`, which reasoning models (o-series, gpt-5 family) reject with HTTP 400 `unsupported_parameter`.
- [ ] Open the deployment's details and copy from the **Target URI** and **Key** fields:
  - `AZURE_OPENAI_ENDPOINT`: the scheme and host only, e.g. `https://<resource>.openai.azure.com/` or `https://<resource>.cognitiveservices.azure.com/`;
  - `AZURE_OPENAI_DEPLOYMENT`: the deployment name (the path segment after `/deployments/`);
  - `AZURE_OPENAI_API_VERSION`: the `api-version` query value in the Target URI;
  - `AZURE_OPENAI_API_KEY`: Key 1.

## 2. Run the strict check locally [operator: environment holds the key]
```bash
pip install -e .
export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
export AZURE_OPENAI_DEPLOYMENT=<deployment-name>
export AZURE_OPENAI_API_VERSION=<api-version from Target URI>
read -rs AZURE_OPENAI_API_KEY && export AZURE_OPENAI_API_KEY   # avoids shell history
python -m matchlens azure-check | tee azure-check.json
```
`azure-check` makes **exactly one** request and **never falls back**. Exit codes: `0` live response received; `1` request failed (`error`, `error_code` such as `DeploymentNotFound`, `401`, `429`); `2` missing or invalid configuration.

## 3. Acceptance criteria (all must hold)
- [ ] Exit code `0` and `"ok": true`.
- [ ] `endpoint_host`, `deployment` and `api_version` match the Foundry deployment.
- [ ] `response_id` (e.g. `chatcmpl-…`) and `model` are present, and `usage` shows non-zero `prompt_tokens` and `completion_tokens`.
- [ ] `completion` is model text, and `completion_status` is `unverified_ai_wording`. The wording is presentation only and is not a verified fact.
- [ ] The Foundry portal (deployment → Metrics, or Monitoring) shows a request at `checked_at_utc` (allow a few minutes for metrics to appear).
- [ ] `azure-check.json` contains no key (`grep -c "$AZURE_OPENAI_API_KEY" azure-check.json` prints `0`).

## 4. See it in the product
- [ ] `python -m matchlens demo --seed 1 --limit 1` prints `Narrative provider: azure_openai` and wording labelled "AI-generated wording (Azure OpenAI) — not verified". There must be **no** `Note: azure_openai unavailable (...)` line; that line means the fallback was used.
- [ ] Optional: `python -m matchlens serve` and confirm cards show "AI wording · NOT VERIFIED" next to the ✓ verified facts.

## 5. Record the evidence
- [ ] Commit `azure-check.json` (it holds no secrets) as `docs/evidence/azure-check-<date>.json`, with a portal screenshot of the deployment metrics with the key area cropped.
- [ ] Update `docs/TASKS.md` (M3: "live Azure run verified") and README's Microsoft integration section with the date, model and region.

## 6. Afterwards
- [ ] **[operator]** Keep the deployment only while it is needed; delete it or rotate the key after recording evidence if it is not used for the demo.
- [ ] The public static demo does not call Azure and holds no key. Any future Azure-worded public build must be generated server-side or at build time, with the key kept in a secret store and the wording still labelled unverified.

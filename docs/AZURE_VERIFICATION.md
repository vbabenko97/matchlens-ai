# Checklist: verify one real Microsoft Foundry / Azure OpenAI inference

Goal: record evidence of **one live Responses API (v1) call** from MatchLens to a Microsoft Foundry (Azure OpenAI) model deployment. Mocked transports in the test suite do **not** count. Neither does a `demo` run that silently used the offline fallback.

Every step marked **[operator]** needs your Azure account, credentials or approval. Claude must stop there.

## 0. Before you start
- [ ] **[operator]** Approve the spend. A Standard (pay-as-you-go) deployment bills per token. The check sends one request of about 250 input and at most 200 output tokens (`max_output_tokens: 200`, `store: false`), a fraction of a cent on small models, but it is not free.
- [ ] **[operator]** Set a budget alert on the subscription or resource group (Cost Management → Budgets), e.g. USD 5.
- [ ] Never paste the key into chat, issues, commits, screenshots or CI logs. MatchLens never prints it.

## 1. Create the model deployment [operator]
- [ ] In the Microsoft Foundry portal (ai.azure.com), create or select a project in a region that has quota for your chosen model.
- [ ] Deploy a chat-capable model that supports the Responses API, e.g. `gpt-4.1-mini` (the verified choice) or `gpt-4o-mini`, deployment type *Standard* or *Global Standard*.
  - Reasoning models also accept the request, but they spend output tokens on reasoning, so a 200-token limit can end as `status: incomplete`, which the check reports as a failure.
- [ ] From the deployment's details copy:
  - `AZURE_OPENAI_ENDPOINT`: the resource base URL, e.g. `https://<resource>.services.ai.azure.com` or `https://<resource>.openai.azure.com`. The target URI `https://<resource>.services.ai.azure.com/openai/v1/responses` is also accepted and normalised;
  - `AZURE_OPENAI_DEPLOYMENT`: the deployment name, sent as the request's `model`;
  - `AZURE_OPENAI_API_KEY`: Key 1.
  - No `api-version` is needed: the v1 API has none, and `AZURE_OPENAI_API_VERSION` is ignored.

## 2. Run the strict check locally [operator: environment holds the key]
```bash
pip install -e .
export AZURE_OPENAI_ENDPOINT=https://<resource>.services.ai.azure.com
export AZURE_OPENAI_DEPLOYMENT=<deployment-name>
read -rs AZURE_OPENAI_API_KEY && export AZURE_OPENAI_API_KEY   # avoids shell history
test -n "$AZURE_OPENAI_API_KEY" || echo "key is empty"
set -o pipefail   # otherwise tee hides a non-zero exit code
python -m matchlens azure-check | tee azure-check.json
```
`azure-check` makes **exactly one** `POST {endpoint}/openai/v1/responses` and **never falls back**. Exit codes: `0` live, valid response received; `1` request failed (`error` such as `HTTP 401` with `error_code` such as `DeploymentNotFound`, `TimeoutError`) or the reply was not valid evidence (`error: InvalidResponse` with a `problems` list); `2` missing or invalid configuration.

## 3. Acceptance criteria (all must hold)
- [ ] Exit code `0` and `"ok": true`.
- [ ] `api` is `responses-v1`, `request_path` is `/openai/v1/responses`, and `endpoint_host` and `deployment` match the Foundry deployment.
- [ ] `response_status` is `completed`, `response_id` (e.g. `resp_…`) and `model` are present, and `usage` shows positive `input_tokens` and `output_tokens`. The check enforces all of these and exits `1` otherwise.
- [ ] `completion` is the model's `output_text`, and `completion_status` is `unverified_ai_wording`. The wording is presentation only and is not a verified fact.
- [ ] The Foundry portal (deployment → Metrics, or Monitoring) shows a request at `checked_at_utc` (allow a few minutes for metrics to appear).
- [ ] `azure-check.json` contains no key (`grep -F -c -- "$AZURE_OPENAI_API_KEY" azure-check.json` prints `0`; `-F` searches the key literally).

## 4. See it in the product
- [ ] `python -m matchlens demo --seed 1 --limit 1` prints `Narrative provider: azure_openai` and wording labelled "AI-generated wording (Azure OpenAI) — not verified". There must be **no** `Note: azure_openai unavailable (...)` line; that line means the fallback was used.
- [ ] Optional: `python -m matchlens serve` and confirm cards show "AI wording · NOT VERIFIED" next to the ✓ verified facts.

## 5. Record the evidence
- [ ] Commit `azure-check.json` (it holds no secrets) as `docs/evidence/azure-check-<date>.json`, with a portal screenshot of the deployment metrics with the key area cropped.
- [ ] Update `docs/TASKS.md` (M3: "live Azure run verified") and README's Microsoft integration section with the date, model and region.

## 5b. Record a live claim-agent run [operator: same shell as step 2]
```bash
set -o pipefail   # otherwise tee hides a non-zero exit code
python -m matchlens agent-review --seed 1 --out agent-trace.json | tee agent-review.txt
grep -F -c -- "$AZURE_OPENAI_API_KEY" agent-trace.json agent-review.txt   # both must print 0
```
Run it with the key already exported and non-empty. A rerun should use new file names.
In zsh, do not paste trailing `# comments`: interactive zsh passes them to the command as arguments.

**Progress and network errors.** The run prints `[agent] <card> attempt N: model call M` to stderr, with no prompt or response content.
- If no HTTP response arrives at all (connection, DNS, TLS, timeout), the call is retried twice, 5 s apart.
- A final failure prints the reason, e.g. `URLError (TimeoutError: timed out)`.
- Retries are recorded per call as `retries` (`cause`, `wait_s`).

**HTTP 429 (rate limit).** Each model call that gets a 429 waits as Azure asks (`retry-after-ms`/`retry-after`, at most 60 s, default 10 s) and retries, up to 4 times. The waits are recorded per call in `retries`.
- A request is kept small: metric IDs only from `list_metrics`, and `max_output_tokens: 400`. Azure counts the output limit against the per-minute quota up front.
- A full seed-1 run is still roughly 20–30K tokens within a few minutes.
- If it fails with `HTTP 429 (… still rate-limited …)`, raise the deployment's *Tokens per Minute Rate Limit* in the Foundry portal (Deployments → the deployment → Edit), within your subscription quota. On pay-per-token deployments the limit itself is not billed; usage is. Then rerun.
`agent-review` runs the bounded Foundry claim agent on the four analyst cards of seed 1. That is about 2–5 Responses API calls per card, plus one retry per card if the validator rejects a claim. It is **strict**, and a trace is written only when the run is acceptable evidence:
- Any failure exits `1` (`live agent run failed: <type>`) and writes nothing, so a recorded trace never holds fallback output.
- A completed run also exits `1` and writes nothing when `trace_problems` finds that the evidence falls short. It checks that every model call has a response ID and positive token usage, that the run made at least one real tool call, that no card holds offline fallback claims, and that each card has at least one model claim the validator accepted. The reasons are printed. An empty claim list therefore cannot pass.
- `export-site --agent-trace` applies the same check to a committed trace.

Acceptance:
- [ ] Exit code `0`. This already implies the `trace_problems` checks above.
- [ ] Every card's lines show `response resp_…` IDs, including tool calls (`list_metrics`, `get_metric`, `get_events`). No card shows `Note: foundry_agent unavailable`.
- [ ] `agent-trace.json` has `"schema": "matchlens.agent-trace/v1"`, `"store": false`, the deployment, and per-call `response_id`/`model`/`usage`.
- [ ] The goal card at 71' shows the injected test claim `home.goals:asof:ml1-01112 = 3` rejected ("claimed 3 but … = 2") and the correction published.
- [ ] Any claim the model got wrong shows its reason, then a retry (`attempt 2`). The injected wrong score is checked **after** the agent's attempts and is corrected by the validator, not by the model. Model self-correction can be claimed in the video only if this recording shows a real `attempt 2`.

Then commit `agent-trace.json` as `docs/evidence/agent-trace-<date>.json`. Build the public site from it with `python -m matchlens export-site --offline --agent-trace docs/evidence/agent-trace-<date>.json --out site`. That build re-checks the recorded claims, and the UI labels them "Recorded Microsoft Foundry agent run …, not a live call".

## 6. Afterwards
- [ ] **[operator]** Keep the deployment only while it is needed; delete it or rotate the key after recording evidence if it is not used for the demo.
- [ ] The public static demo does not call Azure and holds no key. Any future Azure-worded public build must be generated server-side or at build time, with the key kept in a secret store and the wording still labelled unverified.

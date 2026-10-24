# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [x] M2 (Oct 21-22): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [x] M3 (Oct 23-24; live Foundry Responses API v1 call verified by `azure-check` on 2026-10-24, evidence in `docs/evidence/azure-check-2026-10-24.json`): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [x] M4 (Oct 20, local; public hosting is M5): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26; **partial**: public demo live on GitHub Pages at https://vbabenko97.github.io/matchlens-ai/, smoke test green; accessibility code pass done (screen-reader check pending); rehearsal and video remaining): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-24): shared daily cap for live checks (review fix)
- **Review finding (GPT, accepted):** the daily limit and lock were per process, so restarts and parallel instances each got a fresh limit. The docs also called the budget a hard ceiling, but Azure budgets only alert, with delayed data, and TPM caps the rate, not the total.
- **Fix (`matchlens.quota`):**
  - `TableCounter` keeps one Azure Table Storage entity per UTC day, reached by a table SAS URL (`MATCHLENS_LIVE_COUNTER_URL`), stdlib HTTP only.
  - The reservation is a read plus an ETag-conditional `PUT` (412 -> retry) or an insert (409 -> retry). It is taken **before** the model call and kept if the call fails.
  - Any storage error, or a missing URL on the deployed API, means no live call (`counter_unavailable` / `not_configured`). Only `serve --live-api` may use an in-memory count.
  - The per-client limit is now documented as a courtesy limit (forgeable headers), not a cost control.
- **Python 3.11:** the GPT review cites Microsoft Learn listing `python:3.11` for SWA managed functions, so the 3.10 fallback note was removed. That page could not be opened from this sandbox (DNS).
- **Verified:**
  - `python -m pytest -q` -> 464 passed (incl. e2e on local Chromium); `ruff check .` and `ruff format --check .` clean;
  - new: fake Table service with ETag/If-Match semantics, 12 concurrent instances never exceed the cap, lost races retried, endless conflicts give up, storage 403/500/network/malformed fail closed without the SAS token in messages, reservation before the model call and kept on failure, restart continues the count.
  - Fake transports only: **not** evidence of real Table Storage or Foundry behaviour.
- **Next:**
  1. Operator: steps 1–7 in docs/DEPLOYMENT.md "Live Foundry check" (Storage table + SAS is new).
  2. A real Foundry reply with IDs, tokens and latency, and a forced `daily_limit` fallback.
  3. Raise TPM only after that.
  4. Then the match selector.

## Checkpoint (2026-10-24): live Foundry check endpoint (plan item 1, code only)
- **New `matchlens.live`:** `POST /api/live-check` with `{"card_id"}` only.
  - The server builds the Foundry request for that demo card and runs the existing bounded agent (`FoundryAgentProposer`, strict) and the validator.
  - The reply carries the review, its metrics, response IDs, usage and latency. The proposer label says it is a live call, with a UTC timestamp.
- **Fuses:**
  - analyst card IDs of the demo match only (404), JSON POST only (415, no CORS preflight granted), body ≤ 1 KB, no other fields (400);
  - 3 checks per client per 10 min (client = first of `X-Forwarded-For`, `X-Azure-ClientIP`, `X-Client-IP`, hashed; which one SWA sends is unverified until deploy); `MATCHLENS_LIVE_DAILY_LIMIT` (default 100) per UTC day, in memory per instance;
  - one run at a time ("busy", costs no quota);
  - 30 s deadline over calls and 429 waits.
  - Fallback reasons are machine-readable, and the page keeps the recorded run, saying "not a live call".
- **Hosts:**
  - `api/live_check/` is the Azure Functions adapter (v1 `function.json` model), configured with `apiRuntime: python:3.11`;
  - the SWA workflow builds with `--live-api` and ships `api/` (the package is copied in at deploy time);
  - `matchlens serve --live-api` serves the same route locally;
  - Pages builds have no button.
- **UI:** "Check this card live with Microsoft Foundry" on analyst claim checks. A live reply replaces the recorded check and focus moves to the result line; a fallback says why. The disclosure adds a line when the API exists.
- **Python 3.10 check** (in case SWA rejects 3.11): only `datetime.UTC` blocks it; the package runs on 3.10.20 with that shim (not applied).
- **Verified:**
  - `python -m pytest -q` -> 444 passed (incl. e2e on local Chromium; 25 new endpoint/adapter tests, 8 live-button e2e runs);
  - `ruff check .` and `ruff format --check .` clean.
  - All Azure traffic in tests is faked and is **not** evidence of a live call.
- **Blocked on operator** (docs/DEPLOYMENT.md, "Live Foundry check"): create the SWA Free app and token, set the app settings, raise the deployment's TPM limit, set a budget alert, run the SWA workflow, then one `curl` to verify.
- **Next:** match selector in the UI (plan item 4, no credentials needed).

## Checkpoint (2026-10-24): on-air graphic page (plan item 5)
- **New:** `broadcast.html` + `broadcast.js` + `broadcast.css`, exported with the site (hash-versioned like `index.html`).
  - Reads `data/overlays.json` (`matchlens.overlays/v1`) and draws the active cue as a lower-third on a transparent page, for an OBS/vMix browser source.
  - Shows the verified "why" fact only (narrative wording never goes on air); analyst cues add up to 3 source event IDs; a heuristic fact is labelled "Synthetic xG heuristic". A permanent "Synthetic match" bug stays in the corner.
  - Modes: `cue=<id>` (hold one cue), `rundown=1&hold=<s>` (loop every cue), default follows the match clock (`from`, `speed`); a cue never shows before its `start_s`. Unknown cue or bad file -> visible error line, nothing drawn.
  - Score hyphens are non-breaking on the graphic ("2-3" no longer wraps as "2-" / "3", found in the 1280×720 screenshot).
  - Replay UI: **▣ On-air graphic** link opens the rundown preview in the current mode.
- **Verified:** `python -m pytest -q` -> 414 passed (incl. 66 e2e on local Chromium; 12 new broadcast e2e across app-server and static sub-path); `ruff check .` and `ruff format --check .` clean. Screenshots checked at 1920×1080 (casual, analyst) and 1280×720 (green).
- **Not done:** axe-core run on `broadcast.html`; no real OBS test yet (operator: add the URL above as a browser source, 1920×1080).
- **Next:** plan item 1 code (card-ID-only live Foundry endpoint with recorded fallback, fake-transport tests); match selector in the UI.

## Checkpoint (2026-10-24): match story, every goal, causal selection, visible Foundry check
- **Merged and deployed:**
  - PR #11 squash-merged as `c5b585e`; CI was green on the PR head `707d9f5` (3.11, 3.13, e2e).
  - Pages redeployed from `main` @ `c5b585e` by [run 40103756908](https://github.com/vbabenko97/matchlens-ai/actions/runs/40103756908): `build`, `deploy` (14:43 UTC) and `smoke / smoke` all succeeded. Verified via the GitHub Actions API.
  - The smoke job ran `pytest -q -m e2e` against https://vbabenko97.github.io/matchlens-ai/: 27 passed, including the new story, comeback-shortcut and AI-check tests. So the public demo serves this build.
Driven by a judge's-eye review of the public build (seed 1). Casual cards repeated the scoreboard ("That puts Eastvale Athletic ahead, 2-3"), the same "move started with a tackle" line sat on all three goal cards, and two of the five goals (5' and 56') had no card at all. The real story went untold: Eastvale were 2-1 down at 71', scored at 77' and 81', and the 81' scorer had a big chance saved at 38'.
- **Bug fixed: the stats panel lagged the scoreboard by up to 59 s.** At 76:46 the score read 2-2 and "Last event" was the goal, but shots/xG still showed 14–9 / 1.31; they updated per minute. The bundle now has `stat_steps` (one per shot), and the panel shows the last step at or before the current event. A new browser test fails on the old code (`'0 – 0' == '0 – 1'`).
- **Causal selection.** Every goal, every missed big chance and the final whistle get a card; press wins get none. This replaces "top 4 ranked over the whole match", which used later events. Seed 1 now has 7 cards per mode. `test_selection_never_depends_on_later_events` cuts each match (seeds 1-20) right after each moment and gets the same choices.
- **Why it matters (`matchlens.story`).** Each card leads with structured, event-cited story lines:
  - goals: opener, equaliser, comeback, back in front, extends lead, pulls one back; plus redemption and brace;
  - missed chances: what a goal would have meant;
  - full time: comeback or result, the winning goal, lead changes, and shots vs chance quality (labelled synthetic xG).
  - `check_line` re-renders each line from its cited events alone and checks the kind's condition. Lines that need the whole score history cite every goal, and each cited goal must add exactly one. A failing line is dropped and reported in `suppressed`.
  - Seed 1, 81': "Comeback! Eastvale Athletic take the lead" / "Eastvale Athletic were 2-1 down in minute 71. Goals in minutes 77 and 81 turned it around." / "Ugo Vale saw a big chance saved in minute 38; this time it went in."
  - Full time: "Full time: Eastvale Athletic come from behind to win 2-3", "The lead changed hands twice", "Northbridge Rovers had more shots (14–11), but Eastvale Athletic had the better chances (synthetic xG, a heuristic: 3.04 vs 2.05)."
- **Cards and UI:**
  - story headline; "why" lines first and emphasised;
  - analyst event chain folded in a `<details>`;
  - one label line per card ("✓ checked against match events" + "Offline template · not an Azure response");
  - the lower third shows the "why" line, and on phones it sits under the pitch instead of covering it;
  - an FT marker.
- **Shortcuts (`highlights`, hindsight chapter marks that never change a card):**
  - **Watch the comeback (77'–81')** plays from just before the 77' move; at 60× the 77', 81' and full-time cards arrive in about 15 s.
  - **See the AI get fact-checked (77')** opens Analyst at the 77' card and focuses its claim check. The check is grouped by attempt: "Attempt 1: 3 of 4 claims verified, 1 rejected and sent back · Attempt 2: 4 of 4 claims verified"; response IDs sit in a disclosure.
- **Recorded Foundry run:**
  - It still replays exactly on the 4 cards it covers (38', 71', 77', 81'), with the injected test claim on 71' as recorded.
  - The 5', 56' and full-time cards are reviewed by the offline proposer, and their notes say they are not part of the recorded run.
  - A fresh live recording would cover all 7 cards: `agent-review --seed 1 --out …` now defaults to every card. Operator credentials are needed; this is optional.
- **Fresh matches (seeds 100-129, not tuned for the demo):** every goal has a card in both modes, every card has a "why" line, nothing is suppressed, and no card cites an event after its publication. 2-11 cards per match; the offline bundle builds in a median 0.11 s (max 0.15 s) at no API cost. Cost of the recorded agent run: 19 Responses API calls, ≈ 40K input / 1.8K output tokens for 4 cards.
- **Verified:**
  - `python -m pytest -q` -> 402 passed (incl. 54 e2e on local Chromium);
  - `ruff check .` and `ruff format --check .` clean;
  - axe-core 4.10.2 -> 0 WCAG violations in 5 states (see `docs/ACCESSIBILITY.md`).
- **Next:**
  1. Three-person 30-second test on the public URL: "what happened in the match, and how did MatchLens help?"
  2. Video, 110-115 s: comeback shortcut → 81' card → Casual/Analyst switch → the real Foundry rejection and retry at 77' → Overlay JSON export.
  3. Optional, needs operator Azure credentials: re-record the agent trace for all 7 cards into a **new** file, and keep `agent-trace-2026-10-24.json` (it holds the real self-correction). Measure agent latency then: the trace has no call timestamps, and 0.1 s is offline build time only.

## Checkpoint (2026-10-24): live Foundry claim-agent run recorded and wired in
- **Live run (operator, 11:10 UTC):** `agent-review --seed 1` exited 0 on the fifth attempt. Committed as `docs/evidence/agent-trace-2026-10-24.json`.
  - 4 analyst cards; 19 Responses API calls, each with its own response ID and positive usage (≈ 40K input / 1.8K output tokens); 27 real tool calls; 13 HTTP 429 waits absorbed; no fallback; `trace_problems` passes.
  - **Model self-correction (77' card):** the validator rejected `away.pressures:asof:ml1-01206 = 131` because it cited the goal-move events, not pressure events. The retry cited real pressure events and was verified.
  - On the 81' card the model misspelled a metric ID, got "unknown metric" from the tool, and fixed it with another lookup.
  - **Injected test claim (71'):** `home.goals = 3` rejected ("claimed 3 but … = 2"), and "Goals: 2 (as of 71')" published.
  - Checks: no key-like strings or auth headers in the file; replaying the trace reproduces every verdict exactly (regression test).
- **Earlier recording attempts and the fixes they forced** (all in PR #10):
  1. HTTP 429 → wait per `Retry-After`, leaner requests.
  2. `URLError` → bounded network retries, progress output.
  3. Retry spent every call on lookups → the last call runs with `tool_choice: "none"`.
  4. Answer cut off at the output cap → resend once with a larger cap; print reasons.
- **Wired in:**
  - The Pages and SWA workflows build with `--agent-trace docs/evidence/agent-trace-2026-10-24.json`.
  - The page disclosure says claim checks replay a recorded run and were re-checked at build time.
  - The e2e tests use the same bundle as production.
  - The UI and CLI no longer call a superseded attempt-1 correction "published"; it reads "sent back to the agent". The raw CLI text of the run used the old wording, so only the JSON trace is committed as evidence.
- **Verified:** `python -m pytest -q` -> 323 passed (incl. 44 e2e on local Chromium); `ruff check .` and `ruff format --check .` clean.
- **Next (approval needed):**
  1. Merge PR #10 and redeploy Pages in one go; until then the daily public smoke test would fail on the new e2e checks.
  2. Confirm the public smoke job.
  3. Video: card → Casual/Analyst → evidence → claim check (77' self-correction, 71' injected rejection) → recorded response IDs.

## Checkpoint (2026-10-24): cards no longer look ahead
- **Merged and deployed:**
  - PR #8 squash-merged as `3dd2b45`; CI was green on the PR head (3.11, 3.13, e2e).
  - Pages redeployed from `main` @ `3dd2b45` by [run 40085133359](https://github.com/vbabenko97/matchlens-ai/actions/runs/40085133359): `build`, `deploy` (10:15 UTC) and `smoke / smoke` all succeeded. Verified via the GitHub Actions API.
  - The smoke job ran `pytest -q -m e2e` from `3dd2b45` against the public URL. Its new tests read `publish_clock_s` from the served `bundle.json`, so the public demo serves the causal bundle.
- **Bug (reproduced on `main` @ `c838aad`, seed 1):** 5 of the 8 public-demo cues cited events that had not happened yet.
  - The analyst 71' goal card (shown at 70:13) said "Shots: 4" for 60'–75'. Only 3 had been taken; the 4th, `ml1-01166`, is at 74:05.8 (minute 75). Window metrics were computed over the full window.
  - Press-win cards appeared at the tackle, but the shot they cite comes later. For `press_win:ml1-00126` the shot `ml1-00130` comes 14.0 s later.
  - Missed-chance cards cited the save that comes after the shot.
- **Fix:**
  - `KeyMoment.known_clock_s` is the time of the last evidence event. A card's new `publish_clock_s` is set to it, and the card is shown from then on (UI, overlay `start_s`; `end_s` = publish + 12 s). Overlay cues add `anchor_s`.
  - Analyst window metrics use `compute_metrics(..., as_of_event_id=last evidence)`. IDs look like `home.shots@3600-4500:asof:ml1-01112` and the text adds "as of 71'".
  - `future_sources()` re-checks every cited event, evidence event and metric source against publication. Any card that fails is suppressed with the offending IDs.
- **Tests:**
  - seed-1 regression (3 shots as of 70:13, not 4; press win published at its shot);
  - causality invariant over seeds 1–20 with all detected moments, checked against metrics recomputed on the match truncated at publication;
  - a guard test that reintroduces the old full-window metrics;
  - a bundle-level check, as the browser sees it;
  - an e2e check that a card is hidden between its anchor and its publication.
  - The bundle check and the new e2e test fail on the pre-fix code. The hand-checked expectation that encoded the leak ("Shots: 3" at 1') is corrected to 2.
- **Scope:** sources and timing are causal, but the replay is precomputed and the top-4 moments are chosen over the whole match. State this precisely in the submission.
- **Verified:** `python -m pytest -q` -> 287 passed (incl. 42 e2e on local Chromium); `ruff check .` and `ruff format --check .` clean.
- **Next:**
  1. Foundry scenario: the model proposes a structured claim, `verify_claim` rejects a wrong value, and the corrected card is published with its sources.
  2. Screen-reader pass, demo rehearsal and video (110–115 s).

## Checkpoint (2026-10-24): cache-busting deployed; zoom check done
- **Pages redeployed** from `main` @ `0aa672c` (PR #6 merge), [run 40082219742](https://github.com/vbabenko97/matchlens-ai/actions/runs/40082219742): build, deploy (09:30 UTC) and `smoke / smoke` all succeeded. Verified via the GitHub Actions API.
- **After the redeploy**, the operator's screenshots (normal window and incognito) showed the current styles: screen-reader text hidden, styled `h1`, 24px markers, and the keyboard hint on its own line. The stale-CSS issue is resolved.
- **Operator zoom check, 200%/400%:** reflow works, there is no horizontal scroll, and all regions are reachable. Two defects were found and fixed in this checkpoint:
  1. "Last event" was truncated with an ellipsis; it now wraps.
  2. A redundant goal dot overlapped a stacked "G" marker; it is no longer drawn.
  - New regression tests at 320, 390 and 500px fail on the old CSS and JS.
- **Verified:**
  - `python -m pytest -q` -> 258 passed (incl. e2e on local Chromium);
  - `ruff check .` and `ruff format --check .` clean.
- **Remaining for accessibility:** the operator screen-reader pass (VoiceOver script in `docs/ACCESSIBILITY.md`).
- **Next:**
  1. After merge, redeploy Pages and confirm the smoke job.
  2. Screen-reader pass.
  3. Demo rehearsal and video script.

## Checkpoint (2026-10-24): accessibility build deployed; cache-busting
- **Pages redeployed** from `main` @ `2731814` (PR #5 merge) by `Deploy demo (GitHub Pages, fallback)` [run 40081567480](https://github.com/vbabenko97/matchlens-ai/actions/runs/40081567480), verified via the GitHub Actions API:
  - jobs `build`, `deploy` (09:21 UTC) and `smoke / smoke` all succeeded;
  - the smoke job ran `pytest -q -m e2e` against https://vbabenko97.github.io/matchlens-ai/, including the new accessibility tests (one `h1`, ≥ 24px targets, …), so the served site is the accessibility build.
- **Stale-cache issue (operator screenshots):** right after the deploy, a browser showed the new `index.html` with the previous cached `style.css`. Symptoms: visible screen-reader text, an unstyled `h1`, small markers. GitHub Pages caches assets for 10 minutes.
- **Fix in this checkpoint:** `site_files()` references `style.css?v=<sha256[:12]>` and `app.js?v=<sha256[:12]>`, so any asset change gets a new URL. `bundle.json` is fetched with `cache: "no-cache"`. If `index.html` stops referencing either asset in the expected form, the export fails loudly.
- **Verified:**
  - `python -m pytest -q` -> 254 passed (incl. e2e on local Chromium against the versioned static export);
  - `ruff check .` and `ruff format --check .` clean.
- **Next:**
  1. After merge, redeploy Pages and confirm the smoke job.
  2. Operator screen-reader pass and 400% zoom check (scroll through the controls, timeline and cards) per `docs/ACCESSIBILITY.md`.
  3. Then demo rehearsal and video.

## Checkpoint (2026-10-24): accessibility pass (code)
- Audit and fixes are in `docs/ACCESSIBILITY.md` (WCAG 2.2 AA target, 12 fixes):
  - one polite status announcer instead of a live clock and live card list;
  - focus kept after chip activation;
  - radio-group keyboard support;
  - shortcuts scoped away from native controls;
  - `h1` and text alternatives;
  - slider `aria-valuetext`;
  - 24px targets with marker stacking;
  - 3:1 control borders;
  - focusable card panel;
  - 320px reflow.
- **Verified:**
  - `python -m pytest -q` -> 252 passed, including 34 e2e runs against local Chromium. The new accessibility tests failed on the pre-fix UI.
  - axe-core 4.10.2 -> 0 violations at 1400×900 (start; Analyst at full time) and 390×844.
  - `ruff check .`, `ruff format --check .` clean.
- **Remaining for M5 accessibility:** operator screen-reader pass (VoiceOver/NVDA script in `docs/ACCESSIBILITY.md`) and a 200%/400% zoom check. Then redeploy Pages and confirm the public smoke run.
- **Next:** demo rehearsal, video, then M6 audit and submission.
- **Next command:** after merge, *Actions → Deploy demo (GitHub Pages, fallback) → Run workflow*, then `MATCHLENS_BASE_URL=https://vbabenko97.github.io/matchlens-ai/ python -m pytest -q -m e2e`.

## Checkpoint (2026-10-24): public demo status recorded
- **Hosting done (GitHub Pages; no Azure Static Web App will be created):**
  - Deployed by the `Deploy demo (GitHub Pages, fallback)` workflow (run 39476401211, success, 2026-10-20, `main` @ `e7dc926`).
  - URL: https://vbabenko97.github.io/matchlens-ai/. `PUBLIC_DEMO_URL` is set.
- **Public smoke test, verified via the GitHub Actions API:** `Public demo smoke test` runs 1 (manual, 2026-10-20) to 4 (scheduled, 2026-10-23) all concluded `success`, and the `pytest -q -m e2e` step ran in each. The URL could not be fetched from the Claude Code sandbox (egress blocked), so the URL check rests on these CI runs.
- **Manual public replay test:** passed (operator-reported).
- **M5 remaining, in priority order:**
  1. Accessibility pass: keyboard-only use, focus order and visibility, contrast, screen-reader names for controls and cards, `prefers-reduced-motion`.
  2. Demo rehearsal on the public URL, plus a local `azure-check`/`demo` segment for the Microsoft integration.
  3. A public demo video under 2 minutes.
- **M6:** final originality/security/requirements audit, then the Innovation Studio submission (operator approval required) before 2026-10-27 23:59 PT.
- **Next command:** `MATCHLENS_BASE_URL=https://vbabenko97.github.io/matchlens-ai/ python -m pytest -q -m e2e` (after any redeploy).

## Checkpoint (2026-10-24): live MatchLens `azure-check` verified
- The operator ran `python -m matchlens azure-check` locally from the PR #4 branch (`4e5a291`), entering the key with `read -s`. Result: `ok: true`, HTTP 200, `api: responses-v1`, `response_status: completed`, response ID `resp_04cb…084b`, model `gpt-4.1-mini`, 137 input / 41 output tokens, `store: false`, latency 1079 ms, at 2026-10-24T08:51:37Z.
- `grep -cF "$AZURE_OPENAI_API_KEY"` on the output printed `0`. The redacted report is committed as `docs/evidence/azure-check-2026-10-24.json`.
- The completion stays `unverified_ai_wording`. It restates the three facts and adds an unsupported flourish ("the momentum just swung"). This is why AI wording is never shown as verified.
- Still open (optional): a Foundry portal metrics screenshot for the request at that timestamp.

## Checkpoint (2026-10-24): Responses API v1 migration
- **Operator-reported:** a live Microsoft Foundry Responses API call succeeded (`gpt-4.1-mini` deployment, `status: completed`, response ID, 17 input / 27 output tokens). It was made outside MatchLens and no live request ran in Claude Code.
- **Changed:**
  - `AzureOpenAIProvider` now calls `POST {endpoint}/openai/v1/responses` with `model` (= deployment), `input`, `max_output_tokens: 200` and `store: false`. The legacy chat-completions path, `temperature` and `api-version` are gone.
  - `AZURE_OPENAI_API_VERSION` is no longer required. The endpoint may be given as the base URL, `.../openai/v1` or the full `.../openai/v1/responses`.
  - `extract_output_text` joins the `output_text` parts of `message` items in `output`. Non-`completed` status, missing or ill-typed `output`/`content`/`text`, refusal-only replies and the legacy chat shape raise `ResponseFormatError`, and cards fall back to the labelled offline template.
  - `azure-check` requires `status == completed`, a response ID, a model and positive `input_tokens`/`output_tokens`. Otherwise it exits 1 with `error: InvalidResponse` and a `problems` list. The success report adds `api`, `request_path`, `response_status`, `usage` and `store: false`.
- **Verified:** `python -m pytest -q` -> 216 passed, 1 skipped (e2e module skipped here because Playwright is not installed; CI runs it). `ruff check .` and `ruff format --check .` clean. All Azure tests use mocked transports and are **not** integration evidence.

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

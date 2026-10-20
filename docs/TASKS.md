# MatchLens AI — milestone checklist

Update this file after verified work. A task is done only when the acceptance checks pass; no invented completion claims.

- [x] M0 (Oct 20): Decide minimal Python stack, project layout, install/format/test commands. Initialize tests and CI. Record deployment plan and available Azure/Microsoft credentials without copying credentials to git.
- [x] M1 (Oct 20-21): New deterministic synthetic generator and schema validator; reproducibility and invariant tests; checked-in tiny demo fixture.
- [x] M2 (Oct 21-22): Metrics and 2-4 robust key-moment detectors; unit tests with hand-checked fixtures.
- [ ] M3 (Oct 23-24; code done, live Azure run blocked on access): Evidence-grounded cards with claim checks, Microsoft Foundry/Azure path, clearly labeled offline fallback; two distinct audience modes.
- [ ] M4 (Oct 25): Browser UI, match replay, overlays, downloadable overlay JSON, integration tests.
- [ ] M5 (Oct 26): Public deployment, accessibility check, real demo rehearsal, README, short video recorded and published.
- [ ] M6 (Oct 27): Final originality/security/requirements audit, public repository, demo link and video link; complete actual Innovation Studio submission before deadline.

## Latest checkpoint (2026-10-20)
- **PR #1 merged** (https://github.com/vbabenko97/matchlens-ai/pull/1, merge commit `6d8e589`) after a final review. The review fixed one robustness bug: malformed match/team/player fields used to crash `validate` with a traceback; they are now reported as format errors. It also made the related-event check linear. CI green on 3.11 and 3.13 at `47f5e76`.
- **M3 (code) verified locally:**
  - `matchlens.cards`: cards only for moments that re-detect identically, otherwise suppressed with a reason. Every fact is rendered from verified structured data (claims via `verify_claim`, or moment fields) and carries event/metric IDs.
  - **Casual Fan vs Analyst** differ in content. Casual has the score situation and how the move began, with no statistics or IDs in the text. Analyst has the evidence event chain, possession structure, the labelled xG heuristic and window metrics, with IDs. Tests assert disjoint fact sets.
  - `matchlens.narrative`: `NarrativeProvider` protocol, `AzureOpenAIProvider` (env config, HTTPS only, stdlib HTTP, injectable transport, redirects refused so the key is never re-sent) and `OfflineTemplateProvider`. Any failure falls back to the labelled offline template; AI wording is always `unverified_ai_wording`, and unsupported numbers are flagged. Model output never alters facts.
  - CLI: `python -m matchlens cards <file> --mode casual|analyst [--json] [--offline]` and `python -m matchlens demo --seed 42 [--offline]`.
- **Verified results:** `python -m pytest -q` -> 135 passed on Python 3.13 and 3.11; `ruff check .` and `ruff format --check .` clean. Azure provider tested only with a fake transport (no network calls, no real credentials).
- **Remaining for M3:** a live Azure OpenAI / Foundry run (see `docs/DEPLOYMENT.md` "To verify the Azure path"). Blocked on operator Azure access, deployment and spend cap.
- **Next smallest task:** after operator review of M3, M4 (browser UI, replay, overlay JSON). Not started, per instruction.
- **Last verified command:** `python -m pytest -q && ruff check . && ruff format --check .`
- **Blockers / decisions:** Azure access, region, model deployment and spend cap unconfirmed; nothing provisioned. Hosting deferred until Azure availability, costs and hackathon requirements are verified.

# MatchLens data model (schema v1)

Source of truth: `src/matchlens/schema.py` (conventions) and `src/matchlens/validate.py` (enforced invariants).

## File format
JSONL. Line 1 is the `{"record": "match", ...}` metadata record (match ID, seed, period length, both fictional squads). Every following line is one `{"record": "event", ...}`. Output contains no wall-clock timestamps, so the same seed and configuration reproduce the file byte-for-byte (verified on Python 3.11 and 3.13).

## Event fields
| field | meaning |
|---|---|
| `event_id`, `seq` | unique ID `<match_id>-<seq:05d>`; `seq` is 1-based and gap-free |
| `period`, `clock_s` | period 1 or 2; elapsed match seconds (period 2 starts at `period_length_s`). No stoppage time, so the clock is globally nondecreasing |
| `type`, `subtype`, `outcome` | `pass` (`open_play`, `kick_off`, `goal_kick`, `throw_in`; `complete`/`intercepted`/`out`), `shot` (`goal`/`saved`/`off_target`/`blocked`), `pressure`, `tackle`, `interception`, `recovery`, `clearance`, `period_start`, `period_end` |
| `team`, `player_id` | `home`/`away` and a squad player ID (null for period markers) |
| `possession_id` | possession chain ID (see below) |
| `home_score`, `away_score` | score **after** the event |
| `start`, `end` | `[x, y]` in metres |
| `xg_heuristic` | shots only; synthetic, **uncalibrated** heuristic from distance and goal-mouth angle |
| `related_event_id` | earlier event this one responds to (e.g. interception -> failed pass, goal kick -> missed shot) |

## Geometry
Absolute pitch frame, 105 x 68 m, origin at a corner flag; `x` runs goal-to-goal, `y` touchline-to-touchline. Home always attacks toward `x = 105`, away toward `x = 0` (the half-time side switch is not modelled). Coordinates are rounded to 0.1 m. Consecutive ball events in open play must connect: `next.start` lies within **1.0 m** of `prev.end`.

## Possession
A possession is a maximal run of ball events by one team. A new possession (ID = previous max + 1) starts when the team on the ball changes or at a restart (kick-off, goal kick, throw-in). `pressure` is an off-ball event: it carries the *current* possession ID but the pressing team.

## Enforced invariants (`matchlens validate`)
Unique IDs and gap-free `seq`; clock nondecreasing and inside its period; `period_start`/`period_end` bracket exactly periods 1 and 2; known types/outcomes/subtypes; players belong to their team; locations on the pitch; score changes only on goals, by one, for the scorer; a goal is followed by the conceding team's kick-off; `out` -> opposing throw-in, `off_target` -> opposing goal kick; possession IDs and ownership as above; ball continuity; `xg_heuristic` present exactly on shots and in `[0, 1]`.

## Known simplifications
No stoppage time, corners, fouls, cards, substitutions, offside or pass recipients. The generator models event *structure*, not realistic football dynamics; its rates are tuned only so a demo match looks plausible (~24 shots, ~3.6 goals per match averaged over seeds 1-60).

## Metrics (`matchlens.metrics`, M2)
`compute_metrics(match, window_s=900)` returns metrics keyed by stable IDs: `<team>.<name>` for full-match totals and `<team>.<name>@<start>-<end>` for clock windows `[start, end)` (the final window includes the end whistle). Each metric carries `value`, `unit`, `kind` (`fact` or `heuristic`), a plain-language `definition`, and the `source_event_ids` it was computed from (ratios also list `numerator_event_ids`).

| name | definition |
|---|---|
| `passes`, `passes_completed`, `pass_completion` | team pass events (incl. restarts); outcome `complete`; ratio, `null` when no passes |
| `shots`, `shots_on_target`, `goals` | team shots; outcome `goal`/`saved`; outcome `goal` |
| `pressures` | team pressure events |
| `turnovers` | team passes `intercepted`/`out` plus opponent tackles |
| `possessions` | possession chains owned (sources = first event of each chain) |
| `xg_heuristic` | **heuristic**: sum of the synthetic shot heuristic |

Window metrics: `passes`, `shots`, `pressures`, `turnovers`, `xg_heuristic`.

## Claim verification (`matchlens.claims`)
A `Claim` is structured: `metric_id`, `value`, `cited_event_ids`. It deliberately has **no free-text field**. `verify_claim(claim, metrics, match)` returns `verified` only if the claim cites a metric and/or events; all cited events exist; a cited metric exists, is defined and matches the stated value (tolerance 5e-4); cited events are sources of that metric; and a value is never stated without a metric. Otherwise it returns `unsupported` with reasons and no text.

Wording is rendered **after** verification from fixed templates using only verified data (`ClaimCheck.text`), and always ends with `[evidence: ...]`. Heuristic metrics are always rendered with "synthetic heuristic, not empirically calibrated".

**Limitation (by design):** the meaning of free prose cannot be machine-verified. `assess_prose(text)` therefore always returns `unverified`, for true and false text alike, including prose that imitates an evidence citation. UIs must show only template-rendered statements as facts; any LLM wording (M3) is presentation only and must be labelled unverified, never shown as a verified claim.

## Key moments (`matchlens.moments`)
Deterministic detectors; each `KeyMoment` has an ID `<kind>:<anchor_event_id>`, anchor and start clock, evidence `event_ids` in match order, and structured `details`.

| kind | rule | evidence |
|---|---|---|
| `goal` | every shot with outcome `goal` | the scoring possession chain up to the shot (ball events + pressures) |
| `big_chance_missed` | non-goal shot with xG heuristic >= 0.2 | the shot + the event answering it (save, goal kick, clearance) |
| `press_win` | tackle/interception by a team that pressed the lost possession <= 8 s earlier, and whose won possession shoots <= 20 s later | pressure ... win, plus the shot |

`select_key_moments(moments, limit=4)` ranks goals, then missed big chances, then press wins (higher shot xG heuristic first, then earlier) and returns them in match order; if goals would fill every slot (limit >= 2), the best non-goal moment takes the last slot. `describe_moment` renders template text from structured fields only, ending with the evidence IDs. Over seeds 1-60: ~3.6 goals, ~1.7 missed big chances and ~6.5 press wins per match.

## Insight cards (`matchlens.cards`, M3)
`build_cards(match, mode, provider=None, limit=4)` builds one card per selected key moment. A moment must re-detect identically (`verify_moment`) or its card is suppressed with a reason. Each card has `title`, `start_clock_s` / `anchor_clock_s` / `end_clock_s` (anchor + 12 s, for overlays), `evidence_event_ids`, verified `facts` and a `narrative`.

Facts are the only factual content. Each is rendered from verified structured data and carries `event_ids` and/or `metric_ids`:

| | Casual Fan | Analyst |
|---|---|---|
| content | who and when, score situation (ahead / level / extends lead / pulls one back), how the move began, outcome of a chance | every evidence event, possession and build-up structure, shot quality from the xG heuristic, metrics for the 15-min window around the moment |
| statistics | none (no xG, no counts) | labelled xG heuristic and window metrics |
| evidence in text | no (kept as structured IDs on each fact) | yes (`[evidence: ...]` on every fact) |

## Narrative providers (`matchlens.narrative`)
`NarrativeProvider.render(NarrativeRequest) -> str` only rewords the verified fact texts. `provider_from_env()` returns `AzureOpenAIProvider` (Azure OpenAI / Microsoft Foundry chat completions, `POST {endpoint}/openai/deployments/{deployment}/chat/completions?api-version=...`, `api-key` header, HTTPS required, stdlib HTTP only) when all four `AZURE_OPENAI_*` variables are set, else `OfflineTemplateProvider`. `narrate()` labels the result:
- `deterministic_template` + "Offline fallback: deterministic template wording — not an Azure response." (offline, or any provider error; `fallback_reason` names the exception type only, never secrets);
- `unverified_ai_wording` + "AI-generated wording (Azure OpenAI) — not verified" for model output. Numbers in model output that do not appear in the facts are listed in `warnings` (a guard, not verification).

No narrative is ever `verified`; model output never changes a card's facts.

## Overlay cues (`matchlens.overlays`, M4) — schema `matchlens.overlays/v1`
Top level: `match_id`, `seed`, `synthetic: true`, `time_unit: "match_seconds"`, `teams`, `final_score`, `notes`, `cues`. Each cue is one card in one mode: `cue_id`, `mode`, `kind`, `team`, `start_s` (anchor event), `end_s` (anchor + 12 s), `lead_in_s` (first evidence event), `title`, `facts` (verified text + `event_ids`/`metric_ids` + `kind`), `evidence_event_ids`, and `narrative` with its `status` and `label`. Consumers must show `facts` as facts and `narrative` only as labelled wording.

## Replay bundle and UI (`matchlens.replay`, `matchlens.server`, `src/matchlens/web/`)
`build_bundle(match, provider)` precomputes everything the browser needs (schema `matchlens.replay/v1`): all events, cards for both modes, suppressed moments, the metrics cited by cards (with sources), per-minute cumulative goals/shots/xG heuristic (`minute_series`, checked against `compute_metrics` totals), and the overlay document. The browser only displays the bundle and never decides what is verified. The UI is static HTML/CSS/vanilla JS with no dependencies and no build step. It sets text only through `textContent`; `serve` adds a strict CSP (`script-src 'self'`, no inline or eval) and serves a fixed in-memory file set (no filesystem paths, so no traversal).

Narrative display rules in the UI: ✓ marks only verified facts. Offline wording carries "Offline template · not an Azure response". AI wording carries "AI wording · NOT VERIFIED" plus its label; `unsupported_numbers` warnings are shown as "(heuristic check only)", because that check cannot establish the semantic truth of model prose.

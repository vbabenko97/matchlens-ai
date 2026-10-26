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
| `related_event_id` | earlier event this one responds to (for example interception -> failed pass, goal kick -> missed shot) |

## Geometry
Absolute pitch frame, 105 x 68 m, origin at a corner flag; `x` runs goal-to-goal, `y` touchline-to-touchline. Home always attacks toward `x = 105`, away toward `x = 0` (the half-time side switch is not modelled). Coordinates are rounded to 0.1 m. Consecutive ball events in open play must connect: `next.start` lies within **1.0 m** of `prev.end`.

## Possession
A possession is a maximal run of ball events by one team. A new possession (ID = previous max + 1) starts when the team on the ball changes or at a restart (kick-off, goal kick, throw-in). `pressure` is an off-ball event: it carries the *current* possession ID but the pressing team.

## Enforced invariants (`matchlens validate`)
Unique IDs and gap-free `seq`; clock nondecreasing and inside its period; `period_start`/`period_end` bracket exactly periods 1 and 2; known types/outcomes/subtypes; players belong to their team; locations on the pitch; score changes only on goals, by one, for the scorer; a goal is followed by the conceding team's kick-off; `out` -> opposing throw-in, `off_target` -> opposing goal kick; possession IDs and ownership as above; ball continuity; `xg_heuristic` present exactly on shots and in `[0, 1]`.

## Known simplifications
No stoppage time, corners, fouls, cards, substitutions, offside or pass recipients. The generator produces football-realistic event *structure* with simplified dynamics; its rates are tuned only so a demo match looks plausible (~24 shots, ~3.6 goals per match averaged over seeds 1-60).

## Metrics (`matchlens.metrics`)
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

As-of metrics: `compute_metrics(match, window_s, as_of_event_id=X)` counts only the events up to and including `X` in match order, that is, the metrics known when `X` happened. Every ID gets the suffix `:asof:X` (for example `home.shots@3600-4500:asof:ml1-01112`), the record carries `as_of_event_id`, and windows that start after `X` are omitted. Rendered text adds "as of N'" (for example "Shots: 3 (between 60' and 75', as of 71')"). Each as-of metric equals the plain metric computed on the match truncated at `X` (tested).

## Claim verification (`matchlens.claims`)
A `Claim` is structured: `metric_id`, `value`, `cited_event_ids`. It deliberately has **no free-text field**. `verify_claim(claim, metrics, match)` returns `verified` only if the claim cites a metric and/or events; all cited events exist; a cited metric exists, is defined and matches the stated value (tolerance 5e-4); cited events are sources of that metric; and a value is never stated without a metric. Otherwise it returns `unsupported` with reasons and no text.

Wording is rendered **after** verification from fixed templates using only verified data (`ClaimCheck.text`), and always ends with `[evidence: ...]`. Heuristic metrics are always rendered with "synthetic heuristic, not empirically calibrated".

**Limitation (by design):** the meaning of free prose cannot be machine-verified. `assess_prose(text)` therefore always returns `unverified`, for true and false text alike, including prose that imitates an evidence citation. UIs must show only template-rendered statements as facts; any LLM wording is presentation only and must be labelled unverified, never shown as a verified claim.

## Key moments (`matchlens.moments`)
Deterministic detectors; each `KeyMoment` has an ID `<kind>:<anchor_event_id>`, anchor clock (`clock_s`), start clock (first evidence event), `known_clock_s` (last evidence event), evidence `event_ids` in match order, and structured `details`. Some detectors need events after the anchor (a press win's shot, the save after a missed chance), so a moment is fully known only at `known_clock_s`.

| kind | rule | evidence |
|---|---|---|
| `goal` | every shot with outcome `goal` | the scoring possession chain up to the shot (ball events + pressures) |
| `big_chance_missed` | non-goal shot with xG heuristic >= 0.2 | the shot + the event answering it (save, goal kick, clearance) |
| `press_win` | tackle/interception by a team that pressed the lost possession <= 8 s earlier, and whose won possession shoots <= 20 s later | pressure ... win, plus the shot |
| `full_time` | the period-2 `period_end` | every goal, then the whistle; `team` is the winner or `draw`; its lead-in is the whistle itself |

`select_key_moments(moments, limit=None)` is **causal**: every goal, every missed big chance and the final whistle get a card (`CARD_KINDS`), in match order; press wins are detected but get no card. Each moment is chosen on its own kind, never ranked against later moments, so the choice never depends on later events (`test_selection_never_depends_on_later_events`, seeds 1-20). `limit` keeps the first `limit` moments. `describe_moment` renders template text from structured fields only, ending with the evidence IDs. Over seeds 1-60: ~3.6 goals, ~1.7 missed big chances and ~6.5 press wins per match.

## Match story (`matchlens.story`)
Why a moment matters, as structured `StoryLine`s (`kind`, `team`, cited `event_ids`, `text`). The text is rendered from the cited events only (their scores, minutes and players), and the lines are derived from the goals up to the moment's own event, so a story never depends on later events.

| moment | kinds |
|---|---|
| goal (exactly one situation) | `opener`, `equaliser` ("level it at 2-2, 6 minutes after going behind"), `comeback` (takes the lead after trailing, without leading in between: "were 2-1 down in minute 71. Goals in minutes 77 and 81 turned it around."), `back_in_front`, `extends_lead`, `pulls_back` |
| goal (extras) | `redemption` (the scorer's earlier big chance was missed, and the scorer has not scored since), `brace` |
| missed big chance | `stakes` ("It stays 0-1; a goal would have doubled Eastvale Athletic's lead.") |
| full time | `ft_comeback` or `ft_result`, `winner` (the goal that took the winner past the loser's final total), `lead_changes` |

`check_line(line, match, as_of_event_id)` is the validator: cited events must exist, be in match order and not after the as-of event; the text must re-render identically from them; and the kind's condition must hold on them (for example `comeback`: behind after the first cited goal, level before the last, ahead after it). Lines that rely on the whole score history ("for the first time", "changed hands twice") cite every goal so far, and the check requires each cited goal to add exactly one to the total, so no goal can be left out. A line that fails is dropped from the card and reported in `suppressed`.

## Insight cards (`matchlens.cards`)
`build_cards(match, mode, provider=None, limit=None)` builds one card per selected key moment. A moment must re-detect identically (`verify_moment`) or its card is suppressed with a reason. Each card has `title`, `start_clock_s` (first evidence event) / `anchor_clock_s` (anchor event; its minute is shown) / `publish_clock_s` (last evidence event) / `end_clock_s` (publish + 12 s), `evidence_event_ids`, verified `facts` and a `narrative`. `CardSet.metrics` holds every metric the cards cite.

**Causality.** A card is never shown before `publish_clock_s`, and analyst window metrics are as-of metrics computed at the moment's last evidence event, so a card never counts or cites an event that has not happened yet. `future_sources(...)` re-checks every cited event, evidence event and metric source against the publication time and match order; a card that fails is suppressed with the offending IDs. Regression (seed 1): the 71' goal card used to show "Shots: 4" for 60'-75' when only 3 had been taken (the 4th, `ml1-01166`, is at 74:05.8, minute 75); `tests/test_agent.py` keeps this case. When press wins still had cards, the 8' press win was also shown 14 s before its shot `ml1-00130`; press wins no longer get a card (see Key moments).

**Scope of the guarantee.** Causality covers which moments get a card, what each card says and when it appears; the stream processor applies the same rules event by event ([Event-by-event processing](#event-by-event-processing-matchlensstream)). The public replay is still precomputed, and its two demo shortcuts (`highlights`, below) are chosen with hindsight; they only move the replay clock and never change a card.

Facts are the only factual content. Each is rendered from verified structured data and carries `event_ids` and/or `metric_ids`, and a `role`: `why` (story lines and the full-time shots vs chance quality comparison, shown first and emphasised), `what` (what happened) or `chain` (one evidence event of the move, analyst only, folded in the UI).

| | Casual Fan | Analyst |
|---|---|---|
| content | story headline ("Comeback! Eastvale Athletic take the lead"), scorer and score, why it matters, how the move began (tackle, then 6 passes), outcome of a chance and what a goal would have meant; full time: how it was won | the same story lines with evidence, every evidence event, possession and build-up structure, shot quality from the xG heuristic, metrics for the 15-min window around the moment; full time: verified totals |
| statistics | only the full-time comparison of shots and chance quality, worded "synthetic xG, a heuristic" | labelled xG heuristic and window metrics |
| evidence in text | no (kept as structured IDs on each fact) | yes (`[evidence: ...]` on every fact) |

## Narrative providers (`matchlens.narrative`)
`NarrativeProvider.render(NarrativeRequest) -> str` only rewords the verified fact texts. `provider_from_env()` returns `AzureOpenAIProvider` (Microsoft Foundry / Azure OpenAI **Responses API v1**, `POST {endpoint}/openai/v1/responses` with body `model` (= deployment), `input` (system + user messages), `max_output_tokens: 200`, `store: false`; `api-key` header, HTTPS required, redirects refused, stdlib HTTP only) when all three `AZURE_OPENAI_*` variables are set, else `OfflineTemplateProvider`. The text is the joined `output_text` parts of `message` items in the reply's `output` array; a reply whose `status` is not `completed`, or that has no `output_text`, raises `ResponseFormatError` and the card falls back. `narrate()` labels the result:
- `deterministic_template` + "Offline fallback: deterministic template wording — not an Azure response." (offline, or any provider error; `fallback_reason` names the exception type only, never secrets);
- `unverified_ai_wording` + "AI-generated wording (Azure OpenAI) — not verified" for model output. Numbers in model output that do not appear in the facts are listed in `warnings` (a guard, not verification).

No narrative is ever `verified`; model output never changes a card's facts.

## Claim agent (`matchlens.agent`)
One bounded agent, no framework. For each analyst card, a proposer drafts up to 4 **structured** claims: `metric_id` + `value` and/or `cited_event_ids`. Claims never carry free text.
- **Context.** `review_context(match, card)` cuts the match at the card's last evidence event, computes as-of metrics there, and records which events come later.
- **Tools.** The Foundry agent can call three read-only tools bound to that state: `list_metrics`, `get_metric` (value, definition, sources) and `get_events` (up to 25). A later event and an unknown one both return `{"error": "not available"}`, so the tools reveal nothing about the future.
- **Agent loop.** `FoundryAgentProposer` uses the Responses API v1 with function tools, a strict `json_schema` answer format and `store: false`. Each call is echoed back without item IDs. An attempt makes at most 6 model calls. The last one, or the first after 16 tool calls, is sent with `tool_choice: "none"`, so the model must answer with its claims instead of looking things up again.
- **Verdicts.** `check_claim` runs `verify_claim` against the cut-off state. A claim citing a later event is rejected with "cited events happen after the card is published". When a claim names a real metric with a wrong value or wrong sources, the computed value is rendered as a **correction**.
- **Retry.** `review_card` sends all verdicts back once (`MAX_RETRIES = 1`, fresh conversation). Claims still rejected are dropped. `published` = verified final claims + corrections, all template-rendered.
- **Failure.** A proposer failure falls back to `OfflineProposer`, which claims the card's own single-metric facts, with `fallback_reason` (exception type only). `strict=True` (CLI `agent-review`) raises instead.
- **Injected test claim.** On the first goal card, an injected test claim states the scoring team's goals +1 (seed 1: `away.goals:asof:ml1-00089 = 2`, actual 1). A replayed run puts it where the recording had it (seed 1, 2026-10-24 run: the 71' card, `home.goals:asof:ml1-01112 = 3`, actual 2). It is labelled as deliberately wrong and exercises the validator; it is never presented as model output.
- **Recording.** A live run is recorded as a redacted `matchlens.agent-trace/v1` file: claims, response IDs, model, usage and tool-call names/arguments, never the key. `RecordedProposer` replays its claims for `export-site --agent-trace`, the verdicts are **recomputed**, and the label says "not a live call". A card the recording does not cover (the 2026-10-24 run covers the 38', 71', 77' and 81' cards; the 5' and 56' goal cards and the full-time card were added later) is reviewed by `OfflineProposer` and its notes say it is not part of the recorded run.
- **Bundle and UI.** The bundle holds `reviews` keyed by analyst card ID. Their cited metrics are added to `metrics`. Each review carries `explanation`: plain sentences built deterministically from the verdicts and the as-of state (`explain_review`), never by a model. Analyst cards show a "Claim check" section with that summary first, then source, verdict, reasons, correction and chips (folded).

## Event-by-event processing (`matchlens.stream`)
- **Records as they arrive.** `iter_records(lines)` (`matchlens.io`) yields the match record, then each event, as each line is read; the first malformed record raises `MatchFormatError`.
- **Validation on arrival.** `EventValidator(meta).check(event)` applies the rules of `validate_match` to one event, given the events before it; `finish()` adds the whole-match check (both periods closed). `validate_match` is exactly this, run over a complete match.
- **The processor.** `StreamProcessor(meta).push(event)` returns an `Update`: the event, the score, running shots and synthetic xG, its validation issues, and the cards published on its arrival (`Publication`: card, the event that published it, and `evidence_seq`, the card's last evidence event).
  - Moments are detected again only when an event can complete one: a shot, the event after a shot, a `period_end`.
  - A `big_chance_missed` moment waits one event (its evidence may include the answering event).
  - `close()` completes waiting moments and records `end_issues` (for example, no final whistle).
  - After the first per-event issue nothing more is published.
- **The check.** `stream_check(match, card_sets)` compares a full run with the batch cards: the same cards (wording aside, since it depends on the provider), each with `seq == evidence_seq`, the same suppressions, and no issues. Otherwise it raises `ValueError`. The bundle carries the result as `stream` (`matchlens.stream-check/v1`: events, cards, identical, published at last evidence).
- **CLI.**
  - `feed (--seed N | --file F) [--speed X]` writes JSONL line by line, flushed and paced by the match clock.
  - `stream (PATH | -) [--mode] [--json]` prints each card as it is published and a summary (events, cards, maximum wait after the last evidence event, issues). It exits with 1 on any issue.

## Match recap (`matchlens.recap`) — schema `matchlens.recap/v1`
`build_recap(match, casual_cards)` returns `None` without a full-time card. Otherwise: `publish_clock_s` (the full-time card's), `headline` (that card's title), `summary` (its WHY facts) and `goals` (each goal card's scorer fact, in match order; empty unless every goal of the match has one). Each line is `{card_id, index, text, kind, event_ids, metric_ids}` with `index` -1 for a title, so the personalized variants of the casual cards apply by `(card_id, index)`. No line has wording of its own. `format_recap` renders the copy text; the bundle key is `recap`.

## Overlay cues (`matchlens.overlays`) — schema `matchlens.overlays/v1`
Top level: `match_id`, `seed`, `synthetic: true`, `time_unit: "match_seconds"`, `teams`, `final_score`, `notes`, `cues`. Each cue is one card in one mode: `cue_id`, `mode`, `kind`, `team`, `start_s` (card publication = last evidence event; every cited event and metric source is at or before it), `end_s` (`start_s` + 12 s), `anchor_s` (anchor event), `lead_in_s` (first evidence event), `title`, `facts` (verified text + `event_ids`/`metric_ids` + `kind`), `evidence_event_ids`, and `narrative` with its `status` and `label`. Consumers must show `facts` as facts and `narrative` only as labelled wording.

## Replay bundle and UI (`matchlens.replay`, `matchlens.server`, `src/matchlens/web/`)
`build_bundle(match, provider)` precomputes everything the browser needs (schema `matchlens.replay/v1`): all events, cards for both modes, suppressed moments, the metrics cited by cards (with sources), running goals/shots/xG heuristic per team after each shot (`stat_steps`, checked against `compute_metrics` as of each shot, so the stats panel changes on the same event as the scoreboard), the overlay document, and `highlights`: `turning_point` (start of the winner's comeback goals, else the winning goal, else the last goal, with a label such as "Watch the comeback (77'–81')") and `ai_check` (the analyst card whose recorded Foundry claims were rejected and fixed by the agent's retry, else the card with the injected test claim). The browser only displays the bundle and never decides what is verified. The UI is static HTML/CSS/vanilla JS with no dependencies and no build step. It sets text only through `textContent`; `serve` adds a strict CSP (`script-src 'self'`, no inline or eval) and serves a fixed in-memory file set (no filesystem paths, so no traversal).

Narrative display rules in the UI: ✓ marks only verified facts. Each card ends with one label line: "✓ checked against match events" and the wording source, "Template wording · no AI" (the tooltip and the page footer say the wording comes from deterministic templates, not from a model; the bundle keeps the provider's own label) or "AI wording · NOT VERIFIED" plus the wording itself; `unsupported_numbers` warnings are shown as "(heuristic check only)", because that check cannot establish the semantic truth of model prose.

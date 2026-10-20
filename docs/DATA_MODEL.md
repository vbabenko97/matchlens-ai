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
`verify_claim(claim, metrics, match)` returns `verified` only if the claim cites a metric and/or events; all cited events exist; a cited metric exists, is defined and matches the stated value (tolerance 5e-4); cited events are sources of that metric; and heuristic metrics are described as "heuristic" or "synthetic". Otherwise it returns `unsupported` with reasons, so callers must suppress or flag the claim.

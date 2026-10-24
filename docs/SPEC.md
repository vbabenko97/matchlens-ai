# MatchLens AI — MVP specification

## Goal
Make synthetic football events intelligible and explainable for studio/broadcast and personalized fan experiences. Optimize for a convincing 2-minute demo, not full match-engine realism.

## Pipeline and acceptance checks

1. **Ingest**. Generate a seeded fictional 90-minute match as JSONL and optionally replay it progressively. Include team/player metadata, sequence, event ID, timestamp, event type, team, possession ID, optional spatial data, and factual outcomes. Validate malformed records.
2. **Interpret**. Calculate passes/completion rate, shots, goals, synthetic expected goals (explicitly labeled heuristic), pressure activity, turnovers, and rolling-window trends. Tie every calculation to a documented selection of source events.
3. **Explain**. Identify the match moments that matter using deterministic, causal detection (every goal, every missed big chance, full time; the choice never depends on later events) and say why each matters (comeback, equaliser, what a missed chance would have meant), with every story line re-checked against its cited events. Produce short narrative cards from structured facts, with evidence IDs and a claim-verification step. Integrate Microsoft Foundry / Azure-hosted AI for natural-language rendering when available, with a transparently labeled deterministic fallback.
4. **Render**. Interactive replay dashboard with current score, match minute, simple charts, overlay card time windows, and downloadable timestamped JSON of overlay cues.
5. **Personalize**. Casual mode (plain-language significance) and Analyst mode (technical metrics and evidence). An optional club focus/language toggle is stretch scope.

## Priority and exclusions
P0: reproducible generator + data validation + metric engine + evidence-linked cards + dashboard + tests.
P1: actual Microsoft/Azure inference path + usable public demo with deterministic fallback + clear Microsoft integration evidence.
P2: polished visual storytelling, two-minute screen recording, README and final submission.
Explicit exclusions for MVP: video-based player tracking, real club logos and footage, live official Premier League data, empirically calibrated xG, heavyweight agent orchestration, database infrastructure unless justified.

## Validation scenarios
- Two runs with the same seed produce identical events.
- Clocks do not go backward; goals match final score; event and possession IDs are consistent.
- Deliberately corrupting sequence/score/possession causes validator failure.
- Analyst and Casual presentations differ in content, not just headings.
- An explanation with an ungrounded factual claim fails validation and does not appear as verified.
- UI remains functional without a configured inference endpoint, clearly labeling deterministic fallback.
- Judges can access the deployed demo without creating an account or paying.

## External constraints
Official rules: https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md
Submission deadline: Oct 27 2026 23:59 PT. Require public GitHub URL, public demo-video link <2 minutes, and working project accessible for judging.
Actual Microsoft/Azure deployment and paid endpoints require a separate Azure account or equivalent available credits. Do not confuse Claude Code credits with Azure runtime funding.

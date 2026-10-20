# MatchLens AI — Claude Code instructions

## Mission
Build a working, original hackathon entry for Microsoft's "Inside the Game: Developer Hackathon" by the submission deadline: 2026-10-27 23:59 Pacific / 2026-10-28 07:59 Europe/Vienna. Target a completed submission by 2026-10-26, leaving time for failures.

Project page: https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon/project/126828
Official rules: https://github.com/microsoft/insidethegamehackathon/blob/main/OFFICIAL%20RULES.md

Read docs/SPEC.md and docs/TASKS.md before changing architecture. Treat official rules as authoritative and reconfirm any ambiguities.

## Main deliverable
A reproducible, interactive demonstration of the five-stage pipeline:
synthetic football events -> interpretable statistics -> evidence-grounded explanations -> timed, machine-readable overlays -> genuinely different casual/analyst experiences.

## Constraints
- Build new, original code. A competitor's `match_sim.py` was inspected only as a reference; do not copy it or its generated dataset.
- Use synthetic events and fictional teams/players. No copyrighted match footage, club branding, or unauthorized third-party data.
- Deliver real Microsoft integration (prefer a Microsoft Foundry / Azure AI inference path), documented with evidence. Azure credentials/resources are a separate prerequisite; Anthropic coding credits do not pay Azure costs. Provide a deterministic demo fallback when Azure is unavailable; label it transparently and never pretend the fallback is a live Azure response.
- Prefer Python 3.11+, typing, pytest, ruff, deterministic seeds, minimal dependencies. Streamlit is an acceptable quick UI; avoid framework churn.
- No secrets in git, logs, screenshots, demo links, or test fixtures. Create .env.example for configurable endpoints.
- No unnecessary microservices, Kafka, vector databases, speculative multi-agent frameworks, or extra MCP servers.
- Prioritize working, end-to-end UX and verification over architecture diagrams and speculative features.

## Event semantics and correctness
- Event IDs must be unique; clock nondecreasing; periods and score internally consistent.
- Explicitly track possession and its transitions. Possession chains must be traceable.
- State geometry and coordinate reference convention; use consistent ball origin/destination between related events within a documented tolerance.
- Separate true facts derived from simulated events from heuristic scores (e.g., synthetic xG), and never present heuristics as empirically calibrated.
- Every generated narrative claim must cite event IDs and/or computed metric IDs. Unsupported claims are suppressed or flagged, not invented.
- Replay of the same seed and configuration yields byte-for-byte equivalent match events, aside from explicitly excluded nonsemantic timestamps.

## Execution protocol
1. Choose the smallest unfinished task from docs/TASKS.md. Work on a focused vertical slice.
2. Before implementation, state the intended behavior and the one verification command.
3. Implement and write automated tests, especially invariants and failure cases.
4. Run `python -m pytest -q` and `ruff check .` when configured. Fix failures; do not claim green tests without running them.
5. At each milestone, update docs/TASKS.md with verified results and the next command. Commit a coherent checkpoint.
6. Stop exploring alternatives once a viable choice passes acceptance checks.
7. If credentials, deployment permissions, or external billing are required, stop and explicitly ask the operator; never assume access or purchase resources.
8. Keep output concise: what changed, tests run, blocker, next task. Don't paste long logs.

## Cost and scope
- Default to Sonnet 5.5 for implementation. Reserve Opus for unresolved hard architecture or debugging blockers.
- Avoid spawning parallel agent teams; use a single main agent for MVP. Use subagents only for bounded, valuable checks.
- Do not repeatedly re-read unchanged large files. Keep each chat focused; use /clear between unrelated milestones after checkpointing progress.
- Commit by increments; do not auto-deploy or submit the competition entry without explicit user approval.

## Definition of done
- Public original GitHub source; documented setup and license; reproducible fixtures and tests.
- Public, free-to-test working demo (no developer API key required of the judge).
- Proven five-stage pipeline with distinct casual/analyst modes, source event IDs, timed overlay output, and actual disclosed Microsoft technology use.
- Public demo video < 2 minutes and completed Innovation Studio submission before deadline.

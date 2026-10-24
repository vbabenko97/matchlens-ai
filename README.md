# MatchLens AI

Evidence-grounded synthetic football match intelligence for Microsoft × Premier League Inside the Game: Developer Hackathon.

**Status:** M0 (skeleton, tests, CI), M1 (synthetic generator + validator) and M2 (metrics, claim checks, key-moment detection) and M3 (insight cards, Casual/Analyst modes, Microsoft Foundry provider with offline fallback) done. A live Foundry Responses API v1 call (`gpt-4.1-mini`) was verified with `azure-check` on 2026-10-24 ([evidence](docs/evidence/azure-check-2026-10-24.json)). M4 browser replay UI done. M5 is partially complete: the public demo is live on GitHub Pages and the scheduled public smoke test passes. The accessibility code pass is done ([audit](docs/ACCESSIBILITY.md)); a screen-reader check, demo rehearsal and video are still to do.

**Live demo (free, no sign-in or API key):** <https://vbabenko97.github.io/matchlens-ai/>. It uses the labelled offline template wording, and its analyst claim checks replay a recorded live Microsoft Foundry agent run (labelled "not a live call"). The public site never calls Azure. See [MVP specification](docs/SPEC.md) and [milestones](docs/TASKS.md).

Project page: https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon/project/126828

## Run the demo locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
python -m matchlens serve            # seed 1 (2-3), http://127.0.0.1:8000/
```

The replay UI shows a scoreboard, pitch view of the latest ball events, a timeline with key-moment markers, and insight cards that appear as moments happen: one for every goal and missed big chance, and a full-time card. Each card leads with why the moment matters (a comeback, an equaliser, what a missed chance would have meant, how the match was won), checked against the events it cites. Two shortcuts save a viewer the 90 minutes: **Watch the comeback** plays the turning point, and **See the AI get fact-checked** opens the analyst card where the recorded Foundry agent's claim was rejected and fixed on retry. You can play, pause, seek, jump to the previous/next moment and change speed (15×–240×), and switch between **Casual Fan** and **Analyst** modes. In Analyst mode, every event and metric ID on a card can be clicked to inspect it and highlight it on the pitch. **⬇ Overlay JSON** downloads the timed overlay cues (`matchlens.overlays/v1`). Keyboard: Space plays or pauses, ← / → seek 30 s (when focus is not on a control); ← / → also switch the audience mode when the mode toggle is focused. Accessibility target and audit: [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md).

Deployment (static export on GitHub Pages) and the public-URL Playwright smoke test are described in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

It runs fully offline without Azure credentials (deterministic, labelled template wording). Other options: `--seed N`, `--file match.jsonl`, `--port`, `--offline`. `python -m matchlens export-site --out site/` writes the same UI as a static folder (no server code needed to host it).

### Language and club (personalizer agent)

In Casual mode, **Language** (English, Deutsch, Українська) and **Follow** (neutral or either team) rewrite the cards for that audience. A second agent does the rewriting: a Microsoft Foundry deployment, called once per card through the Responses API with structured JSON output. A deterministic checker (`src/matchlens/personalize.py`) compares every rewritten line with its verified English fact:
- the same numbers, as digits;
- the same player and team names in Latin script, and no new ones;
- `xG` kept, the right script, no markup or links.

A rejected line goes back to the agent once with the reasons; if it fails again, the English fact is shown, marked `lang="en"`. The rewritten lines keep the facts' event IDs. The page labels them as AI wording with numbers and names checked, because meaning and tone are not checked. The score check also keeps the home-first order, so `2-3` never becomes `3-2`. The public build replays a recorded run (`python -m matchlens personalize-review --out <trace>`, then `export-site --personalize-trace <trace>`); without one, the picker is hidden.

### More matches

The **Match** picker switches between the demo match (seed 1, the only one with the recorded Foundry run and the live check) and five untuned seeds from the checked range 100–129: 121, 106, 103, 123 and 124. They were chosen for different stories: a 3–4 comeback, a 3–3 draw, a 4–1 comeback, a 0–5 rout and a 0–1 with little to say. Labels show the seed only, so the picker never spoils a result. Every extra match is built offline (template wording, offline claim proposer), and its page says so. A link can name a match: `?seed=121`; the on-air graphic takes the same parameter.

### Live Foundry check (Azure Static Web Apps build)

On the Azure Static Web Apps deployment, each analyst card has **Check this card live with Microsoft Foundry**. The server-side function (`api/`, `src/matchlens/live.py`) takes only the card ID, runs the bounded claim agent and the validator against Foundry, and shows the live result with its response IDs. A shared daily cap (Azure Table Storage, reserved before every model call) bounds spend; without it no live call is made. When no live call is made, the page keeps the recorded run and says so. Setup: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Locally: `python -m matchlens serve --live-api`.

### On-air graphic (broadcast lower-third)

`broadcast.html` reads the same `data/overlays.json` and draws the active cue as a lower-third on a transparent page, so it can be added as a **browser source** in OBS or vMix (1920×1080 or 1280×720) and composited over video. Only the verified "why" fact goes on air, never the narrative wording; analyst cues also show the source event IDs. **▣ On-air graphic** in the replay UI opens a preview in the current mode.

| URL parameter | Effect |
|---|---|
| `mode=casual` / `mode=analyst` | audience mode (default casual) |
| `bg=transparent` / `green` / `dark` | background: transparent for compositing (default), chroma green, or dark for preview |
| `cue=<cue_id>` | show one cue and hold it (stills, manual cueing) |
| `rundown=1&hold=8` | play every cue of the mode in turn, `hold` real seconds each, looping |
| `from=<s>&speed=<x>` | otherwise follow the match clock from `from` match seconds at `speed`× |

Example browser source: `https://vbabenko97.github.io/matchlens-ai/broadcast.html?mode=casual&from=4590&speed=1`. A cue never appears before its `start_s` (the card's publication time).

## Quick start

Requires Python 3.11+. No runtime dependencies; `pytest` and `ruff` for development.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q && ruff check . && ruff format --check .
# optional browser end-to-end tests
pip install -e ".[e2e]" && python -m playwright install chromium
python -m pytest -q -m e2e

python -m matchlens generate --seed 42 --out match.jsonl   # full 90-minute synthetic match
python -m matchlens validate match.jsonl                    # format + invariant checks
python -m matchlens metrics match.jsonl                     # event-grounded metrics as JSON
python -m matchlens moments match.jsonl                     # card moments (goals, big chances, full time) with evidence IDs
python -m matchlens cards match.jsonl --mode casual         # verified insight cards (--json, --offline)
python -m matchlens demo --seed 1 --offline                 # end-to-end demo, both audience modes
python -m matchlens overlays match.jsonl --out overlays.json # timed overlay cues, both modes
```

Data format, geometry and possession rules: [docs/DATA_MODEL.md](docs/DATA_MODEL.md). Deployment/credentials plan: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Teams and players are fictional; all events are synthetic.

## Microsoft Azure OpenAI / Foundry (optional)

Card wording can come from a Microsoft Foundry / Azure OpenAI model deployment through the **Responses API v1** (`POST {endpoint}/openai/v1/responses` with `model`, `input`, `max_output_tokens` and `store: false`). Export the three variables from `.env.example` (the CLI reads the process environment; it does not load `.env` files):

```bash
export AZURE_OPENAI_ENDPOINT=https://<resource>.services.ai.azure.com
export AZURE_OPENAI_DEPLOYMENT=<deployment-name>   # sent as "model"
read -rs AZURE_OPENAI_API_KEY && export AZURE_OPENAI_API_KEY   # never commit
python -m matchlens demo --seed 42
```

`AZURE_OPENAI_API_VERSION` is no longer used; the v1 API takes no `api-version`.

To verify one real call with no fallback, run `python -m matchlens azure-check` (see [docs/AZURE_VERIFICATION.md](docs/AZURE_VERIFICATION.md)). Recorded live run, redacted: [docs/evidence/azure-check-2026-10-24.json](docs/evidence/azure-check-2026-10-24.json).

**Claim agent.** `python -m matchlens agent-review --seed 1` runs one bounded Foundry agent per analyst card.
- The agent calls read-only tools (`list_metrics`, `get_metric`, `get_events`), which only see the match up to the card's publication.
- It proposes structured claims (metric ID + value and/or event IDs).
- The deterministic validator accepts or rejects each claim. Rejections go back to the agent once, with the reasons.
- Only verified claims and template-rendered corrections are published.

Recorded live run, redacted: [docs/evidence/agent-trace-2026-10-24.json](docs/evidence/agent-trace-2026-10-24.json) (`gpt-4.1-mini`; 19 Responses API calls, 27 tool calls). On the 77' card the validator rejected a model claim citing the wrong events, and the agent's retry fixed it. The public build replays this trace and re-checks every claim at build time (`export-site --agent-trace …`). Code path: `src/matchlens/agent.py`; recording steps: [docs/AZURE_VERIFICATION.md](docs/AZURE_VERIFICATION.md) §5b.

Without them (or with `--offline`, or on any request failure) cards use a deterministic template labelled "Offline fallback — not an Azure response". AI wording is always labelled unverified; the verified facts on each card are rendered from structured data and never from model output. Code path: `src/matchlens/narrative.py` (`AzureOpenAIProvider`).

## Using Claude Code

Clone the repo locally and launch `claude` in the repository root. Claude will read the checked-in `CLAUDE.md` file automatically. Ask it to implement the next unchecked milestone in `docs/TASKS.md`, including tests and a verified checkpoint.

Claude Code skills are optional. The single `/submission-audit` skill is for the final audit and must be invoked manually.

## Important

- Only original synthetic data and code may be contributed.
- Never commit secrets, API keys, or personally identifying credentials.
- Anthropic usage credits for coding are separate from any Azure runtime charges.

## License

MIT — see [LICENSE](LICENSE). The project has no runtime dependencies; development tools (pytest, ruff, setuptools) are MIT/BSD/Apache-licensed and are not redistributed.

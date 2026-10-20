# MatchLens AI

Evidence-grounded synthetic football match intelligence for Microsoft × Premier League Inside the Game: Developer Hackathon.

**Status:** M0 (skeleton, tests, CI), M1 (synthetic generator + validator) and M2 (metrics, claim checks, key-moment detection) and M3 code (insight cards, Casual/Analyst modes, Azure OpenAI provider with offline fallback) done; the Azure path has not yet been run against a live endpoint. M4 browser replay UI done (local); public deployment is not done yet. See [MVP specification](docs/SPEC.md) and [milestones](docs/TASKS.md).

Project page: https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon/project/126828

## Run the demo locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e .
python -m matchlens serve            # seed 1 (2-3), http://127.0.0.1:8000/
```

The replay UI shows a scoreboard, pitch view of the latest ball events, a timeline with key-moment markers, and insight cards that appear as moments happen. You can play, pause, seek, jump to the previous/next moment and change speed (15×–240×), and switch between **Casual Fan** and **Analyst** modes. In Analyst mode, every event and metric ID on a card can be clicked to inspect it and highlight it on the pitch. **⬇ Overlay JSON** downloads the timed overlay cues (`matchlens.overlays/v1`). Keyboard: Space plays or pauses, ← / → seek 30 s.

It runs fully offline without Azure credentials (deterministic, labelled template wording). Other options: `--seed N`, `--file match.jsonl`, `--port`, `--offline`. `python -m matchlens export-site --out site/` writes the same UI as a static folder (no server code needed to host it).

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
python -m matchlens moments match.jsonl                     # 2-4 key moments with evidence IDs
python -m matchlens cards match.jsonl --mode casual         # verified insight cards (--json, --offline)
python -m matchlens demo --seed 1 --offline                 # end-to-end demo, both audience modes
python -m matchlens overlays match.jsonl --out overlays.json # timed overlay cues, both modes
```

Data format, geometry and possession rules: [docs/DATA_MODEL.md](docs/DATA_MODEL.md). Deployment/credentials plan: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Teams and players are fictional; all events are synthetic.

## Microsoft Azure OpenAI / Foundry (optional)

Card wording can come from an Azure OpenAI / Microsoft Foundry chat deployment. Export all four variables from `.env.example` (the CLI reads the process environment; it does not load `.env` files):

```bash
export AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com/
export AZURE_OPENAI_API_KEY=...            # never commit
export AZURE_OPENAI_DEPLOYMENT=<deployment-name>
export AZURE_OPENAI_API_VERSION=<api-version>
python -m matchlens demo --seed 42
```

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

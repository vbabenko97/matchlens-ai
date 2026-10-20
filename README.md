# MatchLens AI

Evidence-grounded synthetic football match intelligence for Microsoft × Premier League Inside the Game: Developer Hackathon.

**Status:** M0 (skeleton, tests, CI) and M1 (synthetic generator + validator) done; metrics, explanations, UI and Azure integration are not built yet. See [MVP specification](docs/SPEC.md) and [milestones](docs/TASKS.md).

Project page: https://innovationstudio.microsoft.com/hackathons/insidethegamedeveloperhackathon/project/126828

## Quick start

Requires Python 3.11+. No runtime dependencies; `pytest` and `ruff` for development.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q && ruff check . && ruff format --check .

python -m matchlens generate --seed 42 --out match.jsonl   # full 90-minute synthetic match
python -m matchlens validate match.jsonl                    # format + invariant checks
```

Data format, geometry and possession rules: [docs/DATA_MODEL.md](docs/DATA_MODEL.md). Deployment/credentials plan: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Teams and players are fictional; all events are synthetic.

## Using Claude Code

Clone the repo locally and launch `claude` in the repository root. Claude will read the checked-in `CLAUDE.md` file automatically. Ask it to implement the next unchecked milestone in `docs/TASKS.md`, including tests and a verified checkpoint.

Claude Code skills are optional. The single `/submission-audit` skill is for the final audit and must be invoked manually.

## Important

- Only original synthetic data and code may be contributed.
- Never commit secrets, API keys, or personally identifying credentials.
- Anthropic usage credits for coding are separate from any Azure runtime charges.

## License

MIT — see [LICENSE](LICENSE). The project has no runtime dependencies; development tools (pytest, ruff, setuptools) are MIT/BSD/Apache-licensed and are not redistributed.

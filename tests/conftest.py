from pathlib import Path

import pytest

from matchlens.generator import generate_match
from matchlens.schema import Match

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"
DEMO_FIXTURE = FIXTURE_DIR / "demo_seed7_5min.jsonl"
DEMO_SEED = 7
DEMO_PERIOD_S = 300


@pytest.fixture(scope="session")
def full_match() -> Match:
    return generate_match(42)

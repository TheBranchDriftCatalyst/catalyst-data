from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
SOURCE_TEXT = (FIXTURES_DIR / "sample_source_text.txt").read_text()


@pytest.fixture
def source_text() -> str:
    return SOURCE_TEXT


@pytest.fixture
def valid_mentions_data() -> list[dict]:
    return json.loads((FIXTURES_DIR / "valid_mentions.json").read_text())


@pytest.fixture
def invalid_mentions_data() -> list[dict]:
    return json.loads((FIXTURES_DIR / "invalid_mentions.json").read_text())


@pytest.fixture
def valid_propositions_data() -> list[dict]:
    return json.loads((FIXTURES_DIR / "valid_propositions.json").read_text())


@pytest.fixture
def invalid_propositions_data() -> list[dict]:
    return json.loads((FIXTURES_DIR / "invalid_propositions.json").read_text())


@pytest.fixture
def known_mention_ids() -> set[str]:
    return {"mention_0", "mention_1", "mention_2"}

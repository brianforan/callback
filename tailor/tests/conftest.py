import os

os.environ["ANTHROPIC_API_KEY"] = "test-key-not-used"

from pathlib import Path

import pytest

from tailor.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def resume_tex() -> str:
    return (FIXTURES / "resume.tex").read_text(encoding="utf-8")


@pytest.fixture
def settings():
    return get_settings()

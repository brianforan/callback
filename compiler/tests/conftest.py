import os

# A canary that must never reach a compile, since the service's own environment is not passed on.
os.environ["LEAK_CANARY"] = "canary-value-not-for-latex"
os.environ["LATEX_TIMEOUT_SECONDS"] = "20"

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from compiler import main
from compiler.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def resume_tex() -> str:
    return (FIXTURES / "resume.tex").read_text(encoding="utf-8")


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def client():
    return TestClient(main.app)

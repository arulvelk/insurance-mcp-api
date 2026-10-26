import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.repository import InMemoryRepository  # noqa: E402
from app.services import InsuranceService  # noqa: E402

TODAY = date(2026, 10, 26)


@pytest.fixture
def service() -> InsuranceService:
    return InsuranceService(InMemoryRepository(), today=lambda: TODAY)

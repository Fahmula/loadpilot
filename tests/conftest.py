import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import pytest

# Point LoadPilot at a throwaway data directory before the app module is imported,
# since it creates its folders and log files at import time.
os.environ["LOADPILOT_DATA_DIR"] = tempfile.mkdtemp(prefix="loadpilot-test-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
from loadpilot.database import DatabaseManager  # noqa: E402
from loadpilot.models import Load, LoadStatus  # noqa: E402


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "loadpilot.db"
    monkeypatch.setattr(app_module, "DB_PATH", path)
    return path


@pytest.fixture
async def db(db_path):
    manager = DatabaseManager(db_path=str(db_path))
    await manager.init_db()
    return manager


@pytest.fixture
def client(db_path):
    with TestClient(app_module.app) as test_client:
        yield test_client


def make_load(**overrides) -> Load:
    fields = {
        "broker_name": "Acme Logistics",
        "broker_email": "dispatch@acme.example",
        "load_reference": "PO-1234",
        "rate_amount": 2500.0,
        "pickup_location": "Dallas, TX",
        "pickup_date": date(2026, 10, 1),
        "delivery_location": "Atlanta, GA",
        "delivery_date": date(2026, 10, 3),
        "loaded_miles": 1000,
        "status": LoadStatus.ACTIVE,
    }
    fields.update(overrides)
    return Load(**fields)

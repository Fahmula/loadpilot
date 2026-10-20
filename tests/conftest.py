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


TEST_USERNAME = "driver"
TEST_PASSWORD = "correct-horse-battery"


@pytest.fixture
def anon_client(db_path):
    """A client that hasn't logged in."""
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def client(anon_client):
    """A client signed in to a freshly created account."""
    response = anon_client.post(
        "/setup",
        data={"username": TEST_USERNAME, "password": TEST_PASSWORD, "confirm_password": TEST_PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return anon_client


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

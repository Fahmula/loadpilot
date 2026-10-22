from datetime import date, datetime

import aiosqlite
import pytest

from loadpilot import database
from loadpilot.database import MIGRATIONS, DatabaseManager
from loadpilot.models import LoadStatus

from .conftest import make_load


async def test_add_and_get_load_round_trip(db):
    saved = await db.add_load(make_load(notes="PU# 5521"))

    fetched = await db.get_load(saved.id)

    assert fetched == saved
    assert fetched.pickup_date == date(2026, 10, 1)
    assert fetched.status is LoadStatus.ACTIVE


async def test_get_loads_filters_by_status_and_search(db):
    await db.add_load(make_load(broker_name="Acme Logistics"))
    await db.add_load(make_load(broker_name="Blue Freight", status=LoadStatus.DELIVERED))

    delivered = await db.get_loads(status=LoadStatus.DELIVERED)
    searched = await db.get_loads(search_query="acme")

    assert [load.broker_name for load in delivered] == ["Blue Freight"]
    assert [load.broker_name for load in searched] == ["Acme Logistics"]


async def test_update_load_writes_allowed_fields(db):
    saved = await db.add_load(make_load())

    updated = await db.update_load(
        saved.id, {"status": LoadStatus.DELIVERED, "actual_delivery_date": date(2026, 10, 4)}
    )

    assert updated.status is LoadStatus.DELIVERED
    assert updated.actual_delivery_date == date(2026, 10, 4)


async def test_update_load_rejects_unknown_columns(db):
    saved = await db.add_load(make_load())

    with pytest.raises(ValueError):
        await db.update_load(saved.id, {"rate_amount = 0, broker_name": "pwned"})

    assert (await db.get_load(saved.id)).broker_name == "Acme Logistics"


async def test_delete_load(db):
    saved = await db.add_load(make_load())

    assert await db.delete_load(saved.id) is True
    assert await db.get_load(saved.id) is None
    assert await db.delete_load(saved.id) is False


async def test_settings_cast_booleans(db):
    await db.save_setting("company_name", "Fahmula Trucking")
    await db.save_setting("show_bank_on_invoice", "true")

    settings = await db.get_all_settings()

    assert settings == {"company_name": "Fahmula Trucking", "show_bank_on_invoice": True}
    assert await db.get_setting("missing", "fallback") == "fallback"


async def test_init_db_is_idempotent(db):
    await db.init_db()

    async with aiosqlite.connect(db.db_path) as conn, conn.execute("PRAGMA user_version") as cursor:
        assert (await cursor.fetchone())[0] == len(MIGRATIONS)


async def test_migration_moves_invoice_out_of_rate_con_column(tmp_path):
    """A database created before migrations existed keeps its invoices, now in invoice_pdf_path."""
    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""
            CREATE TABLE loads (
                id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_number TEXT UNIQUE, broker_name TEXT NOT NULL,
                broker_email TEXT, load_reference TEXT NOT NULL, rate_amount REAL NOT NULL,
                pickup_location TEXT NOT NULL, pickup_date TEXT NOT NULL, delivery_location TEXT NOT NULL,
                delivery_date TEXT NOT NULL, actual_delivery_date TEXT, loaded_miles INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL, notes TEXT, rate_con_pdf_path TEXT, bol_pdf_path TEXT
            )
        """)
        await conn.execute(
            "INSERT INTO loads (broker_name, load_reference, rate_amount, pickup_location, pickup_date, "
            "delivery_location, delivery_date, status, rate_con_pdf_path) "
            "VALUES ('Acme', 'PO-1', 1000, 'A', '2026-01-01', 'B', '2026-01-02', 'Invoiced', "
            "'static/invoices/invoice_INV-1001.pdf')"
        )
        await conn.commit()

    manager = DatabaseManager(db_path=str(db_path))
    await manager.init_db()
    load = await manager.get_load(1)

    assert load.invoice_pdf_path == "static/invoices/invoice_INV-1001.pdf"
    assert load.rate_con_pdf_path is None


async def legacy_database(tmp_path, monkeypatch, version: int) -> DatabaseManager:
    """A database as it was at schema version N, before the later migrations existed."""
    manager = DatabaseManager(db_path=str(tmp_path / "legacy.db"))
    with monkeypatch.context() as patch:
        patch.setattr(database, "MIGRATIONS", MIGRATIONS[:version])
        await manager.init_db()
    return manager


async def test_migration_adds_deadhead_columns(tmp_path, monkeypatch):
    """Loads saved before deadhead tracking start with no empty miles."""
    manager = await legacy_database(tmp_path, monkeypatch, version=4)
    async with aiosqlite.connect(manager.db_path) as conn:
        await conn.execute(
            "INSERT INTO loads (broker_name, load_reference, rate_amount, pickup_location, pickup_date, "
            "delivery_location, delivery_date, status) "
            "VALUES ('Acme', 'PO-1', 1000, 'A', '2026-01-01', 'B', '2026-01-02', 'Active')"
        )
        await conn.commit()

    await manager.init_db()
    load = await manager.get_load(1)

    assert load.empty_miles == 0
    assert load.deadhead_origin is None


async def test_deadhead_fields_round_trip(db):
    saved = await db.add_load(make_load(empty_miles=85, deadhead_origin="Joliet, IL"))

    fetched = await db.get_load(saved.id)

    assert fetched.empty_miles == 85
    assert fetched.deadhead_origin == "Joliet, IL"
    assert fetched.total_miles == 1085


async def test_payment_fields_round_trip(db):
    saved = await db.add_load(
        make_load(status=LoadStatus.PAID, invoice_number="INV-1001", paid_at=date(2026, 10, 20), amount_paid=2450.0)
    )

    fetched = await db.get_load_by_invoice_number("INV-1001")

    assert fetched.id == saved.id
    assert (fetched.paid_at, fetched.amount_paid) == (date(2026, 10, 20), 2450.0)
    assert await db.get_load_by_invoice_number("INV-9999") is None


async def test_unpaid_invoices_oldest_first(db):
    newer = await db.add_load(make_load(status=LoadStatus.INVOICED, invoiced_at=datetime(2026, 10, 9)))
    older = await db.add_load(make_load(status=LoadStatus.INVOICED, invoiced_at=datetime(2026, 9, 9)))
    await db.add_load(make_load(status=LoadStatus.PAID, invoiced_at=datetime(2026, 8, 9)))

    assert [load.id for load in await db.get_unpaid_invoices()] == [older.id, newer.id]


async def test_charges_round_trip_and_update(db):
    saved = await db.add_load(make_load(charges=[{"description": "Detention", "amount": 150.0}]))

    assert (await db.get_load(saved.id)).total_amount == 2650.0

    updated = await db.update_load(saved.id, {"charges": [*saved.charges, {"description": "Lumper", "amount": 50}]})

    assert [charge.description for charge in updated.charges] == ["Detention", "Lumper"]
    assert updated.total_amount == 2700.0


async def test_migration_gives_existing_loads_no_charges(tmp_path, monkeypatch):
    manager = await legacy_database(tmp_path, monkeypatch, version=6)
    async with aiosqlite.connect(manager.db_path) as conn:
        await conn.execute(
            "INSERT INTO loads (broker_name, load_reference, rate_amount, pickup_location, pickup_date, "
            "delivery_location, delivery_date, status) "
            "VALUES ('Acme', 'PO-1', 1000, 'A', '2026-01-01', 'B', '2026-01-02', 'Active')"
        )
        await conn.commit()

    await manager.init_db()

    assert (await manager.get_load(1)).charges == []


async def test_broker_contacts_newest_first(db):
    await db.add_load(make_load(broker_name="Echo", broker_email=None))
    await db.add_load(make_load(broker_name="TQL", broker_email="loads@tql.example"))

    assert await db.get_broker_contacts() == [("TQL", "loads@tql.example"), ("Echo", None)]


async def test_stops_round_trip_and_search(db):
    stops = [
        {"kind": "pickup", "location": "Waco, TX", "stop_date": "2026-10-02"},
        {"kind": "delivery", "location": "Macon, GA"},
    ]
    saved = await db.add_load(make_load(stops=stops))

    fetched = await db.get_load(saved.id)

    assert [(stop.kind, stop.location, stop.stop_date) for stop in fetched.stops] == [
        ("pickup", "Waco, TX", date(2026, 10, 2)),
        ("delivery", "Macon, GA", None),
    ]
    assert [load.id for load in await db.get_loads(search_query="Macon")] == [saved.id]


async def test_migration_gives_existing_loads_no_stops(tmp_path, monkeypatch):
    manager = await legacy_database(tmp_path, monkeypatch, version=7)
    async with aiosqlite.connect(manager.db_path) as conn:
        await conn.execute(
            "INSERT INTO loads (broker_name, load_reference, rate_amount, pickup_location, pickup_date, "
            "delivery_location, delivery_date, status) "
            "VALUES ('Acme', 'PO-1', 1000, 'A', '2026-01-01', 'B', '2026-01-02', 'Active')"
        )
        await conn.commit()

    await manager.init_db()

    assert (await manager.get_load(1)).stops == []


async def test_add_load_stamps_created_at(db):
    saved = await db.add_load(make_load())

    assert saved.created_at is not None
    assert (await db.get_load(saved.id)).created_at == saved.created_at


async def test_get_earned_loads_only_returns_delivered_and_invoiced(db):
    for status in LoadStatus:
        await db.add_load(make_load(broker_name=status.value, status=status))

    earned = await db.get_earned_loads()

    assert sorted(load.broker_name for load in earned) == ["Delivered", "Invoiced", "Paid"]


async def test_count_loads_by_status(db):
    await db.add_load(make_load(status=LoadStatus.ACTIVE))
    await db.add_load(make_load(status=LoadStatus.ACTIVE))
    await db.add_load(make_load(status=LoadStatus.INVOICED))

    counts = await db.count_loads_by_status()

    assert counts == {
        LoadStatus.DRAFT: 0,
        LoadStatus.ACTIVE: 2,
        LoadStatus.DELIVERED: 0,
        LoadStatus.INVOICED: 1,
        LoadStatus.PAID: 0,
    }


async def test_get_recent_loads_newest_first(db):
    first = await db.add_load(make_load(load_reference="first"))
    second = await db.add_load(make_load(load_reference="second"))

    recent = await db.get_recent_loads(limit=1)

    assert [load.id for load in recent] == [second.id]
    assert first.id != second.id


async def test_migration_moves_gemini_key_to_provider_settings(tmp_path):
    db_path = tmp_path / "legacy.db"
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
        await conn.execute("INSERT INTO settings VALUES ('gemini_api_key', 'AIzaOldKey')")
        await conn.commit()

    manager = DatabaseManager(db_path=str(db_path))
    await manager.init_db()
    settings = await manager.get_all_settings()

    assert "gemini_api_key" not in settings
    assert settings["ai_api_key_gemini"] == "AIzaOldKey"
    assert settings["ai_model_gemini"] == "gemini-3.1-flash-lite"


async def test_fresh_database_has_no_ai_model_preset(db):
    assert await db.get_all_settings() == {}

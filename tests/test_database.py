from datetime import date

import aiosqlite
import pytest

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


async def test_analytics_totals_and_rpm(db):
    today = date.today()
    await db.add_load(make_load(rate_amount=3000.0, loaded_miles=1000, delivery_date=today))
    await db.add_load(make_load(broker_name="Blue Freight", rate_amount=1000.0, loaded_miles=500, delivery_date=today))
    await db.add_load(make_load(rate_amount=9999.0, loaded_miles=1, status=LoadStatus.DRAFT, delivery_date=today))

    analytics = await db.get_analytics_data()

    assert analytics["total_revenue"] == 4000.0
    assert analytics["total_miles"] == 1500
    assert analytics["weekly_revenue"] == 4000.0
    assert analytics["global_rpm"] == pytest.approx(4000 / 1500)
    assert [b["name"] for b in analytics["brokers"]] == ["Acme Logistics", "Blue Freight"]
    assert analytics["brokers"][0]["rpm"] == 3.0


async def test_analytics_with_no_miles_does_not_divide_by_zero(db):
    await db.add_load(make_load(loaded_miles=0))

    analytics = await db.get_analytics_data()

    assert analytics["global_rpm"] == 0.0

import asyncio
import csv
import io
import re
from datetime import date, timedelta

import aiosqlite

from loadpilot import database
from loadpilot.database import MIGRATIONS, DatabaseManager
from loadpilot.models import LoadStatus

from .conftest import make_load

TODAY = date.today()


def add(client, amount="85.50", category="Fuel", spent_on=None, note=""):
    data = {"spent_on": (spent_on or TODAY).isoformat(), "category": category, "amount": amount, "note": note}
    return client.post("/expenses", data=data, follow_redirects=False)


def test_add_and_list_expenses_by_month(client):
    last_month = TODAY.replace(day=1) - timedelta(days=1)

    response = add(client, "412.30", note="Love's, Amarillo TX")
    add(client, "95", "Tolls & scales")
    add(client, "1200", "Insurance", spent_on=last_month)

    assert response.status_code == 303
    assert response.headers["location"] == "/expenses"
    page = client.get("/expenses").text
    assert "Love&#39;s, Amarillo TX" in page
    assert TODAY.strftime("%B %Y") in page and last_month.strftime("%B %Y") in page
    assert "$507.30" in page  # this month's total
    assert page.index(TODAY.strftime("%B %Y")) < page.index(last_month.strftime("%B %Y"))  # newest first
    assert 'href="/expenses"' in client.get("/").text  # in the bottom nav


def test_bad_expenses_are_refused(client):
    assert add(client, amount="0").status_code == 422
    assert add(client, category="Snacks").status_code == 422
    assert "No expenses yet" in client.get("/expenses").text


def test_delete_an_expense(client):
    add(client, "60", "Parking")
    expense_id = re.search(r'action="/expenses/(\d+)/delete"', client.get("/expenses").text).group(1)

    response = client.post(f"/expenses/{expense_id}/delete", follow_redirects=False)

    assert response.headers["location"] == "/expenses"
    assert "No expenses yet" in client.get("/expenses").text
    assert client.post(f"/expenses/{expense_id}/delete").status_code == 404


def test_expenses_csv_for_the_accountant(client):
    add(client, "412.30", note="=cmd|' /C calc'!A0", spent_on=TODAY - timedelta(days=3))
    add(client, "95", "Tolls & scales")

    response = client.get("/expenses/export.csv")
    rows = list(csv.reader(io.StringIO(response.text.removeprefix("﻿"))))

    assert response.headers["content-disposition"] == 'attachment; filename="loadpilot-expenses.csv"'
    assert rows[0] == ["Date", "Category", "Amount", "Note"]
    assert rows[1] == [(TODAY - timedelta(days=3)).isoformat(), "Fuel", "412.30", "'=cmd|' /C calc'!A0"]  # oldest first
    assert rows[2][1:3] == ["Tolls & scales", "95.00"]


def test_analytics_shows_profit_once_expenses_are_logged(client, db_path):
    assert "to see your profit and profit per mile" in client.get("/analytics").text

    load = make_load(
        status=LoadStatus.DELIVERED, actual_delivery_date=TODAY, rate_amount=2500, loaded_miles=900, empty_miles=100
    )
    asyncio.run(DatabaseManager(str(db_path)).add_load(load))
    add(client, "700")
    add(client, "300", "Maintenance & repairs")
    page = client.get("/analytics").text

    assert "Where the Money Went" in page
    assert "$1,000.00" in page  # expenses
    assert "$1,500.00" in page  # profit
    assert "$1.50" in page  # profit per mile, over all 1,000 miles
    assert "Maintenance &amp; repairs" in page


def test_expenses_need_a_login(client):
    client.cookies.clear()

    for path in ("/expenses", "/expenses/export.csv"):
        assert client.get(path, follow_redirects=False).headers["location"].startswith("/login")
    assert add(client).headers["location"].startswith("/login")


async def test_migration_adds_the_expenses_table(tmp_path, monkeypatch):
    manager = DatabaseManager(db_path=str(tmp_path / "legacy.db"))
    with monkeypatch.context() as patch:
        patch.setattr(database, "MIGRATIONS", MIGRATIONS[:8])
        await manager.init_db()

    await manager.init_db()

    async with aiosqlite.connect(manager.db_path) as db:
        async with db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'expenses'") as cursor:
            assert await cursor.fetchone() is not None
    assert await manager.get_expenses() == []

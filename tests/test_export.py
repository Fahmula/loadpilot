import asyncio
import csv
import io
import os
import zipfile
from datetime import date, datetime

from loadpilot.config import BOL_DIR, DATA_DIR, INVOICE_DIR, RATECON_DIR
from loadpilot.database import DatabaseManager
from loadpilot.models import LoadStatus
from loadpilot.routes import export

from .conftest import make_load


def seed(db_path, **overrides):
    return asyncio.run(DatabaseManager(db_path=str(db_path)).add_load(make_load(**overrides)))


def read_csv(response) -> list[dict]:
    assert response.text.startswith("﻿")  # so Excel reads it as UTF-8
    return list(csv.DictReader(io.StringIO(response.text.removeprefix("﻿"))))


def test_loads_csv_has_what_the_accountant_needs(client, db_path):
    seed(
        db_path,
        load_reference="PO-2026",
        status=LoadStatus.PAID,
        actual_delivery_date=date(2026, 10, 3),
        charges=[{"description": "Detention", "amount": 150.0}],
        stops=[{"kind": "delivery", "location": "Macon, GA"}],
        invoice_number="INV-1001",
        invoiced_at=datetime(2026, 10, 4, 9, 30),
        paid_at=date(2026, 10, 30),
        amount_paid=2600.0,
    )
    seed(db_path, load_reference="PO-2025", status=LoadStatus.DELIVERED, actual_delivery_date=date(2025, 12, 30))

    response = client.get("/export/loads.csv")
    rows = read_csv(response)

    assert response.headers["content-disposition"] == 'attachment; filename="loadpilot-loads-all.csv"'
    assert [row["Load #"] for row in rows] == ["PO-2025", "PO-2026"]  # oldest delivery first
    paid = rows[1]
    assert paid["Invoice #"] == "INV-1001"
    assert paid["Status"] == "Paid"
    assert paid["Stops"] == "Delivery: Macon, GA"
    assert (paid["Rate"], paid["Extra charges"], paid["Total billed"]) == ("2500.00", "150.00", "2650.00")
    assert (paid["Delivered on"], paid["Invoiced on"], paid["Paid on"]) == ("2026-10-03", "2026-10-04", "2026-10-30")
    assert paid["Amount paid"] == "2600.00"


def test_loads_csv_for_one_year(client, db_path):
    seed(db_path, load_reference="PO-2026", actual_delivery_date=date(2026, 10, 3), status=LoadStatus.DELIVERED)
    seed(db_path, load_reference="PO-2025", actual_delivery_date=date(2025, 12, 30), status=LoadStatus.DELIVERED)

    response = client.get("/export/loads.csv?year=2025")

    assert [row["Load #"] for row in read_csv(response)] == ["PO-2025"]
    assert response.headers["content-disposition"] == 'attachment; filename="loadpilot-loads-2025.csv"'
    # The Settings card offers the years there are loads for
    page = client.get("/settings").text
    assert "Delivered in 2026" in page and "Delivered in 2025" in page


def test_loads_csv_keeps_formulas_as_text(client, db_path):
    # A scanned rate con decides the broker name; a spreadsheet mustn't run it
    seed(db_path, broker_name='=HYPERLINK("http://evil.example","Open")', notes="-5 for the lumper")

    row = read_csv(client.get("/export/loads.csv"))[0]

    assert row["Broker"] == '\'=HYPERLINK("http://evil.example","Open")'
    assert row["Notes"] == "'-5 for the lumper"


def test_backup_restores_into_an_empty_data_folder(client, db_path, tmp_path, monkeypatch):
    documents = {
        "ratecons/ratecon_backup.pdf": RATECON_DIR / "ratecon_backup.pdf",
        "bols/bol_backup.pdf": BOL_DIR / "bol_backup.pdf",
        "invoices/invoice_INV-backup.pdf": INVOICE_DIR / "invoice_INV-backup.pdf",
    }
    for name, path in documents.items():
        path.write_bytes(f"%PDF {name}".encode())
    pending = RATECON_DIR / f"pending_{'b' * 32}.pdf"
    pending.write_bytes(b"%PDF unclaimed scan")
    load = seed(db_path, load_reference="PO-KEEP", bol_pdf_path="static/bols/bol_backup.pdf")
    temp_files = []
    make_temp = export.tempfile.mkstemp
    monkeypatch.setattr(export.tempfile, "mkstemp", lambda **kw: temp_files.append(make_temp(**kw)) or temp_files[-1])

    response = client.get("/export/backup.zip")

    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].endswith(f'filename="loadpilot-backup-{date.today()}.zip"')
    backup = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(backup.namelist())
    assert {"loadpilot.db", *documents} <= names
    assert not any(n.startswith(("logs/", "pending_", "ratecons/pending_")) or n == "secret_key" for n in names)
    assert (DATA_DIR / "secret_key").exists()  # there to leave out
    assert not any(os.path.exists(path) for _, path in temp_files)  # the zip isn't left behind

    restored = tmp_path / "restored"
    backup.extractall(restored)
    restored_load = asyncio.run(DatabaseManager(str(restored / "loadpilot.db")).get_load(load.id))
    assert restored_load.load_reference == "PO-KEEP"
    assert (restored / "bols/bol_backup.pdf").read_bytes() == b"%PDF bols/bol_backup.pdf"


def test_exports_need_a_login(client):
    client.cookies.clear()

    for path in ("/export/loads.csv", "/export/backup.zip"):
        assert client.get(path, follow_redirects=False).headers["location"].startswith("/login")

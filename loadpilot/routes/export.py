"""Getting data out: a loads spreadsheet for the accountant, and a backup of everything LoadPilot keeps."""

import asyncio
import csv
import io
import os
import tempfile
import zipfile
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from loadpilot.analytics import earned_on
from loadpilot.config import BOL_DIR, INVOICE_DIR, RATECON_DIR
from loadpilot.documents import PENDING_NAME
from loadpilot.models import Load

router = APIRouter(prefix="/export", tags=["Export"])

CSV_COLUMNS = [
    ("Invoice #", lambda load: load.invoice_number),
    ("Load #", lambda load: load.load_reference),
    ("Broker", lambda load: load.broker_name),
    ("Broker email", lambda load: load.broker_email),
    ("Status", lambda load: load.status.value),
    ("Pickup", lambda load: load.pickup_location),
    ("Pickup date", lambda load: load.pickup_date),
    ("Stops", lambda load: "; ".join(f"{stop.kind.value.title()}: {stop.location}" for stop in load.stops)),
    ("Delivery", lambda load: load.delivery_location),
    ("Delivery date", lambda load: load.delivery_date),
    ("Delivered on", lambda load: load.actual_delivery_date),
    ("Loaded miles", lambda load: load.loaded_miles),
    ("Empty miles", lambda load: load.empty_miles),
    ("Rate", lambda load: f"{load.rate_amount:.2f}"),
    ("Extra charges", lambda load: f"{load.total_amount - load.rate_amount:.2f}"),
    ("Total billed", lambda load: f"{load.total_amount:.2f}"),
    ("Invoiced on", lambda load: load.invoiced_at.date() if load.invoiced_at else None),
    ("Paid on", lambda load: load.paid_at),
    ("Amount paid", lambda load: f"{load.amount_paid:.2f}" if load.amount_paid is not None else None),
    ("Notes", lambda load: load.notes),
]

# A spreadsheet runs a cell starting with one of these as a formula. Broker names and notes can come from a
# scanned rate con, so they're kept as text.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def as_text(value) -> str:
    text = "" if value is None else str(value)
    return "'" + text if isinstance(value, str) and text.startswith(FORMULA_STARTS) else text


def loads_csv(loads: list[Load]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([name for name, _ in CSV_COLUMNS])
    for load in loads:
        writer.writerow([as_text(value(load)) for _, value in CSV_COLUMNS])
    return "﻿" + out.getvalue()  # the byte order mark makes Excel read it as UTF-8


@router.get("/loads.csv")
async def export_loads(request: Request, year: int | None = None):
    """Every load, or those delivered in one year (the day it counts toward earnings, as in Analytics)."""
    loads = await request.app.state.db.get_all_loads()
    if year:
        loads = [load for load in loads if earned_on(load).year == year]
    filename = f"loadpilot-loads-{year or 'all'}.csv"
    return Response(
        loads_csv(loads),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def write_backup(db, path: str):
    """The database and every rate con, BOL and invoice, as a zip that unzips straight into the data folder.

    Logs, unclaimed rate con scans and the session secret stay out; a restored LoadPilot makes a new secret,
    so restoring signs every device out.
    """
    with tempfile.TemporaryDirectory() as scratch:
        snapshot = os.path.join(scratch, "loadpilot.db")
        db.snapshot(snapshot)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as backup:
            backup.write(snapshot, "loadpilot.db")
            for folder in (RATECON_DIR, BOL_DIR, INVOICE_DIR):
                for document in sorted(folder.iterdir()):
                    if document.is_file() and not PENDING_NAME.match(document.name):
                        backup.write(document, f"{folder.name}/{document.name}")


@router.get("/backup.zip")
async def download_backup(request: Request):
    handle, path = tempfile.mkstemp(suffix=".zip")
    os.close(handle)
    await asyncio.to_thread(write_backup, request.app.state.db, path)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"loadpilot-backup-{date.today().isoformat()}.zip",
        background=BackgroundTask(os.unlink, path),
    )

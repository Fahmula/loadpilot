import os
from pathlib import Path

# Everything LoadPilot persists (database, logs, rate cons, uploaded BOLs, generated invoices)
# lives under one directory. Docker mounts it at /config; override it to run locally.
DATA_DIR = Path(os.getenv("LOADPILOT_DATA_DIR", "/config"))

LOG_DIR = DATA_DIR / "logs"
BOL_DIR = DATA_DIR / "bols"
RATECON_DIR = DATA_DIR / "ratecons"
INVOICE_DIR = DATA_DIR / "invoices"
DB_PATH = DATA_DIR / "loadpilot.db"


def ensure_data_dirs():
    for directory in (LOG_DIR, BOL_DIR, RATECON_DIR, INVOICE_DIR):
        directory.mkdir(parents=True, exist_ok=True)

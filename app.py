import logging
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from loadpilot import __version__
from loadpilot.config import BOL_DIR, DB_PATH, INVOICE_DIR, LOG_DIR, ensure_data_dirs
from loadpilot.database import DatabaseManager
from loadpilot.routes import api, ui

ensure_data_dirs()

# ---------------------------------------------------------
# LOGGING CONFIGURATION
# ---------------------------------------------------------

formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

info_file_handler = TimedRotatingFileHandler(
    LOG_DIR / "loadpilot.log", when="midnight", interval=1, backupCount=7, encoding="utf-8"
)
info_file_handler.setLevel(logging.INFO)
info_file_handler.setFormatter(formatter)

debug_file_handler = TimedRotatingFileHandler(
    LOG_DIR / "loadpilot.debug.log", when="midnight", interval=1, backupCount=3, encoding="utf-8"
)
debug_file_handler.setLevel(logging.DEBUG)
debug_file_handler.setFormatter(formatter)

logger = logging.getLogger("loadpilot")
logger.setLevel(logging.DEBUG)
logger.propagate = False

if not logger.handlers:
    logger.addHandler(console_handler)
    logger.addHandler(info_file_handler)
    logger.addHandler(debug_file_handler)

app_logger = logging.getLogger("loadpilot.app")

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = DatabaseManager(db_path=str(DB_PATH))
    await db.init_db()
    app.state.db = db

    app_logger.info(f"LoadPilot {__version__} started successfully.")
    yield
    app_logger.info("Shutting down LoadPilot...")


app = FastAPI(title="LoadPilot API", version=__version__, lifespan=lifespan)

app.mount("/static/bols", StaticFiles(directory=BOL_DIR), name="bols")
app.mount("/static/invoices", StaticFiles(directory=INVOICE_DIR), name="invoices")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(api.router)
app.include_router(ui.router)


@app.get("/manifest.json", include_in_schema=False)
async def manifest():
    return FileResponse(STATIC_DIR / "manifest.json")


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(STATIC_DIR / "sw.js")


@app.get("/health")
def health_check():
    return {"status": "System is running smooth driver.", "version": __version__}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)

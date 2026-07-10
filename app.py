import logging
from pathlib import Path
from logging.handlers import TimedRotatingFileHandler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager

from loadpilot.database import DatabaseManager
from loadpilot.routes import api, ui

# ---------------------------------------------------------
# LOGGING CONFIGURATION
# ---------------------------------------------------------
LOG_DIR = "/config/logs"
Path(LOG_DIR).mkdir(parents=True, exist_ok=True)

formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)

info_file_handler = TimedRotatingFileHandler(
    Path(LOG_DIR) / "loadpilot.log",
    when="midnight",
    interval=1,
    backupCount=7,
    encoding="utf-8"
)
info_file_handler.setLevel(logging.INFO)
info_file_handler.setFormatter(formatter)

debug_file_handler = TimedRotatingFileHandler(
    Path(LOG_DIR) / "loadpilot.debug.log",
    when="midnight",
    interval=1,
    backupCount=3,
    encoding="utf-8"
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

Path("/config/bols").mkdir(parents=True, exist_ok=True)
Path("/config/invoices").mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = "/config/loadpilot.db"
    db = DatabaseManager(db_path=db_path)
    await db.init_db()
    app.state.db = db

    app_logger.info("LoadPilot started successfully.")
    yield
    app_logger.info("Shutting down LoadPilot...")

app = FastAPI(title="LoadPilot API", lifespan=lifespan)

app.mount("/static/bols", StaticFiles(directory="/config/bols"), name="bols")
app.mount("/static/invoices", StaticFiles(directory="/config/invoices"), name="invoices")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(api.router)
app.include_router(ui.router)

@app.get("/manifest.json", include_in_schema=False)
async def manifest():
    return FileResponse("static/manifest.json")

@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse("static/sw.js")

@app.get("/health")
def health_check():
    return {"status": "System is running smooth driver."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
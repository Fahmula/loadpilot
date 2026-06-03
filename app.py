# app.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from database import engine, Base
from routers import loads, views

# Create the database tables if they don't exist
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="LoadPilot API",
    description="Backend for Owner-Operator Trucking Management",
    version="1.0.0"
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Register the Loads API router
app.include_router(loads.router)
app.include_router(views.router)


@app.get("/health")
def health_check():
    return {"status": "System is running smooth driver."}
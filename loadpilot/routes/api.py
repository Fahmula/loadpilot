import pypdf
import asyncio
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from loadpilot.models import Load, LoadStatus

router = APIRouter(prefix="/api/loads", tags=["Loads"])

@router.post("/", response_model=Load)
async def create_load(load: Load, request: Request):
    db = request.app.state.db
    return await db.add_load(load)

@router.get("/", response_model=list[Load])
async def read_loads(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    status: LoadStatus | None = None
):
    db = request.app.state.db
    return await db.get_loads(skip=skip, limit=limit, status=status)

@router.get("/{load_id}", response_model=Load)
async def read_load(load_id: int, request: Request):
    db = request.app.state.db
    db_load = await db.get_load(load_id)
    if not db_load:
        raise HTTPException(status_code=404, detail="Load not found")
    return db_load

@router.put("/{load_id}", response_model=Load)
async def update_load(load_id: int, load_update: dict, request: Request):
    db = request.app.state.db
    db_load = await db.update_load(load_id, load_update)
    if not db_load:
        raise HTTPException(status_code=404, detail="Load not found")
    return db_load

@router.delete("/{load_id}")
async def delete_load(load_id: int, request: Request):
    db = request.app.state.db
    success = await db.delete_load(load_id)
    if not success:
        raise HTTPException(status_code=404, detail="Load not found")
    return {"message": "Load deleted successfully"}
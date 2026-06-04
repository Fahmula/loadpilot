# crud.py
from sqlalchemy.orm import Session
import models
import schemas

# ----------------- READ -----------------

def get_load(db: Session, load_id: int):
    """Fetch a single load by its ID."""
    return db.query(models.Load).filter(models.Load.id == load_id).first()

def get_loads(db: Session, skip: int = 0, limit: int = 100, status: models.LoadStatus = None):
    """
    Fetch a list of loads. 
    If a status is provided (e.g., LoadStatus.ACTIVE), it filters the list.
    This is perfect for feeding different tabs on your dashboard.
    """
    query = db.query(models.Load)
    if status:
        query = query.filter(models.Load.status == status)
    
    # Order by pickup date descending (newest first)
    return query.order_by(models.Load.pickup_date.desc()).offset(skip).limit(limit).all()

def get_settings(db: Session):
    """Get the company settings (always ID 1). If it doesn't exist, create it."""
    settings = db.query(models.CompanySettings).filter(models.CompanySettings.id == 1).first()
    if not settings:
        settings = models.CompanySettings(id=1, company_name="My Trucking Co")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


# ----------------- CREATE -----------------

def create_load(db: Session, load: schemas.LoadCreate):
    """Create a new load in the database."""
    # Convert the Pydantic schema into a SQLAlchemy model dictionary
    db_load = models.Load(**load.model_dump())
    
    db.add(db_load)
    db.commit()
    db.refresh(db_load) # Refreshes to get the auto-generated ID
    return db_load


# ----------------- UPDATE -----------------

def update_load(db: Session, load_id: int, load_update: schemas.LoadUpdate):
    """
    Update a load. Usually used to change status (e.g., ACTIVE -> DELIVERED),
    or to attach PDF file paths once you upload a BOL.
    """
    db_load = get_load(db, load_id)
    if not db_load:
        return None

    # Only update fields that were actually provided in the request
    update_data = load_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_load, key, value)

    db.commit()
    db.refresh(db_load)
    return db_load

def update_settings(db: Session, settings_data: schemas.SettingsUpdate):
    settings = get_settings(db)
    for key, value in settings_data.model_dump().items():
        setattr(settings, key, value)
    db.commit()
    db.refresh(settings)
    return settings


# ----------------- DELETE -----------------

def delete_load(db: Session, load_id: int):
    """Delete a load (in case it cancels or is a duplicate)."""
    db_load = get_load(db, load_id)
    if db_load:
        db.delete(db_load)
        db.commit()
        return True
    return False
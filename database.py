from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker

# Using SQLite for simple, file-based storage
SQLALCHEMY_DATABASE_URL = "sqlite:///./loadpilot.db"

# connect_args is needed only for SQLite in FastAPI
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get the DB session in our routers
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
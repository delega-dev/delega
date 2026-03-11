"""Delega database configuration."""
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Default database path in the data directory for easy backup
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "delega.db")

DB_PATH = os.environ.get("DELEGA_DB_PATH", DEFAULT_DB_PATH)
SQLALCHEMY_DATABASE_URL = os.environ.get("DELEGA_DATABASE_URL", f"sqlite:///{DB_PATH}")

# Ensure sqlite directory exists when using file-based sqlite URL
if SQLALCHEMY_DATABASE_URL.startswith("sqlite:///"):
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency for FastAPI routes"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

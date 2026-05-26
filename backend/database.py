"""Delega database configuration."""
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

# Default database path in the data directory for easy backup
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "delega.db")

DB_PATH = os.environ.get("DELEGA_DB_PATH", DEFAULT_DB_PATH)
SQLALCHEMY_DATABASE_URL = os.environ.get("DELEGA_DATABASE_URL", f"sqlite:///{DB_PATH}")


def sqlite_file_path(database_url: str) -> str | None:
    """Return the filesystem path for file-based SQLite URLs."""
    if not database_url.startswith("sqlite"):
        return None
    database = make_url(database_url).database
    if not database or database == ":memory:":
        return None
    return database


def enable_sqlite_foreign_keys(engine: Engine) -> None:
    """Enable SQLite foreign-key and ON DELETE behavior for every connection."""
    if not str(engine.url).startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


# Ensure sqlite directory exists when using file-based sqlite URL
sqlite_path = sqlite_file_path(SQLALCHEMY_DATABASE_URL)
if sqlite_path:
    db_dir = os.path.dirname(os.path.abspath(sqlite_path))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {},
)
enable_sqlite_foreign_keys(engine)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Dependency for FastAPI routes"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

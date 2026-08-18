"""SQLAlchemy engine, session factory and declarative Base.

SQLite is fine to start (the reference deployment ran 280k messages and 50k
contacts on it). Switch to PostgreSQL by changing DATABASE_URL — the pool
settings below activate automatically.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.core.config import settings
import os

os.makedirs("data", exist_ok=True)

is_sqlite = settings.DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if is_sqlite else {}
engine_kwargs = {} if is_sqlite else {
    "pool_pre_ping": True,   # drop stale connections instead of erroring
    "pool_size": 10,
    "max_overflow": 20,
}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, **engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

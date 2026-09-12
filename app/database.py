import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

# Make sure ./data exists for the default SQLite file location.
if settings.DATABASE_URL.startswith("sqlite"):
    os.makedirs("data", exist_ok=True)

def _normalise(url: str) -> str:
    """Accept the connection string hosted Postgres providers hand you verbatim.

    Neon/Render/Heroku all print a URL starting with "postgres://" or
    "postgresql://". SQLAlchemy needs an explicit driver, so add one rather
    than making anyone hand-edit the string they pasted from a dashboard.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _normalise(settings.DATABASE_URL)

if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}
    engine_kwargs = {}
else:
    # Serverless Postgres (Neon) drops connections that have been idle, and
    # a free web host parks the app between bursts of traffic — exactly the
    # combination that hands you a dead connection out of the pool. Check
    # each one before use and retire them well inside the idle timeout.
    connect_args = {"connect_timeout": 10}
    engine_kwargs = {"pool_pre_ping": True, "pool_recycle": 280, "pool_size": 5, "max_overflow": 2}

engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

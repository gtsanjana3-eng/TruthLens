"""SQLAlchemy engine/session management (PostgreSQL in production, SQLite for local dev/tests)."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    elif url.startswith("postgresql"):
        port = make_url(url).port
        # Supabase poolers drop idle connections; keep the pool small and recycle it.
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
        connect_args: dict = {"connect_timeout": 15, "application_name": "truthlens-backend"}
        if port == 6543:  # Supabase *transaction* pooler cannot keep server-side prepared statements
            connect_args["prepare_threshold"] = None
        kwargs["connect_args"] = connect_args
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _fk_on(dbapi_conn, _):  # enforce FK constraints in SQLite
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
    return eng


engine = _make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def configure_engine(url: str) -> None:
    """Rebind the global engine (used by tests)."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def init_schema(eng: Engine | None = None) -> None:
    """Create any missing tables (idempotent). On PostgreSQL / Supabase also lock every table down with
    row-level security: Supabase publishes the `public` schema through its REST API, and RLS without
    policies means the anon / authenticated API keys can read nothing. The backend connects as the
    database owner, which is not affected by RLS."""
    eng = eng or engine
    Base.metadata.create_all(eng)
    if eng.dialect.name == "postgresql":
        existing = set(inspect(eng).get_table_names(schema="public"))
        with eng.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if table.name in existing:
                    conn.execute(text(f'ALTER TABLE public."{table.name}" ENABLE ROW LEVEL SECURITY'))


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

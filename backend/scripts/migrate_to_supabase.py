"""Copy every TruthLens record from the local SQLite file into Supabase (PostgreSQL).

Usage (from backend/, after putting your Supabase connection string in backend/.env as DATABASE_URL):

    .venv\\Scripts\\python -m scripts.migrate_to_supabase            # dry run: shows what would be copied
    .venv\\Scripts\\python -m scripts.migrate_to_supabase --yes      # copy

What it does
  1. creates the tables in Supabase (and enables row-level security on them),
  2. refuses to run if those tables already contain data (no duplicates, nothing overwritten),
  3. copies rows table by table in foreign-key order, inside one transaction (all or nothing),
  4. moves the id sequences past the copied ids, and
  5. checks the row counts and the tamper-evident audit hash chain on the Supabase side.

The SQLite file is only read, never modified. Evidence files stay in backend/data (only database rows move).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Integer, create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.audit import verify_chain
from app.config import BACKEND_DIR, get_settings, normalise_database_url
from app.database import Base, _make_engine, init_schema

DEFAULT_SOURCE = f"sqlite:///{(BACKEND_DIR / 'data' / 'truthlens.db').as_posix()}"
BATCH = 500


def _utc(row: dict) -> dict:
    # SQLite drops the time zone; every timestamp this app writes is UTC.
    return {k: (v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v) for k, v in row.items()}


def _counts(eng: Engine) -> dict[str, int]:
    existing = set(inspect(eng).get_table_names())
    with eng.connect() as c:
        return {t.name: (c.scalar(select(func.count()).select_from(t)) or 0) if t.name in existing else 0 for t in Base.metadata.sorted_tables}


def migrate(source_url: str, target_url: str, apply: bool, out=print) -> int:
    source_url, target_url = normalise_database_url(source_url), normalise_database_url(target_url)
    if make_url(source_url) == make_url(target_url):
        out("Source and target are the same database - nothing to do.")
        return 2
    src_path = make_url(source_url).database
    if source_url.startswith("sqlite") and src_path and not Path(src_path).exists():
        out(f"Source database not found: {src_path}")
        return 2

    src = create_engine(source_url)
    dst = _make_engine(target_url)
    host = make_url(target_url).host or make_url(target_url).database
    out(f"Source: {make_url(source_url).render_as_string(hide_password=True)}")
    out(f"Target: {make_url(target_url).render_as_string(hide_password=True)}")

    src_counts = _counts(src)
    out("\nRows to copy:")
    for name, n in src_counts.items():
        out(f"  {name:<28} {n}")
    out(f"  {'TOTAL':<28} {sum(src_counts.values())}")

    if not apply:
        out(f"\nDry run only. Re-run with --yes to create the tables on {host} and copy the rows.")
        return 0

    out("\nCreating tables on the target (row-level security enabled on PostgreSQL)...")
    init_schema(dst)
    dst_before = _counts(dst)
    if any(dst_before.values()):
        busy = {k: v for k, v in dst_before.items() if v}
        out(f"Target already contains data {busy} - refusing to copy so nothing is duplicated or overwritten.")
        return 3

    with src.connect() as s_conn, dst.begin() as d_conn:  # one transaction: all rows or none
        for table in Base.metadata.sorted_tables:
            total = 0
            batch: list[dict] = []
            for row in s_conn.execute(select(table)).mappings():
                batch.append(_utc(dict(row)))
                if len(batch) >= BATCH:
                    d_conn.execute(table.insert(), batch)
                    total += len(batch)
                    batch = []
            if batch:
                d_conn.execute(table.insert(), batch)
                total += len(batch)
            out(f"  copied {table.name:<28} {total}")
        if dst.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and isinstance(pk[0].type, Integer):
                    col = pk[0].name
                    d_conn.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('public.\"{table.name}\"', '{col}'), "
                        f"COALESCE((SELECT MAX(\"{col}\") FROM public.\"{table.name}\"), 1), "
                        f"(SELECT MAX(\"{col}\") FROM public.\"{table.name}\") IS NOT NULL)"))

    dst_after = _counts(dst)
    mismatched = {k: (src_counts[k], dst_after[k]) for k in src_counts if src_counts[k] != dst_after[k]}
    with Session(dst) as db:
        chain = verify_chain(db)
    out("\nVerification:")
    out(f"  row counts match: {'yes' if not mismatched else f'NO {mismatched}'}")
    chain_state = "intact" if chain["valid"] else f"BROKEN at entry {chain['broken_at']}"
    out(f"  audit hash chain: {chain_state} ({chain['entries_checked']} entries)")
    if mismatched or not chain["valid"]:
        return 4
    out("\nDone. Restart the backend - it now reads and writes Supabase.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=DEFAULT_SOURCE, help="SQLite database to read (default: backend/data/truthlens.db)")
    ap.add_argument("--target", default=None, help="target database URL (default: DATABASE_URL from backend/.env)")
    ap.add_argument("--yes", action="store_true", help="actually copy (without it: dry run)")
    a = ap.parse_args(argv)
    target = a.target or get_settings().database_url
    if not target.startswith("postgresql") and a.target is None:
        print("DATABASE_URL in backend/.env is not a PostgreSQL/Supabase connection string yet.\n"
              "Add it first (Supabase dashboard → Connect → Session pooler), then run this again.")
        return 2
    return migrate(a.source, target, a.yes)


if __name__ == "__main__":
    sys.exit(main())

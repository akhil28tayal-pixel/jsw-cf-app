#!/usr/bin/env python
"""
Copy this machine's SQLite database into a Postgres database, once.

    python scripts/push_to_postgres.py \
        --source data/jsw_cf.db \
        --target "postgresql://user:pass@host/db?sslmode=require"

What it does, in order:
  1. Builds the schema in the target from the models (create_all) — the same
     schema a fresh app boot would build. It does NOT run app/migrations.py,
     which is SQLite-only legacy-upgrade code.
  2. Copies every table in foreign-key-safe order, preserving row ids, so
     every existing reference between tables still points at the same row.
  3. Resets each table's id sequence past the highest id copied — without
     this, the first row Postgres inserts afterwards reuses id 1 and fails
     on the primary key.

Reading and writing both go through SQLAlchemy, so each column is converted
by the same type the app uses: SQLite's 0/1 becomes a real Postgres boolean,
its date/datetime strings become real dates.

Safe to re-run: --wipe clears the target tables first. Without --wipe it
refuses to touch a target that already has rows, rather than doubling them.
"""
import argparse
import os
import sys

# Import the app's models without letting app.config read a .env that points
# somewhere else — the engine below is built explicitly, from the arguments.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select, insert, text, inspect  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402


def normalise(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="data/jsw_cf.db", help="path to the SQLite file (default: data/jsw_cf.db)")
    ap.add_argument("--target", default=os.environ.get("TARGET_DATABASE_URL"),
                    help="Postgres URL, or set TARGET_DATABASE_URL")
    ap.add_argument("--wipe", action="store_true", help="delete all rows in the target first")
    ap.add_argument("--dry-run", action="store_true", help="report what would be copied, change nothing")
    args = ap.parse_args()

    if not args.target:
        ap.error("no target given — pass --target or set TARGET_DATABASE_URL")
    if not os.path.exists(args.source):
        ap.error(f"source database not found: {args.source}")

    # app.database builds its own engine from .env on import; that engine is
    # unused here but must not blow up, so point it at the source file.
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{args.source}")
    from app.database import Base  # noqa: E402
    from app import models  # noqa: F401,E402  (registers every table on Base)

    src = create_engine(f"sqlite:///{args.source}")
    dst = create_engine(normalise(args.target))

    tables = Base.metadata.sorted_tables  # parents before children
    src_tables = set(inspect(src).get_table_names())

    print(f"source : {args.source}")
    print(f"target : {dst.url.render_as_string(hide_password=True)}")
    print()

    # ---- read everything out of SQLite first -----------------------------
    payload = {}
    for table in tables:
        if table.name not in src_tables:
            print(f"  {table.name:<20} not in source, skipped")
            continue
        with src.connect() as conn:
            rows = [dict(r) for r in conn.execute(select(table)).mappings()]
        payload[table.name] = rows
        print(f"  {table.name:<20} {len(rows):>6} rows")

    total = sum(len(r) for r in payload.values())
    print(f"\n  {'total':<20} {total:>6} rows")

    if args.dry_run:
        print("\ndry run — nothing written.")
        return 0

    # ---- build the schema ------------------------------------------------
    print("\nCreating tables in the target...")
    Base.metadata.create_all(bind=dst)

    with Session(dst) as session:
        # ---- refuse to double up unless told to wipe ---------------------
        existing = {}
        for table in tables:
            n = session.execute(text(f'SELECT count(*) FROM "{table.name}"')).scalar_one()
            if n:
                existing[table.name] = n
        if existing and not args.wipe:
            print("\nThe target already has data:")
            for name, n in existing.items():
                print(f"  {name:<20} {n:>6} rows")
            print("\nRefusing to copy on top of it. Re-run with --wipe to replace it.")
            return 1

        if args.wipe and existing:
            print("\nClearing the target...")
            for table in reversed(tables):  # children before parents
                session.execute(text(f'DELETE FROM "{table.name}"'))
            session.commit()

        # ---- copy --------------------------------------------------------
        print("\nCopying...")
        for table in tables:
            rows = payload.get(table.name)
            if not rows:
                continue
            # chunked, so a big table doesn't build one enormous statement
            for i in range(0, len(rows), 500):
                session.execute(insert(table), rows[i:i + 500])
            print(f"  {table.name:<20} {len(rows):>6} rows")
        session.commit()

        # ---- move the id sequences past what we just inserted ------------
        print("\nResetting id sequences...")
        for table in tables:
            pk = list(table.primary_key.columns)
            if len(pk) != 1 or not pk[0].autoincrement:
                continue
            col = pk[0].name
            seq = session.execute(
                text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table.name, "c": col}
            ).scalar()
            if not seq:
                continue
            session.execute(text(
                f'SELECT setval(:seq, COALESCE((SELECT MAX("{col}") FROM "{table.name}"), 0) + 1, false)'
            ), {"seq": seq})
            print(f"  {table.name:<20} ok")
        session.commit()

    # ---- verify ----------------------------------------------------------
    print("\nVerifying row counts...")
    ok = True
    with Session(dst) as session:
        for name, rows in payload.items():
            n = session.execute(text(f'SELECT count(*) FROM "{name}"')).scalar_one()
            mark = "ok" if n == len(rows) else f"MISMATCH (source had {len(rows)})"
            if n != len(rows):
                ok = False
            print(f"  {name:<20} {n:>6} rows  {mark}")

    print("\nDone." if ok else "\nFinished with mismatches — check the table(s) above.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Migrates an existing (pre-godown) database to the multi-godown schema, in
place, without losing any data. Safe to run on:
  - a brand-new database (nothing exists yet — everything is skipped here
    and left to Base.metadata.create_all() to build fresh with the correct
    schema from the start)
  - an already-migrated database (every check below is a no-op)
  - your real, existing single-godown database (the actual target: adds a
    godown_id column to grn/dispatch/billing/import_log, and rebuilds
    opening_stock/freight_rate_card — which need their UNIQUE constraints
    changed, something SQLite can't do with a plain ALTER TABLE — then
    backfills every existing row to point at a new "Manesar Godown", since
    that's what all of it always was.

This must run BEFORE Base.metadata.create_all(), so it only ever touches
tables that already exist with the old shape; genuinely new tables are left
alone for create_all() to create correctly afterward.
"""
import datetime as dt
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger("jsw_cf_app")

DEFAULT_GODOWN_NAME = "Manesar Godown"
SECOND_GODOWN_NAME = "Daultabad Godown"

# tables that just need a plain "ADD COLUMN godown_id" + backfill
SIMPLE_ADD_COLUMN_TABLES = ["grn", "dispatch", "billing", "import_log"]

# tables whose UNIQUE constraint changes shape, so they need a full
# rename -> recreate -> copy -> drop instead of a plain ADD COLUMN
REBUILD_TABLES = {
    "opening_stock": {
        "columns": "id, godown_id, product_id, bags, as_of_date",
        "old_columns": "id, product_id, bags, as_of_date",
        "create_sql": """
            CREATE TABLE opening_stock (
                id INTEGER NOT NULL PRIMARY KEY,
                godown_id INTEGER NOT NULL REFERENCES godowns(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                bags FLOAT NOT NULL,
                as_of_date DATE NOT NULL,
                UNIQUE (godown_id, product_id)
            )
        """,
    },
    "freight_rate_card": {
        "columns": "id, godown_id, district, pincode, transporter_rate_per_mt, company_claim_rate_per_mt, remarks",
        "old_columns": "id, district, pincode, transporter_rate_per_mt, company_claim_rate_per_mt, remarks",
        "create_sql": """
            CREATE TABLE freight_rate_card (
                id INTEGER NOT NULL PRIMARY KEY,
                godown_id INTEGER NOT NULL REFERENCES godowns(id),
                district VARCHAR(80) NOT NULL,
                pincode VARCHAR(10),
                transporter_rate_per_mt FLOAT NOT NULL,
                company_claim_rate_per_mt FLOAT NOT NULL,
                remarks TEXT,
                UNIQUE (godown_id, district, pincode)
            )
        """,
    },
}


def _table_exists(conn, name: str) -> bool:
    return inspect(conn).has_table(name)


def _column_exists(conn, table: str, column: str) -> bool:
    cols = [c["name"] for c in inspect(conn).get_columns(table)]
    return column in cols


def run_migrations(engine: Engine) -> None:
    # This whole module exists to upgrade an *existing* pre-godown SQLite
    # file in place, and it does that with raw SQLite-flavoured DDL
    # (DATETIME columns, "INTEGER PRIMARY KEY" as an autoincrement,
    # rename-recreate-copy to change a UNIQUE constraint). None of that is
    # valid on Postgres, and none of it is needed there either: a Postgres
    # database is always created fresh from the models by create_all(),
    # with the current schema from the start. So: bail out on anything
    # that isn't SQLite.
    if engine.dialect.name != "sqlite":
        logger.info("Not SQLite (%s) — skipping the legacy in-place migration.", engine.dialect.name)
        return

    with engine.begin() as conn:
        # ---- Step 1: make sure the godowns table + the two known godowns exist ----
        if not _table_exists(conn, "godowns"):
            conn.execute(text("""
                CREATE TABLE godowns (
                    id INTEGER NOT NULL PRIMARY KEY,
                    name VARCHAR(120) NOT NULL UNIQUE,
                    active BOOLEAN,
                    created_at DATETIME
                )
            """))
            logger.info("Created godowns table.")

        existing_names = {row[0] for row in conn.execute(text("SELECT name FROM godowns"))}
        for name in (DEFAULT_GODOWN_NAME, SECOND_GODOWN_NAME):
            if name not in existing_names:
                conn.execute(
                    text("INSERT INTO godowns (name, active, created_at) VALUES (:n, 1, CURRENT_TIMESTAMP)"),
                    {"n": name},
                )
                logger.info("Created godown: %s", name)

        manesar_id = conn.execute(
            text("SELECT id FROM godowns WHERE name = :n"), {"n": DEFAULT_GODOWN_NAME}
        ).scalar()

        # ---- Step 2: simple tables — add the column, backfill existing rows ----
        for table in SIMPLE_ADD_COLUMN_TABLES:
            if not _table_exists(conn, table):
                continue  # brand-new DB — create_all() will build this correctly
            if _column_exists(conn, table, "godown_id"):
                continue  # already migrated
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN godown_id INTEGER REFERENCES godowns(id)"))
            conn.execute(
                text(f"UPDATE {table} SET godown_id = :gid WHERE godown_id IS NULL"),
                {"gid": manesar_id},
            )
            logger.info("Migrated table '%s' to include godown_id (backfilled to %s).", table, DEFAULT_GODOWN_NAME)

        # ---- Step 3: tables needing a full rebuild (UNIQUE constraint changed) ----
        for table, spec in REBUILD_TABLES.items():
            if not _table_exists(conn, table):
                continue  # brand-new DB — create_all() will build this correctly
            if _column_exists(conn, table, "godown_id"):
                continue  # already migrated

            conn.execute(text(f"ALTER TABLE {table} RENAME TO {table}_old"))
            conn.execute(text(spec["create_sql"]))
            conn.execute(text(
                f"INSERT INTO {table} ({spec['columns']}) "
                f"SELECT id, {manesar_id}, {spec['old_columns'].split(', ', 1)[1]} FROM {table}_old"
            ))
            conn.execute(text(f"DROP TABLE {table}_old"))
            logger.info("Rebuilt table '%s' with godown_id (backfilled to %s).", table, DEFAULT_GODOWN_NAME)

        # ---- Step 4: per-product bag weight ----
        # Not every product is a 50 kg bag. Microfine is packed at 20 kg, so
        # 1 MT is 50 bags of it. Existing rows stay NULL (= use the global
        # 50 kg setting), except Microfine, which is set here so an existing
        # database gets the right answer without anyone having to remember.
        if _table_exists(conn, "products") and not _column_exists(conn, "products", "bag_weight_mt"):
            conn.execute(text("ALTER TABLE products ADD COLUMN bag_weight_mt FLOAT"))
            conn.execute(text(
                "UPDATE products SET bag_weight_mt = 0.02 WHERE UPPER(name) LIKE '%MICROFINE%'"
            ))
            logger.info("Added per-product bag_weight_mt (Microfine products set to a 20 kg bag).")


# ---------------------------------------------------------------------------
# Link existing GRN / Billing / SAP-stock rows to the import that created them
#
# Unlike run_migrations above, this one runs on EVERY dialect. It has to: the
# column is needed on Postgres too, and create_all() only ever creates missing
# TABLES — it will not add a column to a table that already exists.
#
# "ALTER TABLE ... ADD COLUMN <nullable>" is the one piece of DDL SQLite and
# Postgres agree on, which is why the column is a plain integer with no
# constraint added after the fact; new databases get the real foreign key from
# the model.
# ---------------------------------------------------------------------------
LINKED_TABLES = {
    "grn": "material_in",
    "billing": "sale",
    "sap_stock_snapshot": "sap_stock",
}


def _as_datetime(value):
    """SQLite hands back a string for a DATETIME column, Postgres a datetime."""
    if value is None or isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None


def link_imports_to_their_rows(engine: Engine) -> None:
    """Add import_log_id where missing, then backfill it for existing rows.

    Backfill rule: a row belongs to the FIRST import of its own type and godown
    whose log was written at or after the row was created. That is exactly the
    order the importer works in — every row is committed, then the log is
    written — so the next log after a row is the one that made it.

    Nothing here invents a link: a row with no later log of the right type
    simply stays NULL and is reported as unlinked rather than guessed at.
    """
    inspector = inspect(engine)
    if not inspector.has_table("import_log"):
        return

    for table in LINKED_TABLES:
        if not inspector.has_table(table):
            continue
        columns = [c["name"] for c in inspector.get_columns(table)]
        if "import_log_id" not in columns:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN import_log_id INTEGER"))
            logger.info("Added %s.import_log_id.", table)

    with engine.begin() as conn:
        for table, import_type in LINKED_TABLES.items():
            if not inspector.has_table(table):
                continue
            unlinked = conn.execute(text(
                f"SELECT id, godown_id, created_at FROM {table} WHERE import_log_id IS NULL"
            )).fetchall()
            if not unlinked:
                continue

            logs = [
                (row[0], row[1], _as_datetime(row[2]))
                for row in conn.execute(text(
                    "SELECT id, godown_id, imported_at FROM import_log "
                    "WHERE import_type = :t ORDER BY imported_at"
                ), {"t": import_type}).fetchall()
            ]
            logs = [l for l in logs if l[2] is not None]
            if not logs:
                continue

            matched = 0
            for row_id, godown_id, created_at in unlinked:
                created_at = _as_datetime(created_at)
                if created_at is None:
                    continue
                log_id = next(
                    (lid for lid, lgod, lat in logs
                     if lat >= created_at and (lgod == godown_id or lgod is None)),
                    None,
                )
                if log_id is None:
                    continue
                conn.execute(text(f"UPDATE {table} SET import_log_id = :l WHERE id = :r"),
                             {"l": log_id, "r": row_id})
                matched += 1
            if matched:
                logger.info("Linked %s of %s unlinked %s rows to their import.",
                            matched, len(unlinked), table)

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

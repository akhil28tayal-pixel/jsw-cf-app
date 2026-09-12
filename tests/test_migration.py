import sqlite3

from sqlalchemy import create_engine, text

from app.migrations import run_migrations, DEFAULT_GODOWN_NAME, SECOND_GODOWN_NAME


def build_old_schema_db(path):
    """Recreates the exact pre-godown schema, with sample data shaped like
    a real single-godown deployment (Manesar) that's already been in use."""
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE products (id INTEGER PRIMARY KEY, name VARCHAR(120) UNIQUE NOT NULL, active BOOLEAN, created_at DATETIME);
    CREATE TABLE dealers (id INTEGER PRIMARY KEY, name VARCHAR(150) UNIQUE NOT NULL, sap_code VARCHAR(40) UNIQUE, active BOOLEAN, created_at DATETIME);
    CREATE TABLE transporters (id INTEGER PRIMARY KEY, name VARCHAR(150) UNIQUE NOT NULL, sap_code VARCHAR(40) UNIQUE, contact VARCHAR(100), active BOOLEAN, created_at DATETIME);
    CREATE TABLE opening_stock (id INTEGER PRIMARY KEY, product_id INTEGER UNIQUE NOT NULL, bags FLOAT NOT NULL, as_of_date DATE NOT NULL);
    CREATE TABLE grn (id INTEGER PRIMARY KEY, date DATE NOT NULL, sap_grn_no VARCHAR(60) UNIQUE, invoice_no VARCHAR(60), vehicle_no VARCHAR(30), product_id INTEGER NOT NULL, bags_invoice FLOAT NOT NULL, bags_received FLOAT NOT NULL, source_plant VARCHAR(120), remarks TEXT, created_by INTEGER, created_at DATETIME);
    CREATE TABLE dispatch (id INTEGER PRIMARY KEY, date DATE NOT NULL, dc_no VARCHAR(60), dealer_id INTEGER NOT NULL, destination VARCHAR(150), district VARCHAR(80), pincode VARCHAR(10), vehicle_no VARCHAR(30), transporter_id INTEGER, product_id INTEGER NOT NULL, bags FLOAT NOT NULL, remarks TEXT, created_by INTEGER, created_at DATETIME);
    CREATE TABLE billing (id INTEGER PRIMARY KEY, date DATE NOT NULL, invoice_no VARCHAR(60), dealer_id INTEGER NOT NULL, product_id INTEGER NOT NULL, bags FLOAT NOT NULL, amount FLOAT NOT NULL, remarks TEXT, created_by INTEGER, created_at DATETIME);
    CREATE TABLE freight_rate_card (id INTEGER PRIMARY KEY, district VARCHAR(80) NOT NULL, pincode VARCHAR(10), transporter_rate_per_mt FLOAT NOT NULL, company_claim_rate_per_mt FLOAT NOT NULL, remarks TEXT, UNIQUE(district, pincode));
    CREATE TABLE import_log (id INTEGER PRIMARY KEY, import_type VARCHAR(20) NOT NULL, filename VARCHAR(255), imported_at DATETIME, imported_by INTEGER, rows_total INTEGER, rows_imported INTEGER, rows_skipped_duplicate INTEGER, rows_skipped_unmapped INTEGER, rows_skipped_pending INTEGER, rows_skipped_other INTEGER, details TEXT);
    """)
    conn.execute("INSERT INTO products (id, name, active) VALUES (1, 'JSW PPC', 1)")
    conn.execute("INSERT INTO products (id, name, active) VALUES (2, 'JSW MICROFINE', 1)")
    conn.execute("INSERT INTO dealers (id, name, active) VALUES (1, 'Old Dealer', 1)")
    conn.execute("INSERT INTO opening_stock (id, product_id, bags, as_of_date) VALUES (1, 1, 2000, '2026-08-01')")
    conn.execute("INSERT INTO grn (id, date, product_id, bags_invoice, bags_received) VALUES (1, '2026-09-01', 1, 500, 500)")
    conn.execute("INSERT INTO dispatch (id, date, dealer_id, product_id, bags) VALUES (1, '2026-09-01', 1, 1, 300)")
    conn.execute("INSERT INTO billing (id, date, dealer_id, product_id, bags, amount) VALUES (1, '2026-09-01', 1, 1, 300, 90000)")
    conn.execute("INSERT INTO freight_rate_card (id, district, transporter_rate_per_mt, company_claim_rate_per_mt) VALUES (1, 'Gurugram', 1500, 1800)")
    conn.commit()
    conn.close()


def test_migration_preserves_existing_data_and_adds_godowns(tmp_path):
    db_path = tmp_path / "old.db"
    build_old_schema_db(str(db_path))
    engine = create_engine(f"sqlite:///{db_path}")
    run_migrations(engine)

    with engine.connect() as conn:
        names = [row[0] for row in conn.execute(text("SELECT name FROM godowns ORDER BY id"))]
        assert DEFAULT_GODOWN_NAME in names
        assert SECOND_GODOWN_NAME in names
        manesar_id = conn.execute(text("SELECT id FROM godowns WHERE name=:n"), {"n": DEFAULT_GODOWN_NAME}).scalar()

        assert conn.execute(text("SELECT godown_id FROM grn WHERE id=1")).scalar() == manesar_id
        assert conn.execute(text("SELECT godown_id FROM dispatch WHERE id=1")).scalar() == manesar_id
        assert conn.execute(text("SELECT godown_id FROM billing WHERE id=1")).scalar() == manesar_id

        os_row = conn.execute(text("SELECT godown_id, product_id, bags FROM opening_stock WHERE id=1")).fetchone()
        assert os_row == (manesar_id, 1, 2000)

        frc_row = conn.execute(text(
            "SELECT godown_id, district, transporter_rate_per_mt FROM freight_rate_card WHERE id=1"
        )).fetchone()
        assert frc_row == (manesar_id, "Gurugram", 1500)

        # The real point of the rebuild: this INSERT would have violated the
        # old UNIQUE(product_id) constraint. It must succeed now.
        daultabad_id = conn.execute(text("SELECT id FROM godowns WHERE name=:n"), {"n": SECOND_GODOWN_NAME}).scalar()
        conn.execute(text(
            "INSERT INTO opening_stock (godown_id, product_id, bags, as_of_date) VALUES (:g, 1, 0, '2026-09-01')"
        ), {"g": daultabad_id})
        conn.commit()
        count = conn.execute(text("SELECT COUNT(*) FROM opening_stock WHERE product_id=1")).scalar()
        assert count == 2


def test_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "old2.db"
    build_old_schema_db(str(db_path))
    engine = create_engine(f"sqlite:///{db_path}")
    run_migrations(engine)
    run_migrations(engine)  # must not error or duplicate anything
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM godowns")).scalar() == 2
        assert conn.execute(text("SELECT COUNT(*) FROM opening_stock")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM grn")).scalar() == 1


def test_migration_on_fresh_empty_db_is_a_noop_for_old_tables(tmp_path):
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")
    run_migrations(engine)  # nothing pre-exists — should not error
    with engine.connect() as conn:
        # godowns still gets created + seeded even on a fresh DB (harmless, desired)
        assert conn.execute(text("SELECT COUNT(*) FROM godowns")).scalar() == 2


def test_migration_adds_per_product_bag_size_and_sets_microfine(tmp_path):
    """A database from before bag size was per-product gets the column, with
    every cement left on the 50 kg default (NULL) and Microfine set to its
    real 20 kg bag — so nobody has to remember to go and set it."""
    db_path = tmp_path / "bagsize.db"
    build_old_schema_db(str(db_path))
    engine = create_engine(f"sqlite:///{db_path}")
    run_migrations(engine)

    with engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT name, bag_weight_mt FROM products")).fetchall())
        assert rows["JSW PPC"] is None          # unchanged: falls back to the 50 kg default
        assert rows["JSW MICROFINE"] == 0.02    # 20 kg bag -> 50 bags to the tonne

    run_migrations(engine)  # idempotent: a second run must not error or reset anything
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT bag_weight_mt FROM products WHERE name='JSW MICROFINE'"
        )).scalar() == 0.02

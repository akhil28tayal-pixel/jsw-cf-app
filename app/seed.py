import logging

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.database import Base, engine, SessionLocal
from app import models
from app.auth import hash_password
from app.config import settings
from app.migrations import run_migrations

logger = logging.getLogger("jsw_cf_app")

DEFAULT_PRODUCTS = ["JSW PPC", "JSW ACE", "JSW JAL KAVACH", "JSW OPC43", "JSW OPC53"]

# Bag size in kg, where it isn't the usual 50 kg cement bag. Microfine is
# packed at 20 kg, so 1 MT is 50 bags of it — this feeds stock in MT, the
# MT->bags conversion on SAP import, and the freight weight of a truck.
NON_STANDARD_BAG_KG = {
    "JSW MICROFINE": 20,
}

# Known SAP material/brand codes -> our product names, seen in real JSW
# Sale/Material-In exports. Any code NOT in this list will show up on the
# Import page as "unmapped" for an admin to assign by hand — this list is
# just a head start, not something that needs to be exhaustive.
DEFAULT_SAP_PRODUCT_MAP = {
    "FG-10-PPC-CH-BG-HD": "JSW PPC",       # POZZOLANA PORTLAND CMNT CONCREEL HD
    "FG-10-PPC-CH-BG-AC": "JSW ACE",       # CONCREEL-HD-LPP-ACE
    "FG-10-CH-JK-BG-LPP": "JSW JAL KAVACH",  # PPC-JSWCL-CHD-JALKAVACH-LAMINATED BG
    "FG-10-OPC-43-BG-HD": "JSW OPC43",
    "FG-10-OPC-53-BG-HD": "JSW OPC53",
}


def init_db_and_seed():
    # Migrate any pre-existing (pre-godown) tables to the current schema
    # BEFORE create_all(), so create_all() only ever creates genuinely new
    # tables and never has to touch — or clash with — an existing one.
    try:
        run_migrations(engine)
    except OperationalError as e:
        if "already exists" not in str(e).lower() and "duplicate column" not in str(e).lower():
            raise
        logger.info("Schema already migrated by a concurrent process — continuing.")

    # With SQLite, more than one process can race to CREATE TABLE on the very
    # first startup (e.g. multiple gunicorn workers booting at once) — that
    # races harmlessly into an "already exists" error rather than corrupting
    # anything, so it's safe to swallow. (This whole function additionally
    # runs at most once in the recommended single-worker SQLite setup, but
    # stays defensive here in case that's ever changed.)
    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as e:
        if "already exists" not in str(e).lower():
            raise
        logger.info("Tables already created by a concurrent process — continuing.")

    db: Session = SessionLocal()
    try:
        # Seed products, only on a fully empty table.
        if db.query(models.Product).count() == 0:
            for name in DEFAULT_PRODUCTS:
                kg = NON_STANDARD_BAG_KG.get(name.upper())
                db.add(models.Product(name=name, bag_weight_mt=(kg / 1000.0) if kg else None))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()  # another process seeded products first

        # Seed known SAP code -> product mappings, only if not already present.
        if db.query(models.SapProductMap).count() == 0:
            products_by_name = {p.name: p for p in db.query(models.Product).all()}
            for sap_code, product_name in DEFAULT_SAP_PRODUCT_MAP.items():
                product = products_by_name.get(product_name)
                if product:
                    db.add(models.SapProductMap(sap_code=sap_code, product_id=product.id))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()

        # Seed the first admin user, only if there are no users at all.
        if db.query(models.User).count() == 0:
            admin = models.User(
                username=settings.ADMIN_USERNAME,
                password_hash=hash_password(settings.ADMIN_PASSWORD),
                role="admin",
            )
            db.add(admin)
            try:
                db.commit()
                print(
                    f"[seed] Created first admin user '{settings.ADMIN_USERNAME}'. "
                    f"Log in and change this password immediately via the Users page."
                )
            except IntegrityError:
                db.rollback()  # another process created the admin user first
    finally:
        db.close()

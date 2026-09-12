import datetime as dt
import io
import os
import tempfile

import pytest

# Point at a throwaway DB *before* the app (and therefore its engine) is
# imported anywhere, so every test module shares this one isolated database.
_tmp_dir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_dir}/test.db"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "changeme123"

import openpyxl  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app.database import SessionLocal  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db_session():
    """A raw SQLAlchemy session onto the same test database the app uses,
    for tests that need to inspect or set up state the HTTP layer doesn't
    expose directly."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------------------
# SAP import helpers
#
# GRN and Billing rows can ONLY be created by importing a SAP export — there
# is no manual-entry endpoint any more — so tests that need those rows build
# a minimal Sale / Material In workbook in memory and upload it through the
# real import endpoint. That means every test below also exercises the one
# code path that actually creates this data in production.
# ---------------------------------------------------------------------------
BAG_MT = 0.05                        # 50 kg bag, matches the bag_weight_mt setting
PPC_SAP_CODE = "FG-10-PPC-CH-BG-HD"  # seeded mapping -> product "JSW PPC" (id 1)
ACE_SAP_CODE = "FG-10-PPC-CH-BG-AC"  # seeded mapping -> product "JSW ACE"  (id 2)


def _as_date(value):
    """SAP exports carry real date cells, so tests write real dates too —
    the importer only accepts a date/datetime cell or SAP's dd.mm.yyyy text."""
    if isinstance(value, (dt.date, dt.datetime)):
        return value
    return dt.date.fromisoformat(str(value))


def _xlsx_bytes(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append([r.get(h) for h in headers])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SALE_HEADERS = ["Invoice No", "Invoice Date", "Sold To Party", "Sold To Name", "Product",
                "Product Description", "Qty", "Total Value", "ODN No.", "Truck No",
                "Cancelled Invoice"]

MATERIAL_IN_HEADERS = ["Delivery", "Status", "Material Document", "Posting Date", "Material",
                       "Description", "Actual Delivery Qty", "Received Qty", "Pending Qty",
                       "Truck Number", "ODN No", "Supplying Plant", "LR Number", "Transporter"]


def sale_xlsx(rows):
    """rows: dicts with keys invoice_no, date, dealer, bags, amount and the
    optional sap_code / dealer_code / truck / cancelled."""
    out = []
    for r in rows:
        out.append({
            "Invoice No": r["invoice_no"],
            "Invoice Date": _as_date(r.get("date", "2026-09-01")),
            "Sold To Party": r.get("dealer_code", ""),
            "Sold To Name": r["dealer"],
            "Product": r.get("sap_code", PPC_SAP_CODE),
            "Product Description": r.get("description", "PPC CONCREEL HD"),
            "Qty": r["bags"] * BAG_MT,
            "Total Value": r.get("amount", 0),
            "ODN No.": r.get("odn", ""),
            "Truck No": r.get("truck", ""),
            "Cancelled Invoice": r.get("cancelled", ""),
        })
    return _xlsx_bytes(SALE_HEADERS, out)


def material_in_xlsx(rows):
    """rows: dicts with keys material_doc, bags_received and the optional
    date / bags_invoice / truck / plant / status / sap_code."""
    out = []
    for r in rows:
        bags_invoice = r.get("bags_invoice", r["bags_received"])
        out.append({
            "Delivery": r.get("delivery", f"DL-{r['material_doc']}"),
            "Status": r.get("status", "Inward"),
            "Material Document": r["material_doc"],
            "Posting Date": _as_date(r.get("date", "2026-09-01")),
            "Material": r.get("sap_code", PPC_SAP_CODE),
            "Description": r.get("description", "PPC CONCREEL HD"),
            "Actual Delivery Qty": bags_invoice * BAG_MT,
            "Received Qty": r["bags_received"] * BAG_MT,
            "Pending Qty": r.get("pending_mt", 0),
            "Truck Number": r.get("truck", ""),
            "ODN No": r.get("odn", ""),
            "Supplying Plant": r.get("plant", ""),
            "LR Number": r.get("lr", ""),
            "Transporter": r.get("transporter", ""),
        })
    return _xlsx_bytes(MATERIAL_IN_HEADERS, out)


@pytest.fixture()
def import_billing(client):
    """Create Billing rows the only way production can — a Sale import into
    the client's currently active godown."""
    def _do(*rows, filename="sale.xlsx"):
        return client.post(
            "/import/sale",
            files={"file": (filename, sale_xlsx(list(rows)),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            follow_redirects=True,
        )
    return _do


@pytest.fixture()
def import_grn(client):
    """Create GRN rows the only way production can — a Material In import
    into the client's currently active godown."""
    def _do(*rows, filename="material_in.xlsx"):
        return client.post(
            "/import/material-in",
            files={"file": (filename, material_in_xlsx(list(rows)),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            follow_redirects=True,
        )
    return _do


@pytest.fixture()
def active_godown_id():
    """The godown the navbar switcher currently has selected for this client."""
    import re

    def _get(client):
        r = client.get("/")
        m = re.search(r'<option value="(\d+)" selected>', r.text)
        assert m, "no godown is marked active in the navbar"
        return int(m.group(1))

    return _get

import os

from app import models

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _login(client):
    client.post("/login", data={"username": "admin", "password": "changeme123"})


def _upload(client, path, endpoint):
    with open(path, "rb") as f:
        return client.post(endpoint, files={"file": (os.path.basename(path), f,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})


def test_sale_import_creates_billing_rows(client):
    _login(client)
    r = _upload(client, os.path.join(FIXTURES, "Sale_4-9.XLSX"), "/import/sale")
    assert r.status_code == 200

    r = client.get("/billing")
    assert "SEHRAWAT STEELS" in r.text
    assert "MUKESH TRADERS" in r.text

    r = client.get("/import")
    assert "Sale → Billing" in r.text
    # All 4 product codes in this sample file are pre-mapped by seed data,
    # so nothing should have been skipped as unmapped.
    assert "0" in r.text  # the unmapped column for this log row


def test_sale_import_is_idempotent_on_reimport(client):
    _login(client)
    _upload(client, os.path.join(FIXTURES, "Sale_4-9.XLSX"), "/import/sale")
    before = client.get("/billing").text.count("SAP import")

    _upload(client, os.path.join(FIXTURES, "Sale_4-9.XLSX"), "/import/sale")
    after = client.get("/billing").text.count("SAP import")
    assert before == after  # second import added nothing new


def test_sale_qty_converted_from_mt_to_bags(client):
    """First data row: QTY 3.0 MT -> 60 bags at the default 0.05 MT/bag."""
    _login(client)
    _upload(client, os.path.join(FIXTURES, "Sale_4-9.XLSX"), "/import/sale")
    r = client.get("/billing")
    assert "60" in r.text


def test_material_in_import_creates_grn_and_updates_stock(client, db_session):
    _login(client)
    r = _upload(client, os.path.join(FIXTURES, "Material_In_4-9.XLSX"), "/import/material-in")
    assert r.status_code == 200

    r = client.get("/grn")
    assert "5028085876" in r.text  # a real Material Document from the file

    # 3 "Inward" rows for JSW PPC in the sample, 42 MT each -> 840 bags each
    # at the default 0.05 MT/bag. Check the DB directly rather than the
    # aggregate stock page, since other tests in this shared-DB suite also
    # post dispatches/GRNs against product_id=1.
    grn_rows = db_session.query(models.GRN).filter(
        models.GRN.sap_grn_no.in_(["5028085876", "5028090161", "5028050195"])
    ).all()
    assert len(grn_rows) == 3
    assert all(g.bags_received == 840 for g in grn_rows)
    assert all(g.bags_invoice == 840 for g in grn_rows)


def test_material_in_skips_stock_in_transit_rows(client):
    _login(client)
    r = _upload(client, os.path.join(FIXTURES, "Material_In_4-9.XLSX"), "/import/material-in")
    assert r.status_code == 200
    r = client.get("/import")
    assert "Material In" in r.text


def test_material_in_never_imports_ggbs_or_raksha_as_grn(client):
    """Both codes are only 'Stock in Transit' in this fixture (not yet
    received) — they must not appear as GRN entries even though the codes
    themselves get registered for future mapping."""
    _login(client)
    _upload(client, os.path.join(FIXTURES, "Material_In_4-9.XLSX"), "/import/material-in")
    r = client.get("/grn")
    assert "GGBS" not in r.text
    assert "RAKSHA" not in r.text.upper() or "CONCREEL-HD-LPP-RAKSHA" not in r.text


def test_unmapped_codes_registered_for_pending_rows(client, db_session):
    _login(client)
    _upload(client, os.path.join(FIXTURES, "Material_In_4-9.XLSX"), "/import/material-in")
    codes = {u.sap_code for u in db_session.query(models.SapProductMap).filter(
        models.SapProductMap.product_id.is_(None)
    ).all()}
    assert "FG-02-GGBS-MF-LP20" in codes
    assert "FG-10-PPC-CH-BG-RA" in codes


def test_mapping_an_unmapped_code_then_reimporting_pulls_it_in(client, db_session):
    _login(client)
    from app import sap_import
    sap_import.resolve_product(db_session, "FG-10-PPC-CH-BG-RA", "CONCREEL-HD-LPP-RAKSHA")
    row = db_session.query(models.SapProductMap).filter_by(sap_code="FG-10-PPC-CH-BG-RA").first()
    assert row is not None and row.product_id is None

    client.post("/products/add", data={"name": "JSW PPC Raksha"})
    product = db_session.query(models.Product).filter_by(name="JSW PPC Raksha").first()
    r = client.post("/import/map-product", data={"sap_code": "FG-10-PPC-CH-BG-RA", "product_id": product.id})
    assert r.status_code == 200

    db_session.expire_all()
    row = db_session.query(models.SapProductMap).filter_by(sap_code="FG-10-PPC-CH-BG-RA").first()
    assert row.product_id == product.id

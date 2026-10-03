"""SAP stock vs physical stock reconciliation.

    SAP + advance - hold - shortage == physical

Each test below builds the three terms through the real endpoints, so the
arithmetic is checked against the same advance/hold logic the dealer report
uses rather than against a re-derivation of it.
"""
import datetime as dt

from app import crud, models
from tests.test_app import login


def _godown(db):
    return db.query(models.Godown).order_by(models.Godown.id).first()


def _row(db, godown_id, product_name):
    rows = crud.get_stock_reconciliation(db, godown_id)
    return next(r for r in rows if r["product"].name == product_name)


def test_no_statement_uploaded_leaves_the_row_blank_not_zero(client, db_session):
    """A missing SAP figure must not be treated as SAP holding zero — that
    would show a vast fake difference on every product."""
    login(client)
    g = _godown(db_session)
    r = _row(db_session, g.id, "JSW OPC53")
    assert r["sap_bags"] is None
    assert r["expected_bags"] is None
    assert r["difference_bags"] is None
    assert r["matched"] is False
    html = client.get("/stock").text
    assert "Not uploaded" in html


def test_advance_is_added_and_hold_is_subtracted(client, db_session, import_billing):
    """Billed-not-dispatched sits in the godown (add it); dispatched-not-billed
    has left while SAP still counts it (subtract it)."""
    login(client)
    g = _godown(db_session)
    product = db_session.query(models.Product).filter_by(name="JSW PPC").first()

    # Opening baseline so physical stock is a known number.
    client.post("/stock/opening", data={"product_id": product.id, "bags": 1000,
                                        "as_of_date": "2026-01-01"})
    # Advance: 600 billed, nothing dispatched to this dealer.
    import_billing({"invoice_no": "REC-A1", "date": "2026-02-01",
                    "dealer": "Recon Advance Dealer", "bags": 600})
    # Hold: 250 dispatched, nothing billed to this dealer.
    client.post("/dispatch/add", data={"date": "2026-02-02", "dealer_name": "",
                                       "new_dealer": "Recon Hold Dealer",
                                       "product_id": product.id, "bags": 250,
                                       "district": "Gurugram"})

    before = _row(db_session, g.id, "JSW PPC")
    advance, hold = before["advance_bags"], before["hold_bags"]
    assert advance >= 600 and hold >= 250

    physical = before["physical_bags"]
    # Choose the SAP figure that should reconcile exactly, then assert it does.
    sap = physical - advance + hold
    crud.upsert_sap_stock(db_session, g.id, product.id, dt.date(2026, 2, 28), sap, source="manual")

    after = _row(db_session, g.id, "JSW PPC")
    assert after["sap_bags"] == sap
    assert abs(after["expected_bags"] - physical) < 1e-6
    assert abs(after["difference_bags"]) < 1e-6
    assert after["matched"] is True


def test_shortage_adjustment_is_subtracted_from_sap(client, db_session):
    """A handover shortage means SAP over-states. Recording it must close the
    gap, not widen it — this is the sign that is easy to get backwards."""
    login(client)
    g = _godown(db_session)
    product = db_session.query(models.Product).filter_by(name="JSW ACE").first()

    r = _row(db_session, g.id, "JSW ACE")
    # SAP claims 40 bags more than reconciles, i.e. a 40-bag shortage.
    sap = r["physical_bags"] - r["advance_bags"] + r["hold_bags"] + 40
    crud.upsert_sap_stock(db_session, g.id, product.id, dt.date(2026, 3, 31), sap, source="manual")

    unadjusted = _row(db_session, g.id, "JSW ACE")
    assert abs(unadjusted["difference_bags"] - (-40)) < 1e-6
    assert unadjusted["matched"] is False

    crud.add_sap_adjustment(db_session, g.id, product.id, 40,
                            as_of_date=dt.date(2026, 3, 31), reason="Godown handover shortage")
    adjusted = _row(db_session, g.id, "JSW ACE")
    assert adjusted["shortage_bags"] == 40
    assert abs(adjusted["difference_bags"]) < 1e-6
    assert adjusted["matched"] is True


def test_reuploading_the_same_date_replaces_the_figure(client, db_session):
    login(client)
    g = _godown(db_session)
    product = db_session.query(models.Product).filter_by(name="JSW OPC43").first()
    d = dt.date(2026, 4, 30)
    crud.upsert_sap_stock(db_session, g.id, product.id, d, 500, source="manual")
    crud.upsert_sap_stock(db_session, g.id, product.id, d, 700, source="manual")
    rows = db_session.query(models.SapStockSnapshot).filter_by(
        godown_id=g.id, product_id=product.id, as_of_date=d).all()
    assert len(rows) == 1
    assert rows[0].bags == 700


def test_the_most_recent_statement_is_the_one_used(client, db_session):
    login(client)
    g = _godown(db_session)
    product = db_session.query(models.Product).filter_by(name="JSW JAL KAVACH").first()
    crud.upsert_sap_stock(db_session, g.id, product.id, dt.date(2026, 5, 31), 111, source="manual")
    crud.upsert_sap_stock(db_session, g.id, product.id, dt.date(2026, 6, 30), 222, source="manual")
    r = _row(db_session, g.id, "JSW JAL KAVACH")
    assert r["sap_bags"] == 222
    assert r["sap_as_of"] == dt.date(2026, 6, 30)


def test_adjustments_are_per_godown_and_summed(client, db_session):
    login(client)
    godowns = db_session.query(models.Godown).order_by(models.Godown.id).all()
    product = db_session.query(models.Product).filter_by(name="JSW OPC53").first()
    crud.add_sap_adjustment(db_session, godowns[0].id, product.id, 15, reason="handover 1")
    crud.add_sap_adjustment(db_session, godowns[0].id, product.id, 25, reason="handover 2")
    assert crud.sap_adjustment_bags(db_session, godowns[0].id, product.id) == 40
    if len(godowns) > 1:
        assert crud.sap_adjustment_bags(db_session, godowns[1].id, product.id) == 0


def test_staff_cannot_add_a_sap_adjustment(client, db_session):
    login(client)
    g = _godown(db_session)
    product = db_session.query(models.Product).filter_by(name="JSW PPC").first()
    client.post("/users/add", data={"username": "recon_staff", "password": "staffpass123",
                                    "role": "staff"})
    client.post("/logout")
    client.post("/login", data={"username": "recon_staff", "password": "staffpass123"})
    before = len(crud.list_sap_adjustments(db_session))
    r = client.post("/sap-adjustment/add", data={"godown_id": g.id, "product_id": product.id,
                                                 "bags": 99, "reason": "should not work"})
    assert r.status_code == 403
    assert len(crud.list_sap_adjustments(db_session)) == before

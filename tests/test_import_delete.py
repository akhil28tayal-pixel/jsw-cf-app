"""Undoing a SAP import from inside the app.

GRN and Billing can only ever come from an import, so deleting the import and
re-uploading a corrected file is the only way to fix a bad upload. Every row
records the import that created it, which is what makes the removal exact
rather than a guess from timestamps.
"""
import datetime as dt

from sqlalchemy import text

from app import crud, models
from tests.test_app import login


def test_imported_rows_know_which_import_made_them(client, db_session, import_billing):
    login(client)
    import_billing({"invoice_no": "LINK-1", "dealer": "Link Dealer", "bags": 60})
    row = db_session.query(models.Billing).filter_by(invoice_no="LINK-1").one()
    assert row.import_log_id is not None
    log = db_session.get(models.ImportLog, row.import_log_id)
    assert log.import_type == "sale"


def test_delete_removes_exactly_that_imports_rows(client, db_session, import_billing):
    login(client)
    import_billing({"invoice_no": "DEL-KEEP", "dealer": "Keep Dealer", "bags": 10})
    keep_log = db_session.query(models.ImportLog).order_by(models.ImportLog.id.desc()).first().id
    import_billing({"invoice_no": "DEL-GO-1", "dealer": "Go Dealer", "bags": 20},
                   {"invoice_no": "DEL-GO-2", "dealer": "Go Dealer", "bags": 30})
    go_log = db_session.query(models.ImportLog).order_by(models.ImportLog.id.desc()).first().id

    assert crud.import_log_contents(db_session, go_log)["billing"] == 2
    r = client.post(f"/import/{go_log}/delete", follow_redirects=True)
    assert r.status_code == 200

    assert db_session.query(models.Billing).filter_by(invoice_no="DEL-GO-1").first() is None
    assert db_session.query(models.Billing).filter_by(invoice_no="DEL-GO-2").first() is None
    assert db_session.query(models.Billing).filter_by(invoice_no="DEL-KEEP").first() is not None
    assert db_session.get(models.ImportLog, go_log) is None
    assert db_session.get(models.ImportLog, keep_log) is not None


def test_deleting_an_import_moves_stock_and_advance_hold(client, db_session, import_grn):
    """The whole point: the registers feed stock and the dealer report, so
    undoing an import has to move them too."""
    login(client)
    product = db_session.query(models.Product).filter_by(name="JSW PPC").first()
    before = next(s for s in crud.get_current_stock(db_session, _godown(db_session).id)
                  if s["product"].id == product.id)["current_bags"]
    import_grn({"material_doc": "DELSTK-1", "bags_received": 500, "bags_invoice": 500})
    log_id = db_session.query(models.ImportLog).order_by(models.ImportLog.id.desc()).first().id
    during = next(s for s in crud.get_current_stock(db_session, _godown(db_session).id)
                  if s["product"].id == product.id)["current_bags"]
    assert during == before + 500

    client.post(f"/import/{log_id}/delete", follow_redirects=True)
    after = next(s for s in crud.get_current_stock(db_session, _godown(db_session).id)
                 if s["product"].id == product.id)["current_bags"]
    assert after == before


def _godown(db):
    return db.query(models.Godown).order_by(models.Godown.id).first()


def test_reimporting_the_same_file_brings_it_back(client, db_session, import_billing):
    """Delete, fix, re-upload — the sanctioned repair path. Nothing may block
    the re-import, which means the de-duplication key has to be gone too."""
    login(client)
    import_billing({"invoice_no": "ROUND-1", "dealer": "Round Dealer", "bags": 45})
    log_id = db_session.query(models.ImportLog).order_by(models.ImportLog.id.desc()).first().id
    client.post(f"/import/{log_id}/delete", follow_redirects=True)
    assert db_session.query(models.Billing).filter_by(invoice_no="ROUND-1").first() is None

    import_billing({"invoice_no": "ROUND-1", "dealer": "Round Dealer", "bags": 45})
    again = db_session.query(models.Billing).filter_by(invoice_no="ROUND-1").one()
    # Not asserting a NEW log id: SQLite reissues the id of a deleted row, so
    # the fresh log can legitimately reuse the old number. What matters is that
    # the row came back and points at a log that exists.
    assert again.import_log_id is not None
    assert db_session.get(models.ImportLog, again.import_log_id) is not None
    assert crud.import_log_contents(db_session, again.import_log_id)["billing"] == 1


def test_staff_cannot_delete_an_import(client, db_session, import_billing):
    login(client)
    import_billing({"invoice_no": "PERM-1", "dealer": "Perm Dealer", "bags": 15})
    log_id = db_session.query(models.ImportLog).order_by(models.ImportLog.id.desc()).first().id
    client.post("/users/add", data={"username": "imp_staff", "password": "staffpass123",
                                    "role": "staff"})
    client.post("/logout")
    client.post("/login", data={"username": "imp_staff", "password": "staffpass123"})

    r = client.post(f"/import/{log_id}/delete")
    assert r.status_code == 403
    assert db_session.get(models.ImportLog, log_id) is not None
    assert "/delete" not in client.get("/import").text.split("Recent Imports")[1]


def test_a_hand_corrected_stock_figure_survives_the_delete(client, db_session):
    """Someone retyping a SAP figure means they knew better than the file.
    Discarding that silently when the import is undone would be worse than
    leaving the row behind."""
    login(client)
    g = _godown(db_session)
    product = db_session.query(models.Product).filter_by(name="JSW OPC43").first()
    d = dt.date(2026, 7, 31)
    snap = crud.upsert_sap_stock(db_session, g.id, product.id, d, 500, source="html")
    log = models.ImportLog(import_type="sap_stock", godown_id=g.id, filename="x.html")
    db_session.add(log); db_session.commit()
    snap.import_log_id = log.id
    db_session.commit()

    # An admin corrects it by hand, which flips source away from "html".
    crud.upsert_sap_stock(db_session, g.id, product.id, d, 777, source="manual")
    crud.delete_import(db_session, log.id)

    kept = crud.latest_sap_stock(db_session, g.id, product.id)
    assert kept is not None and kept.bags == 777
    assert kept.import_log_id is None          # the dangling link is cleared


def test_backfill_links_rows_that_predate_the_column(client, db_session, import_billing):
    """Existing databases have rows with no link. They are matched to the first
    import of their own type and godown logged at or after they were created —
    which is the order the importer actually works in."""
    login(client)
    import_billing({"invoice_no": "BACKFILL-1", "dealer": "Backfill Dealer", "bags": 70})
    row = db_session.query(models.Billing).filter_by(invoice_no="BACKFILL-1").one()
    expected = row.import_log_id
    assert expected is not None

    # Simulate the pre-upgrade state for this row.
    db_session.execute(text("UPDATE billing SET import_log_id = NULL WHERE id = :i"), {"i": row.id})
    db_session.commit()

    from app.database import engine
    from app.migrations import link_imports_to_their_rows
    link_imports_to_their_rows(engine)

    db_session.expire_all()
    assert db_session.query(models.Billing).filter_by(invoice_no="BACKFILL-1").one().import_log_id == expected

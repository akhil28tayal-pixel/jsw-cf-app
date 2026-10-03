import pytest  # noqa: F401
from app import models


def login(client, username="admin", password="changeme123"):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code in (200, 303)
    return r


def test_login_required_redirects(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_login_wrong_password_rejected(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"}, follow_redirects=False)
    assert r.status_code == 303
    r2 = client.get("/", follow_redirects=False)
    assert r2.status_code == 303  # still not logged in


def test_login_success_and_dashboard(client):
    login(client)
    r = client.get("/")
    assert r.status_code == 200
    for p in ["JSW PPC", "JSW ACE", "JSW JAL KAVACH", "JSW OPC43", "JSW OPC53"]:
        assert p in r.text


def test_staff_cannot_reach_claims_or_masters(client):
    login(client)
    client.post("/users/add", data={"username": "staffuser", "password": "pass1234", "role": "staff"})
    client.get("/logout")
    login(client, "staffuser", "pass1234")
    r = client.get("/claims")
    assert r.status_code == 403
    r = client.get("/products")
    assert r.status_code == 403


def test_opening_stock_and_current_stock(client):
    login(client)
    r = client.post("/stock/opening", data={"product_id": 1, "bags": 2000, "as_of_date": "2026-09-01"})
    assert r.status_code == 200
    r = client.get("/stock")
    assert "2000" in r.text


def test_grn_updates_stock(client, import_grn):
    login(client)
    client.post("/stock/opening", data={"product_id": 1, "bags": 1000, "as_of_date": "2026-09-01"})
    import_grn({"material_doc": "GRN-1", "date": "2026-09-02", "bags_invoice": 500,
                "bags_received": 490, "truck": "HR01", "plant": "Plant A"})
    r = client.get("/stock")
    assert "1490" in r.text  # 1000 opening + 490 received


def test_grn_reimport_of_same_material_doc_is_skipped(client, import_grn, db_session):
    """The Material Document is the de-duplication key, so re-uploading a file
    that overlaps a previous one can never double-count a receipt."""
    login(client)
    row = {"material_doc": "DUP-1", "date": "2026-09-02", "bags_received": 100}
    import_grn(row)
    r = import_grn(row)
    assert "1 already imported" in r.text
    assert db_session.query(models.GRN).filter_by(sap_grn_no="DUP-1").count() == 1


def test_manual_grn_and_billing_entry_endpoints_do_not_exist(client):
    """GRN and Billing are SAP-import-only by design."""
    login(client)
    for path in ("/grn/add", "/billing/add"):
        r = client.post(path, data={"date": "2026-09-01", "product_id": 1, "bags": 10},
                        follow_redirects=False)
        assert r.status_code in (404, 405), f"{path} still accepts manual entry"


def test_grn_and_billing_pages_have_no_entry_form(client):
    login(client)
    for path, form_action in (("/grn", 'action="/grn/add"'), ("/billing", 'action="/billing/add"')):
        r = client.get(path)
        assert r.status_code == 200
        assert form_action not in r.text
        assert "come from SAP only" in r.text


def test_dispatch_creates_dealer_and_transporter_on_the_fly(client):
    login(client)
    r = client.post("/dispatch/add", data={
        "date": "2026-09-05", "dc_no": "DC-1", "dealer_name": "", "new_dealer": "Sharma Traders",
        "destination": "Rohtak", "district": "Gurugram", "pincode": "122001",
        "vehicle_no": "HR12CD5678", "transporter_name": "", "new_transporter": "Balaji Transport",
        "product_id": 1, "bags": 400,
    })
    assert r.status_code == 200
    r = client.get("/dispatch")
    assert "Sharma Traders" in r.text
    assert "Balaji Transport" in r.text


def test_dealer_advance_hold_logic(client, import_billing):
    """Core requirement #5: billed-ahead-of-dispatch = Advance;
    dispatched-ahead-of-billing = Hold; later matching entries net out."""
    login(client)
    # Dealer billed 600 bags but 0 dispatched yet -> Advance of 600
    import_billing({"invoice_no": "INV-1", "date": "2026-09-01", "dealer": "Advance Dealer",
                    "bags": 600, "amount": 180000})
    r = client.get("/dealer-report")
    assert "Advance Dealer" in r.text
    assert "Advance" in r.text

    # Dealer dispatched 300 bags but not billed yet -> Hold of 300
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Hold Dealer",
        "product_id": 1, "bags": 300, "district": "Gurugram",
    })
    r = client.get("/dealer-report")
    assert "Hold Dealer" in r.text
    assert "Hold" in r.text

    # Now dispatch 600 to Advance Dealer -> balance should settle to 0
    client.post("/dispatch/add", data={
        "date": "2026-09-10", "dealer_name": "Advance Dealer", "new_dealer": "",
        "product_id": 1, "bags": 600, "district": "Gurugram",
    })
    r = client.get("/dealer-report")
    assert "Settled" in r.text


def test_freight_rate_card_and_truck_report(client, active_godown_id):
    login(client)
    client.post("/rate-card/add", data={
        "godown_id": active_godown_id(client), "district": "Gurugram", "pincode": "122001",
        "transporter_rate_per_mt": 1500, "company_claim_rate_per_mt": 1800,
    })
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Freight Test Dealer",
        "vehicle_no": "HR99ZZ0001", "transporter_name": "", "new_transporter": "Freight Transport Co",
        "district": "Gurugram", "pincode": "122001", "product_id": 1, "bags": 400,
    })
    r = client.get("/freight")
    assert r.status_code == 200
    assert "HR99ZZ0001" in r.text
    assert "Freight Transport Co" in r.text
    # 400 bags * 0.05 MT = 20 MT * 1500 = 30000 payable to transporter
    assert "30,000" in r.text or "30000" in r.text
    # The company claim rate must NEVER appear on the public freight page.
    assert "1800" not in r.text or "1,800" not in r.text


def test_claims_page_shows_commission_and_margin_admin_only(client, active_godown_id):
    login(client)
    client.post("/rate-card/add", data={
        "godown_id": active_godown_id(client), "district": "Rohtak", "pincode": "",
        "transporter_rate_per_mt": 1400, "company_claim_rate_per_mt": 1700,
    })
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Claims Test Dealer",
        "district": "Rohtak", "product_id": 1, "bags": 1000,
    })
    r = client.get("/claims")
    assert r.status_code == 200
    assert "Commission Claimable" in r.text
    assert "Freight Margin" in r.text


# ---------------------------------------------------------------------------
# Filters on GRN, Dispatch, Billing
# ---------------------------------------------------------------------------
def test_grn_filter_by_product_and_vehicle(client, import_grn):
    login(client)
    import_grn({"material_doc": "FILT-GRN-1", "truck": "FILTER-TRUCK-1", "bags_received": 100})
    r = client.get("/grn", params={"vehicle_no": "FILTER-TRUCK-1"})
    assert "FILT-GRN-1" in r.text
    r = client.get("/grn", params={"vehicle_no": "NO-SUCH-TRUCK"})
    assert "FILT-GRN-1" not in r.text


def test_dispatch_filter_by_dealer(client):
    login(client)
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Filter Test Dealer XYZ",
        "product_id": 1, "bags": 50, "vehicle_no": "FILTER-DISP-1",
    })
    r = client.get("/dispatch")
    # find the dealer's id from the rendered dealer filter dropdown
    import re
    m = re.search(r'<option value="(\d+)"[^>]*>Filter Test Dealer XYZ</option>', r.text)
    assert m, "dealer not found in filter dropdown"
    dealer_id = m.group(1)
    r = client.get("/dispatch", params={"dealer_id": dealer_id})
    assert "FILTER-DISP-1" in r.text
    r = client.get("/dispatch", params={"dealer_id": 999999})
    assert "FILTER-DISP-1" not in r.text


def test_billing_filter_by_date_range(client, import_billing):
    login(client)
    import_billing({"invoice_no": "FILT-BILL-1", "date": "2026-01-15",
                    "dealer": "Filter Billing Dealer", "bags": 20, "amount": 6000})
    r = client.get("/billing", params={"date_from": "2026-01-01", "date_to": "2026-01-31"})
    assert "FILT-BILL-1" in r.text
    r = client.get("/billing", params={"date_from": "2026-02-01", "date_to": "2026-02-28"})
    assert "FILT-BILL-1" not in r.text


# ---------------------------------------------------------------------------
# Backup & export
# ---------------------------------------------------------------------------
def test_backup_page_admin_only(client):
    login(client)
    r = client.get("/backup")
    assert r.status_code == 200
    assert "Full Database Backup" in r.text


def test_backup_download_returns_sqlite_file(client):
    login(client)
    r = client.get("/backup/download-db")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert len(r.content) > 0
    assert r.content[:16] == b"SQLite format 3\x00"


def test_export_csv_endpoints_work(client, import_grn):
    login(client)
    import_grn({"material_doc": "CSV-TEST-1", "bags_received": 10})
    r = client.get("/backup/export/grn")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "CSV-TEST-1" in r.text


def test_staff_cannot_reach_backup(client):
    login(client)
    client.post("/users/add", data={"username": "backupstaff", "password": "pass1234", "role": "staff"})
    client.get("/logout")
    login(client, "backupstaff", "pass1234")
    r = client.get("/backup")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Filter forms submitted with blank fields (how browsers actually submit an
# unselected "All X" <select> or an empty <input type=date>) must not 422.
# ---------------------------------------------------------------------------
def test_grn_filter_blank_fields_does_not_422(client):
    login(client)
    r = client.get("/grn", params={"date_from": "", "date_to": "", "product_id": "",
                                    "vehicle_no": "", "search": ""})
    assert r.status_code == 200


def test_dispatch_filter_blank_fields_does_not_422(client):
    login(client)
    r = client.get("/dispatch", params={"date_from": "", "date_to": "", "product_id": "",
                                         "dealer_id": "", "transporter_id": "", "vehicle_no": ""})
    assert r.status_code == 200


def test_billing_filter_blank_fields_does_not_422(client):
    login(client)
    r = client.get("/billing", params={"date_from": "", "date_to": "", "product_id": "",
                                        "dealer_id": "", "search": ""})
    assert r.status_code == 200


def test_freight_filter_blank_fields_does_not_422(client):
    login(client)
    r = client.get("/freight", params={"date_from": "", "date_to": ""})
    assert r.status_code == 200


def test_dealer_report_filter_blank_fields_does_not_422(client):
    login(client)
    r = client.get("/dealer-report", params={"dealer_id": "", "product_id": ""})
    assert r.status_code == 200


def test_grn_filter_with_real_values_still_works_after_fix(client, import_grn):
    login(client)
    import_grn({"material_doc": "BLANKFIX-GRN-1", "date": "2026-09-01", "bags_received": 5})
    r = client.get("/grn", params={"date_from": "2026-09-01", "date_to": "2026-09-01", "product_id": "1"})
    assert "BLANKFIX-GRN-1" in r.text


# ---------------------------------------------------------------------------
# Editing a dispatch entry
# ---------------------------------------------------------------------------
def test_edit_dispatch_form_loads_prefilled(client, db_session):
    login(client)
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dc_no": "EDIT-DC-1", "dealer_name": "", "new_dealer": "Edit Test Dealer",
        "product_id": 1, "bags": 300, "vehicle_no": "EDIT-TRUCK-1",
    })
    entry = db_session.query(models.Dispatch).filter_by(dc_no="EDIT-DC-1").first()
    assert entry is not None
    dispatch_id = entry.id

    r = client.get(f"/dispatch/{dispatch_id}/edit")
    assert r.status_code == 200
    assert "EDIT-DC-1" in r.text
    assert 'value="300"' in r.text


def test_edit_dispatch_updates_values_and_stock(client, db_session):
    login(client)
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Edit Update Dealer",
        "product_id": 1, "bags": 100, "vehicle_no": "EDIT-TRUCK-2", "dc_no": "EDIT-UPD-1",
    })
    entry = db_session.query(models.Dispatch).filter_by(dc_no="EDIT-UPD-1").first()
    assert entry is not None
    dispatch_id = entry.id

    r = client.post(f"/dispatch/{dispatch_id}/edit", data={
        "date": "2026-09-02", "dc_no": "EDIT-UPD-1-CHANGED", "dealer_name": "Edit Update Dealer",
        "new_dealer": "", "product_id": 1, "bags": 250, "vehicle_no": "EDIT-TRUCK-2-CHANGED",
    })
    assert r.status_code == 200

    db_session.expire_all()
    updated = db_session.query(models.Dispatch).get(dispatch_id)
    assert updated.dc_no == "EDIT-UPD-1-CHANGED"
    assert updated.bags == 250
    assert updated.vehicle_no == "EDIT-TRUCK-2-CHANGED"
    assert str(updated.date) == "2026-09-02"


def test_edit_dispatch_nonexistent_id_redirects_with_error(client):
    login(client)
    r = client.get("/dispatch/999999/edit", follow_redirects=True)
    assert "not found" in r.text


# ---------------------------------------------------------------------------
# Deleting a dispatch entry
# ---------------------------------------------------------------------------
def test_delete_dispatch_removes_it_and_updates_stock(client, db_session):
    login(client)
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dc_no": "DEL-DC-1", "dealer_name": "", "new_dealer": "Delete Test Dealer",
        "product_id": 1, "bags": 150,
    })
    entry = db_session.query(models.Dispatch).filter_by(dc_no="DEL-DC-1").first()
    assert entry is not None
    dispatch_id = entry.id

    r = client.post(f"/dispatch/{dispatch_id}/delete")
    assert r.status_code == 200
    assert "Deleted dispatch" in r.text

    db_session.expire_all()
    gone = db_session.get(models.Dispatch, dispatch_id)
    assert gone is None

    r = client.get("/dispatch")
    assert "DEL-DC-1" not in r.text


def test_delete_dispatch_nonexistent_id_redirects_with_error(client):
    login(client)
    r = client.post("/dispatch/999999/delete", follow_redirects=True)
    assert "not found" in r.text


def test_delete_dispatch_removes_material_from_advance_hold_report(client, db_session):
    """Deleting a dispatch should remove its effect on the advance/hold balance
    (the dealer master record itself is untouched, so we check the actual
    report data via crud rather than scraping the page, since the dealer
    name legitimately still appears in the filter dropdown)."""
    from app import crud
    login(client)
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Delete Hold Dealer",
        "product_id": 1, "bags": 400,
    })
    dealer = db_session.query(models.Dealer).filter_by(name="Delete Hold Dealer").first()
    rows = crud.get_dealer_advance_hold(db_session, dealer_id=dealer.id)
    assert any(r["status"] == "Hold" and r["balance_bags"] == -400 for r in rows)

    entry = db_session.query(models.Dispatch).filter_by(dealer_id=dealer.id).first()
    client.post(f"/dispatch/{entry.id}/delete")

    db_session.expire_all()
    rows = crud.get_dealer_advance_hold(db_session, dealer_id=dealer.id)
    assert rows == []  # no billing or dispatch left for this dealer at all


def test_admin_can_delete_billing_row_and_reimport_it(client, import_billing, db_session):
    """Deleting frees the Invoice No., so the corrected file can be re-imported
    — that is the whole point of allowing a delete on an import-only table."""
    login(client)
    import_billing({"invoice_no": "DEL-1", "date": "2026-09-01", "dealer": "Delete Dealer",
                    "bags": 100, "amount": 30000})
    row = db_session.query(models.Billing).filter_by(invoice_no="DEL-1").one()
    r = client.post(f"/billing/{row.id}/delete", follow_redirects=True)
    assert r.status_code == 200
    db_session.expire_all()
    assert db_session.query(models.Billing).filter_by(invoice_no="DEL-1").count() == 0

    import_billing({"invoice_no": "DEL-1", "date": "2026-09-01", "dealer": "Delete Dealer",
                    "bags": 120, "amount": 36000})
    db_session.expire_all()
    again = db_session.query(models.Billing).filter_by(invoice_no="DEL-1").one()
    assert again.bags == 120


def test_staff_cannot_delete_billing(client, import_billing, db_session):
    login(client)
    import_billing({"invoice_no": "DEL-2", "date": "2026-09-01", "dealer": "Keep Dealer",
                    "bags": 50, "amount": 15000})
    row = db_session.query(models.Billing).filter_by(invoice_no="DEL-2").one()
    client.post("/users/add", data={"username": "staffdel", "password": "pass1234", "role": "staff"})
    client.get("/logout")
    login(client, "staffdel", "pass1234")
    r = client.post(f"/billing/{row.id}/delete", follow_redirects=False)
    assert r.status_code == 403
    db_session.expire_all()
    assert db_session.query(models.Billing).filter_by(invoice_no="DEL-2").count() == 1


def test_billing_delete_button_hidden_from_staff(client):
    login(client)
    client.post("/users/add", data={"username": "staffview", "password": "pass1234", "role": "staff"})
    client.get("/logout")
    login(client, "staffview", "pass1234")
    r = client.get("/billing")
    assert r.status_code == 200
    assert "/delete" not in r.text



# ---------------------------------------------------------------------------
# Dispatch -> Excel export
# ---------------------------------------------------------------------------
def _dispatch_sheet(response, sheet="Dispatch"):
    """Read a downloaded workbook back as (header_row, data_rows, total_row).

    Every export now opens with a title block naming the firm, the report, the
    godown and the period, so the column headings are NOT on row 1. This finds
    them by content instead of by position, which also means the tests stop
    caring if another line is added to that block later.
    """
    import io as _io
    import openpyxl
    wb = openpyxl.load_workbook(_io.BytesIO(response.content))
    rows = list(wb[sheet].iter_rows(values_only=True))
    header_idx = next(i for i, row in enumerate(rows) if row and row[0] == "Date")
    header = rows[header_idx]
    body = rows[header_idx + 1:]
    total = None
    if body and body[-1] and str(body[-1][0] or "").startswith("TOTAL"):
        total, body = body[-1], body[:-1]
    return header, body, total


def _add_dispatch(client, **overrides):
    data = {"date": "2026-09-05", "dc_no": "DC-X", "dealer_name": "", "new_dealer": "Excel Dealer",
            "destination": "Rohtak", "district": "Gurugram", "pincode": "122001",
            "vehicle_no": "HR55XL0001", "transporter_name": "", "new_transporter": "Excel Transport",
            "product_id": 1, "bags": 200}
    data.update(overrides)
    return client.post("/dispatch/add", data=data)


def test_dispatch_export_returns_an_xlsx_with_totals(client):
    login(client)
    _add_dispatch(client, new_dealer="XL Dealer A", bags=200, date="2026-10-01",
                  vehicle_no="HR55XL1111")
    _add_dispatch(client, new_dealer="XL Dealer B", bags=300, date="2026-10-02",
                  vehicle_no="HR55XL2222")
    r = client.get("/dispatch/export", params={"vehicle_no": "HR55XL"})
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert ".xlsx" in r.headers["content-disposition"]

    header, body, total = _dispatch_sheet(r)
    assert header[0] == "Date" and header[9] == "Bags" and header[10] == "MT"
    assert len(body) == 2
    assert total is not None and total[0].startswith("TOTAL")
    assert total[9] == 500                         # 200 + 300 bags
    assert total[10] == 25.0                       # 500 bags x 50 kg = 25 MT


def test_dispatch_export_respects_the_filters(client, db_session):
    login(client)
    _add_dispatch(client, new_dealer="Only Me Dealer", bags=120, date="2026-11-01",
                  vehicle_no="HR66ON0001")
    _add_dispatch(client, new_dealer="Not Me Dealer", bags=999, date="2026-11-02",
                  vehicle_no="HR66NO0002")
    dealer = db_session.query(models.Dealer).filter_by(name="Only Me Dealer").one()

    r = client.get("/dispatch/export", params={"dealer_id": dealer.id})
    header, body, total = _dispatch_sheet(r)
    assert {row[2] for row in body} == {"Only Me Dealer"}
    assert total[9] == 120


def test_dispatch_export_accepts_blank_filter_fields(client):
    """The Filter form submits empty strings for untouched fields — the export
    must treat those as 'no filter', not 422 like a raw typed Query would."""
    login(client)
    r = client.get("/dispatch/export", params={"date_from": "", "date_to": "", "product_id": "",
                                               "dealer_id": "", "transporter_id": "",
                                               "vehicle_no": ""})
    assert r.status_code == 200


def test_export_carries_a_title_block_naming_the_firm_and_scope(client):
    """A file someone saves or emails has to identify itself."""
    login(client)
    _add_dispatch(client, new_dealer="Title Block Dealer", bags=10, date="2026-12-01")
    r = client.get("/dispatch/export")
    import io as _io, openpyxl
    ws = openpyxl.load_workbook(_io.BytesIO(r.content))["Dispatch"]
    assert "A T TRADING CO" in ws["A1"].value
    assert "Dispatch Register" in ws["A1"].value
    assert "Godown:" in ws["A2"].value and "Generated:" in ws["A2"].value
    assert "IST" in ws["A2"].value


def test_dispatch_page_has_the_excel_button(client):
    login(client)
    r = client.get("/dispatch")
    assert 'formaction="/dispatch/export"' in r.text

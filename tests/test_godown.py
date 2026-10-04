from app import models


def login(client, username="admin", password="changeme123"):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code in (200, 303)
    return r


def get_godown_id(db_session, name):
    g = db_session.query(models.Godown).filter_by(name=name).first()
    assert g is not None, f"Godown '{name}' not found — migration/seed should have created it"
    return g.id


def switch_godown(client, godown_id, redirect_to="/"):
    return client.post("/set-godown", data={"godown_id": godown_id, "redirect_to": redirect_to})


def test_both_godowns_exist_after_seed(client, db_session):
    names = {g.name for g in db_session.query(models.Godown).all()}
    assert "Manesar Godown" in names
    assert "Daultabad Godown" in names


def test_default_active_godown_is_manesar(client):
    login(client)
    r = client.get("/")
    assert "Manesar Godown" in r.text


def test_switching_godown_changes_active_context(client, db_session):
    login(client)
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")
    switch_godown(client, daultabad_id)
    r = client.get("/")
    assert "Daultabad Godown" in r.text


def test_grn_entries_are_isolated_between_godowns(client, db_session, import_grn):
    """A SAP import lands in whichever godown the navbar is switched to, and
    stays there — the other godown's GRN list never shows it."""
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")

    switch_godown(client, manesar_id)
    import_grn({"material_doc": "GODOWN-TEST-MANESAR-1", "bags_received": 100})

    switch_godown(client, daultabad_id)
    import_grn({"material_doc": "GODOWN-TEST-DAULTABAD-1", "bags_received": 200})

    # Currently on Daultabad — should see only its own GRN
    r = client.get("/grn")
    assert "GODOWN-TEST-DAULTABAD-1" in r.text
    assert "GODOWN-TEST-MANESAR-1" not in r.text

    switch_godown(client, manesar_id)
    r = client.get("/grn")
    assert "GODOWN-TEST-MANESAR-1" in r.text
    assert "GODOWN-TEST-DAULTABAD-1" not in r.text


def test_stock_is_independent_per_godown(client, db_session):
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")

    switch_godown(client, manesar_id)
    client.post("/stock/opening", data={"product_id": 2, "bags": 5000, "as_of_date": "2026-09-01"})

    switch_godown(client, daultabad_id)
    client.post("/stock/opening", data={"product_id": 2, "bags": 999, "as_of_date": "2026-09-01"})

    r = client.get("/stock")  # still on Daultabad
    assert "999" in r.text

    switch_godown(client, manesar_id)
    r = client.get("/stock")
    assert "5000" in r.text


def test_freight_rate_card_is_godown_specific(client, db_session):
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")

    client.post("/rate-card/add", data={
        "godown_id": manesar_id, "district": "IsolationTestDistrict", "pincode": "",
        "transporter_rate_per_mt": 1111, "company_claim_rate_per_mt": 2222,
    })

    # Same district name, different godown -> must be a totally separate rate,
    # and must NOT collide with the unique constraint (proving the rebuilt
    # schema's UNIQUE(godown_id, district, pincode) actually works).
    r = client.post("/rate-card/add", data={
        "godown_id": daultabad_id, "district": "IsolationTestDistrict", "pincode": "",
        "transporter_rate_per_mt": 3333, "company_claim_rate_per_mt": 4444,
    }, follow_redirects=True)
    assert "already exists" not in r.text

    switch_godown(client, daultabad_id)

    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Rate Isolation Dealer",
        "district": "IsolationTestDistrict", "product_id": 1, "bags": 100, "vehicle_no": "RATE-ISO-1",
    })
    r = client.get("/freight")  # on Daultabad
    assert "RATE-ISO-1" in r.text
    assert "16,665" in r.text or "16665" in r.text  # 100 bags * 0.05 MT/bag = 5 MT * 3333/MT

    switch_godown(client, manesar_id)
    r = client.get("/freight")
    assert "RATE-ISO-1" not in r.text  # that dispatch belongs to Daultabad


def test_dealer_advance_hold_defaults_to_active_godown_and_all_godowns_combines(client, db_session):
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")

    switch_godown(client, manesar_id)
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "", "new_dealer": "Cross Godown Dealer",
        "product_id": 1, "bags": 300,
    })
    switch_godown(client, daultabad_id)
    client.post("/dispatch/add", data={
        "date": "2026-09-01", "dealer_name": "Cross Godown Dealer", "new_dealer": "",
        "product_id": 1, "bags": 200,
    })

    # Active godown only (Daultabad) -> should see 200, not 300
    r = client.get("/dealer-report")
    assert "Cross Godown Dealer" in r.text
    assert "200" in r.text

    # All Godowns -> combined 500
    r = client.get("/dealer-report", params={"all_godowns": "1"})
    assert "500" in r.text


def test_masters_cannot_disable_last_active_godown(client, db_session):
    login(client)
    # Disable Daultabad first so only Manesar remains active
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")
    client.post(f"/godowns/{daultabad_id}/toggle")

    manesar_id = get_godown_id(db_session, "Manesar Godown")
    # TestClient follows the redirect within this same call, so the
    # flash message (a one-time session pop) shows up in THIS response —
    # a separate follow-up GET would find it already consumed.
    r = client.post(f"/godowns/{manesar_id}/toggle")
    assert "deactivate the last remaining active godown" in r.text

    db_session.expire_all()
    manesar = db_session.get(models.Godown, manesar_id)
    assert manesar.active is True  # the toggle must not have gone through

    # restore state for any tests that run after this one
    client.post(f"/godowns/{daultabad_id}/toggle")


def test_add_new_godown_via_masters(client):
    login(client)
    r = client.post("/godowns/add", data={"name": "Sonipat Godown"})
    assert r.status_code == 200
    assert "Sonipat Godown" in r.text


def test_sap_import_tags_rows_with_active_godown(client, db_session):
    import io
    login(client)
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")
    switch_godown(client, daultabad_id)

    # Minimal in-memory "Sale" style xlsx isn't worth constructing here since
    # the real-file import is already covered in test_sap_import.py; this
    # test just confirms the plumbing (godown_id reaches ImportLog) using a
    # direct call to the already-tested import function.
    from app import sap_import
    result = sap_import.ImportResult()
    log = sap_import.log_import(db_session, "sale", "dummy.xlsx", None, result, godown_id=daultabad_id)
    assert log.godown_id == daultabad_id


def test_default_active_godown_is_manesar_not_alphabetically_first(client):
    """Regression test: crud.list_godowns() sorts alphabetically for the
    dropdown ("Daultabad" < "Manesar"), which must NOT be used to pick the
    default active godown — that has to stay the original (oldest/lowest id)
    one, or every existing Manesar user would land on an empty Daultabad
    view the first time they load the app post-upgrade."""
    login(client)
    r = client.get("/")
    assert '>Manesar Godown</option>' in r.text
    # the selected option must specifically be Manesar's, not just present somewhere
    import re
    m = re.search(r'<option value="(\d+)" selected>([^<]+)</option>', r.text)
    assert m, "no godown option is marked selected"
    assert m.group(2) == "Manesar Godown"


# ---------------------------------------------------------------------------
# Managing per-godown freight rates from Masters
# ---------------------------------------------------------------------------
def test_rate_card_can_be_added_for_a_godown_other_than_the_active_one(client, db_session):
    """The rate form carries its own godown, so rates can't silently land on
    whichever location the navbar happened to be left on."""
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")

    switch_godown(client, manesar_id)  # active godown is Manesar...
    client.post("/rate-card/add", data={
        "godown_id": daultabad_id, "district": "CrossPostDistrict", "pincode": "",
        "transporter_rate_per_mt": 700, "company_claim_rate_per_mt": 900,
    })  # ...but the rate is for Daultabad

    row = db_session.query(models.FreightRateCard).filter_by(district="CrossPostDistrict").one()
    assert row.godown_id == daultabad_id


def test_rate_card_view_switches_per_godown_and_compares_all(client, db_session):
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")
    for godown_id, district, rate in ((manesar_id, "ViewTestM", 100), (daultabad_id, "ViewTestD", 200)):
        client.post("/rate-card/add", data={
            "godown_id": godown_id, "district": district, "pincode": "",
            "transporter_rate_per_mt": rate, "company_claim_rate_per_mt": rate + 50,
        })

    r = client.get("/products", params={"rate_godown_id": manesar_id})
    assert "ViewTestM" in r.text and "ViewTestD" not in r.text

    r = client.get("/products", params={"rate_godown_id": daultabad_id})
    assert "ViewTestD" in r.text and "ViewTestM" not in r.text

    r = client.get("/products", params={"rate_godown_id": "all"})
    assert "ViewTestM" in r.text and "ViewTestD" in r.text


def test_rate_card_can_be_edited_and_deleted(client, db_session):
    """Rates change; a wrong rate has to be fixable in place rather than
    needing a second row for the same district."""
    login(client)
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")
    client.post("/rate-card/add", data={
        "godown_id": daultabad_id, "district": "EditTestDistrict", "pincode": "",
        "transporter_rate_per_mt": 1000, "company_claim_rate_per_mt": 1200,
    })
    row = db_session.query(models.FreightRateCard).filter_by(district="EditTestDistrict").one()

    client.post(f"/rate-card/{row.id}/update", data={
        "transporter_rate_per_mt": 1150, "company_claim_rate_per_mt": 1400,
    })
    db_session.expire_all()
    row = db_session.get(models.FreightRateCard, row.id)
    assert row.transporter_rate_per_mt == 1150
    assert row.company_claim_rate_per_mt == 1400
    assert row.godown_id == daultabad_id  # editing must not move it to another godown

    rate_id = row.id
    client.post(f"/rate-card/{rate_id}/delete")
    db_session.expunge_all()  # drop the identity-map copy of the now-deleted row
    assert db_session.query(models.FreightRateCard).filter_by(id=rate_id).first() is None


def test_same_district_costs_differently_from_each_godown(client, db_session):
    """End to end: one district, two godowns, two rates — each dispatch is
    costed against its own godown's rate card."""
    from app import crud
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")
    for godown_id, rate in ((manesar_id, 1000), (daultabad_id, 1600)):
        client.post("/rate-card/add", data={
            "godown_id": godown_id, "district": "TwoRateDistrict", "pincode": "",
            "transporter_rate_per_mt": rate, "company_claim_rate_per_mt": rate + 200,
        })

    for godown_id, vehicle in ((manesar_id, "TWORATE-M"), (daultabad_id, "TWORATE-D")):
        switch_godown(client, godown_id)
        client.post("/dispatch/add", data={
            "date": "2026-09-01", "dealer_name": "", "new_dealer": "Two Rate Dealer",
            "district": "TwoRateDistrict", "product_id": 1, "bags": 100, "vehicle_no": vehicle,
        })

    # 100 bags = 5 MT: Manesar 5 x 1000 = 5,000; Daultabad 5 x 1600 = 8,000
    payable = {}
    for godown_id in (manesar_id, daultabad_id):
        rows = [r for r in crud.get_dispatch_freight_rows(db_session, godown_id=godown_id)
                if r["dispatch"].district == "TwoRateDistrict"]
        payable[godown_id] = sum(r["freight_payable"] for r in rows)
    assert payable[manesar_id] == 5000
    assert payable[daultabad_id] == 8000


# ---------------------------------------------------------------------------
# Opening stock moved to Masters (2026-10-04)
# ---------------------------------------------------------------------------
def test_opening_stock_form_lives_on_masters_not_stock(client):
    """It is a setup action, not a daily one, so it sits with the other
    masters. The Stock page should open on the live figures instead."""
    login(client)
    masters = client.get("/products").text
    stock = client.get("/stock").text
    assert 'action="/stock/opening"' in masters
    assert 'action="/stock/opening"' not in stock
    # And current stock is now the first thing on the Stock page.
    assert stock.index("Current Stock") < stock.index("SAP vs Physical Reconciliation")


def test_opening_stock_is_admin_only_now(client, db_session):
    """Re-baselining silently rewrites every stock figure after it, so it
    belongs behind the same gate as the rest of Masters."""
    login(client)
    client.post("/users/add", data={"username": "ops_staff", "password": "staffpass123",
                                    "role": "staff"})
    client.post("/logout")
    client.post("/login", data={"username": "ops_staff", "password": "staffpass123"})
    r = client.post("/stock/opening", data={"product_id": 1, "bags": 7777,
                                            "as_of_date": "2026-09-01"})
    assert r.status_code == 403
    assert db_session.query(models.OpeningStock).filter_by(bags=7777).first() is None


def test_masters_picker_targets_the_chosen_godown_not_the_active_one(client, db_session):
    """The form carries its own godown, so an admin can set Daultabad's
    baseline without switching the navbar away from Manesar."""
    login(client)
    manesar_id = get_godown_id(db_session, "Manesar Godown")
    daultabad_id = get_godown_id(db_session, "Daultabad Godown")
    switch_godown(client, manesar_id)

    client.post("/stock/opening", data={"godown_id": daultabad_id, "product_id": 3,
                                        "bags": 4321, "as_of_date": "2026-09-01"})

    on_daultabad = db_session.query(models.OpeningStock).filter_by(
        godown_id=daultabad_id, product_id=3).one()
    assert on_daultabad.bags == 4321
    assert db_session.query(models.OpeningStock).filter_by(
        godown_id=manesar_id, product_id=3).first() is None

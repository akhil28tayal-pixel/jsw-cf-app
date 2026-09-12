"""Bag size is a property of the product, not of the company.

Cement here is a 50 kg bag (0.05 MT). JSW Microfine is a 20 kg bag, so 1 MT
is 50 bags of it, not 20. These tests pin that down everywhere the app
crosses between bags and tonnes: stock in MT, the MT->bags conversion on SAP
import, and the weight a truck is rated on for freight.
"""
from app import crud, models

MICROFINE = "JSW MICROFINE"


def login(client, username="admin", password="changeme123"):
    r = client.post("/login", data={"username": username, "password": password})
    assert r.status_code in (200, 303)
    return r


def microfine(client, db_session):
    """The 20 kg product, created through Masters like an admin would."""
    existing = db_session.query(models.Product).filter_by(name=MICROFINE).first()
    if existing is None:
        client.post("/products/add", data={"name": MICROFINE, "bag_weight_kg": 20})
        db_session.expire_all()
        existing = db_session.query(models.Product).filter_by(name=MICROFINE).one()
    return existing


def test_new_product_can_be_added_with_its_own_bag_size(client, db_session):
    login(client)
    p = microfine(client, db_session)
    assert p.bag_weight_mt == 0.02
    assert p.bag_weight_kg == 20


def test_cement_products_still_use_the_default_50kg_bag(client, db_session):
    login(client)
    ppc = db_session.query(models.Product).filter_by(name="JSW PPC").one()
    assert ppc.bag_weight_mt is None                       # nothing set...
    assert crud.product_bag_weight_mt(db_session, ppc) == 0.05  # ...falls back to 50 kg


def test_stock_in_mt_uses_the_products_own_bag_size(client, db_session):
    login(client)
    p = microfine(client, db_session)
    client.post("/stock/opening", data={"product_id": p.id, "bags": 500, "as_of_date": "2026-09-01"})

    active = crud.list_godowns(db_session)
    rows = {r["product"].name: r for r in crud.get_current_stock(db_session, min(g.id for g in active))}
    row = rows[MICROFINE]
    assert row["bag_weight_kg"] == 20
    assert row["current_mt"] == 10.0     # 500 bags x 20 kg = 10 MT (not 25 MT)


def test_sap_sale_import_converts_mt_to_bags_at_20kg(client, db_session, import_billing):
    """1 MT of Microfine on a SAP invoice is 50 bags, not 20."""
    login(client)
    p = microfine(client, db_session)
    db_session.add(models.SapProductMap(sap_code="FG-10-MICROFINE-BG", product_id=p.id))
    db_session.commit()

    # The helper works in bags and converts at 50 kg, so ask for the MT we want
    # directly: 1 MT -> the importer must read it as 50 bags.
    import_billing({"invoice_no": "MICRO-INV-1", "dealer": "Microfine Dealer",
                    "bags": 20, "amount": 42000, "sap_code": "FG-10-MICROFINE-BG"})  # 20 x 0.05 = 1 MT

    entry = db_session.query(models.Billing).filter_by(invoice_no="MICRO-INV-1").one()
    assert entry.bags == 50


def test_sap_material_in_import_converts_mt_to_bags_at_20kg(client, db_session, import_grn):
    login(client)
    p = microfine(client, db_session)
    if db_session.query(models.SapProductMap).filter_by(sap_code="FG-10-MICROFINE-BG").first() is None:
        db_session.add(models.SapProductMap(sap_code="FG-10-MICROFINE-BG", product_id=p.id))
        db_session.commit()

    import_grn({"material_doc": "MICRO-GRN-1", "bags_received": 40,      # 40 x 0.05 = 2 MT
                "sap_code": "FG-10-MICROFINE-BG"})

    entry = db_session.query(models.GRN).filter_by(sap_grn_no="MICRO-GRN-1").one()
    assert entry.bags_received == 100   # 2 MT at 20 kg/bag


def test_freight_weighs_a_microfine_truck_at_its_own_bag_size(client, db_session):
    """Freight is ₹/MT, so the same 400 bags is 8 MT of Microfine but 20 MT of
    cement — the truck must not be over-rated by 2.5x."""
    login(client)
    p = microfine(client, db_session)
    ppc = db_session.query(models.Product).filter_by(name="JSW PPC").one()
    godown_id = min(g.id for g in crud.list_godowns(db_session))

    client.post("/rate-card/add", data={
        "godown_id": godown_id, "district": "BagSizeDistrict", "pincode": "",
        "transporter_rate_per_mt": 1000, "company_claim_rate_per_mt": 1200,
    })
    client.post("/set-godown", data={"godown_id": godown_id, "redirect_to": "/"})
    for product_id, vehicle in ((p.id, "BAGSIZE-MICRO"), (ppc.id, "BAGSIZE-CEMENT")):
        client.post("/dispatch/add", data={
            "date": "2026-09-01", "dealer_name": "", "new_dealer": "Bag Size Dealer",
            "district": "BagSizeDistrict", "product_id": product_id, "bags": 400,
            "vehicle_no": vehicle,
        })

    rows = {r["dispatch"].vehicle_no: r for r in crud.get_dispatch_freight_rows(db_session, godown_id=godown_id)
            if r["dispatch"].district == "BagSizeDistrict"}
    assert rows["BAGSIZE-MICRO"]["weight_mt"] == 8.0        # 400 x 20 kg
    assert rows["BAGSIZE-MICRO"]["freight_payable"] == 8000
    assert rows["BAGSIZE-CEMENT"]["weight_mt"] == 20.0      # 400 x 50 kg
    assert rows["BAGSIZE-CEMENT"]["freight_payable"] == 20000


def test_bag_size_can_be_changed_and_cleared_from_masters(client, db_session):
    login(client)
    p = microfine(client, db_session)

    r = client.post(f"/products/{p.id}/bag-weight", data={"bag_weight_kg": "25"}, follow_redirects=True)
    assert "1 MT = 40 bags" in r.text
    db_session.expire_all()
    assert db_session.get(models.Product, p.id).bag_weight_mt == 0.025

    client.post(f"/products/{p.id}/bag-weight", data={"bag_weight_kg": ""})
    db_session.expire_all()
    assert db_session.get(models.Product, p.id).bag_weight_mt is None   # back to the 50 kg default

    # put it back for any test that runs after this one
    client.post(f"/products/{p.id}/bag-weight", data={"bag_weight_kg": "20"})
    db_session.expire_all()
    assert db_session.get(models.Product, p.id).bag_weight_mt == 0.02


def test_zero_bag_size_is_rejected(client, db_session):
    login(client)
    p = microfine(client, db_session)
    r = client.post(f"/products/{p.id}/bag-weight", data={"bag_weight_kg": "0"}, follow_redirects=True)
    assert "more than 0 kg" in r.text
    db_session.expire_all()
    assert db_session.get(models.Product, p.id).bag_weight_mt == 0.02   # unchanged


def test_staff_cannot_change_bag_size(client, db_session):
    login(client)
    p = microfine(client, db_session)
    client.post("/users/add", data={"username": "bagstaff", "password": "pass1234", "role": "staff"})
    client.get("/logout")
    login(client, "bagstaff", "pass1234")
    r = client.post(f"/products/{p.id}/bag-weight", data={"bag_weight_kg": "35"})
    assert r.status_code == 403
    db_session.expire_all()
    assert db_session.get(models.Product, p.id).bag_weight_mt == 0.02

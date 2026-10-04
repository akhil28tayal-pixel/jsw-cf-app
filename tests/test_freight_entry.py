"""Hand-entered freight, and the trip drill-down behind each freight row.

The rate card gives a default from ₹/MT by district. A typed figure is what
actually happened on that truck and overrides it — which is the only way a
district with no rate card entry ever gets costed.
"""
from app import crud, models
from tests.test_app import login


def _add_trip(client, **over):
    data = {"date": "2026-09-15", "dc_no": "FR-1", "dealer_name": "", "new_dealer": "Freight Entry Dealer",
            "district": "Nuh", "vehicle_no": "HR99FR0001", "transporter_name": "",
            "new_transporter": "Freight Entry Transport", "product_id": 1, "bags": 200}
    data.update(over)
    return client.post("/dispatch/add", data=data)


def _row_for(db, vehicle):
    rows = crud.get_dispatch_freight_rows(db)
    return next(r for r in rows if r["dispatch"].vehicle_no == vehicle)


def test_a_trip_with_no_rate_card_is_unrated_until_someone_types_a_figure(client, db_session):
    login(client)
    _add_trip(client, vehicle_no="HR99NR0001", district="Nowhere District")
    r = _row_for(db_session, "HR99NR0001")
    assert r["rate_found"] is False
    assert r["freight_payable"] is None       # not zero — zero would read as "free"

    client.post(f"/freight/trip/{r['dispatch'].id}", data={"freight_paid": "4500"})
    r = _row_for(db_session, "HR99NR0001")
    assert r["freight_payable"] == 4500
    assert r["manual_payable"] is True


def test_a_typed_figure_beats_the_rate_card(client, db_session, active_godown_id):
    login(client)
    client.post("/rate-card/add", data={"godown_id": active_godown_id(client), "district": "Rewari",
                                        "pincode": "", "transporter_rate_per_mt": 1000,
                                        "company_claim_rate_per_mt": 1200})
    _add_trip(client, vehicle_no="HR99OV0001", district="Rewari", bags=200)   # 10 MT
    r = _row_for(db_session, "HR99OV0001")
    assert r["card_payable"] == 10000                 # 10 MT x 1000
    assert r["freight_payable"] == 10000

    client.post(f"/freight/trip/{r['dispatch'].id}", data={"freight_paid": "8800",
                                                           "remarks": "return load"})
    r = _row_for(db_session, "HR99OV0001")
    assert r["card_payable"] == 10000                 # the card is still there
    assert r["freight_payable"] == 8800               # but the typed figure wins
    assert r["entry"].remarks == "return load"


def test_clearing_the_box_falls_back_to_the_rate_card(client, db_session, active_godown_id):
    login(client)
    client.post("/rate-card/add", data={"godown_id": active_godown_id(client), "district": "Jhajjar",
                                        "pincode": "", "transporter_rate_per_mt": 900,
                                        "company_claim_rate_per_mt": 1100})
    _add_trip(client, vehicle_no="HR99CL0001", district="Jhajjar", bags=200)
    d_id = _row_for(db_session, "HR99CL0001")["dispatch"].id
    client.post(f"/freight/trip/{d_id}", data={"freight_paid": "7000"})
    assert _row_for(db_session, "HR99CL0001")["freight_payable"] == 7000
    client.post(f"/freight/trip/{d_id}", data={"freight_paid": "", "has_paid": "1"})
    r = _row_for(db_session, "HR99CL0001")
    assert r["freight_payable"] == 9000               # back to 10 MT x 900
    assert r["manual_payable"] is False


def test_entering_paid_does_not_wipe_a_claim_entered_earlier(client, db_session):
    """Someone usually knows what they paid long before what they will claim."""
    login(client)
    _add_trip(client, vehicle_no="HR99KP0001", district="Palwal")
    d_id = _row_for(db_session, "HR99KP0001")["dispatch"].id
    client.post(f"/freight/trip/{d_id}", data={"freight_claim": "6000"})
    client.post(f"/freight/trip/{d_id}", data={"freight_paid": "5000"})
    entry = db_session.query(models.FreightEntry).filter_by(dispatch_id=d_id).one()
    assert entry.freight_claim == 6000 and entry.freight_paid == 5000


def test_staff_cannot_set_the_claim_side(client, db_session):
    """The claim figure is what is billed to JSW and drives the freight margin
    on the admin-only Claims page. Staff must not be able to set it."""
    login(client)
    _add_trip(client, vehicle_no="HR99ST0001", district="Sohna")
    d_id = _row_for(db_session, "HR99ST0001")["dispatch"].id
    client.post("/users/add", data={"username": "freight_staff", "password": "staffpass123",
                                    "role": "staff"})
    client.post("/logout")
    client.post("/login", data={"username": "freight_staff", "password": "staffpass123"})

    client.post(f"/freight/trip/{d_id}", data={"freight_paid": "3000", "freight_claim": "9999"})
    entry = db_session.query(models.FreightEntry).filter_by(dispatch_id=d_id).one()
    assert entry.freight_paid == 3000          # staff may record what was paid
    assert entry.freight_claim is None         # but not what is claimed

    # And the claim column is not even rendered for them.
    html = client.get("/freight/trips", params={"group": "vehicle", "key": "HR99ST0001"}).text
    assert "Claim from JSW" not in html


def test_drill_down_lists_only_that_groups_trips(client, db_session):
    login(client)
    _add_trip(client, vehicle_no="HR99DD0001", new_transporter="Drill Transport A", dc_no="DD-1")
    _add_trip(client, vehicle_no="HR99DD0002", new_transporter="Drill Transport B", dc_no="DD-2")

    by_vehicle = client.get("/freight/trips", params={"group": "vehicle", "key": "HR99DD0001"}).text
    assert "DD-1" in by_vehicle and "DD-2" not in by_vehicle

    by_transporter = client.get("/freight/trips",
                                params={"group": "transporter", "key": "Drill Transport B"}).text
    assert "DD-2" in by_transporter and "DD-1" not in by_transporter


def test_a_trip_with_a_blank_vehicle_can_still_be_opened(client, db_session):
    """The report groups those under a placeholder, so the link has to work."""
    login(client)
    _add_trip(client, vehicle_no="", dc_no="NOVEH-1", new_dealer="No Vehicle Dealer")
    html = client.get("/freight/trips", params={"group": "vehicle", "key": "(no vehicle no.)"}).text
    assert "NOVEH-1" in html


def test_typed_freight_reaches_the_claims_page(client, db_session):
    """Claims reads the margin as claimable minus paid, so a typed figure has
    to flow through to it — otherwise the margin silently stays wrong."""
    login(client)
    _add_trip(client, vehicle_no="HR99CP0001", district="Bawal", bags=200)
    d_id = _row_for(db_session, "HR99CP0001")["dispatch"].id
    client.post(f"/freight/trip/{d_id}", data={"freight_paid": "4000", "freight_claim": "5500"})

    report = crud.get_claims_report(db_session)
    assert report["freight_paid_to_transporters"] >= 4000
    assert report["freight_claimable_from_company"] >= 5500


def test_the_freight_report_links_each_row_to_its_trips(client):
    login(client)
    _add_trip(client, vehicle_no="HR99LK0001")
    html = client.get("/freight").text
    assert "/freight/trips?group=vehicle" in html
    assert "/freight/trips?group=transporter" in html


def test_an_absent_box_leaves_the_figure_alone(client, db_session):
    """FastAPI drops an empty form field, so without the hidden marker a save
    from a form that doesn't carry a side would silently wipe it."""
    login(client)
    _add_trip(client, vehicle_no="HR99AB0001", district="Hodal")
    d_id = _row_for(db_session, "HR99AB0001")["dispatch"].id
    client.post(f"/freight/trip/{d_id}", data={"freight_paid": "2500", "has_paid": "1"})
    # A post with no paid field and no marker at all — e.g. some other form.
    client.post(f"/freight/trip/{d_id}", data={"remarks": "just a note"})
    entry = db_session.query(models.FreightEntry).filter_by(dispatch_id=d_id).one()
    assert entry.freight_paid == 2500
    assert entry.remarks == "just a note"


def test_row_links_stay_correct_after_coming_back_from_a_drill_down(client):
    """The reported bug: open one vehicle, press Back, and every vehicle link
    then opened that same vehicle.

    Back returned to /freight carrying the stale group=/key= pair, each row
    link appended its own on top, and FastAPI keeps the LAST value of a
    repeated parameter — so every link resolved to whichever vehicle had been
    opened last. Only the filters may ride along.
    """
    login(client)
    _add_trip(client, vehicle_no="HR99BK0001", dc_no="BK-1", new_dealer="Back Dealer A")
    _add_trip(client, vehicle_no="HR99BK0002", dc_no="BK-2", new_dealer="Back Dealer B")

    # Land on /freight the way the Back button used to leave it.
    html = client.get("/freight", params={"group": "vehicle", "key": "HR99BK0001"}).text
    for href in [h for h in html.split('"') if h.startswith("/freight/trips")]:
        assert href.count("key=") == 1, f"link carries a duplicate key: {href}"
        assert href.count("group=") == 1, f"link carries a duplicate group: {href}"

    # And the second vehicle really does open its own trips from there.
    trips = client.get("/freight/trips", params={"group": "vehicle", "key": "HR99BK0002"}).text
    assert "BK-2" in trips and "BK-1" not in trips


def test_filters_survive_the_round_trip_but_grouping_does_not(client):
    login(client)
    _add_trip(client, vehicle_no="HR99FL0001", date="2026-09-18")
    html = client.get("/freight", params={"date_from": "2026-09-01", "date_to": "2026-09-30"}).text
    links = [h for h in html.split('"') if h.startswith("/freight/trips")]
    assert links, "no drill-down links rendered"
    assert all("date_from=2026-09-01" in h and "date_to=2026-09-30" in h for h in links)

    trips = client.get("/freight/trips", params={"group": "vehicle", "key": "HR99FL0001",
                                                 "date_from": "2026-09-01", "date_to": "2026-09-30"}).text
    # The back link keeps the filters and drops the grouping.
    back = [h for h in trips.split('"') if h.startswith("/freight?")]
    assert back, "no back link rendered"
    assert "date_from=2026-09-01" in back[0]
    assert "key=" not in back[0] and "group=" not in back[0]

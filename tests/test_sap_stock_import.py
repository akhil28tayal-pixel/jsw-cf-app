"""Importing the SAP actual-stock report (ALV HTML export).

The fixture is a trimmed copy of a real Daultabad export, so the quirks under
test are SAP's actual output and not an invented format: trailing-minus
negatives, comma thousands separators, nbsp padding, truncated headers, a
description whose full text lives in a title attribute, and a yellow totals
row carrying "*" instead of a material code.
"""
import datetime as dt

from app import crud, models
from app.sap_stock_import import parse_sap_number, parse_sap_stock_html, import_sap_stock_file
from tests.test_app import login

FIXTURE = "tests/fixtures/sap_stock_daultabad.html"


def _html():
    with open(FIXTURE, "rb") as fh:
        return fh.read()


def test_sap_number_format():
    assert parse_sap_number("1,325.000") == 1325.0
    assert parse_sap_number("245.320-") == -245.32      # SAP's trailing minus
    assert parse_sap_number("2,604.970-") == -2604.97
    assert parse_sap_number("0.000") == 0.0
    assert parse_sap_number("  11.300 ") == 11.3
    assert parse_sap_number("") is None
    assert parse_sap_number("MT") is None               # a unit is not a quantity


def test_parses_every_material_and_agrees_with_the_printed_total():
    """The report prints its own total. If our row sum doesn't match it, the
    parse is wrong — this is the check that catches a shifted column."""
    parsed = parse_sap_stock_html(_html())
    assert parsed.warnings == []
    assert len(parsed.rows) == 11
    by_code = {r.sap_code: r for r in parsed.rows}
    assert by_code["FG-10-PPC-CH-BG-HD"].closing_qty == 11.300
    assert by_code["FG-02-GGBS-MF-LP20"].closing_qty == 27.680
    assert by_code["FG-10-PPC-CH-BG-AC"].closing_qty == 66.800
    assert all(r.unit == "MT" for r in parsed.rows)
    assert parsed.total_qty == 157.830
    assert abs(sum(r.closing_qty for r in parsed.rows) - parsed.total_qty) < 1e-6


def test_totals_row_is_not_imported_as_a_product():
    parsed = parse_sap_stock_html(_html())
    assert not any(r.sap_code.startswith("*") for r in parsed.rows)


def test_full_description_comes_from_the_title_attribute():
    """SAP truncates the visible text; the full string is in a title attr."""
    parsed = parse_sap_stock_html(_html())
    jk = next(r for r in parsed.rows if r.sap_code == "FG-10-CH-JK-BG-LPP")
    assert jk.description == "PPC-JSWCL-CHD-JALKAVACH-LAMINATED BG"


def test_a_file_that_is_not_a_stock_report_is_reported_not_crashed():
    parsed = parse_sap_stock_html(b"<html><body><p>nothing here</p></body></html>")
    assert parsed.rows == []
    assert parsed.warnings and "No SAP stock table" in parsed.warnings[0]


def test_mt_is_converted_with_each_products_own_bag_size(client, db_session):
    """11.300 MT of PPC is 226 fifty-kg bags. The same MT figure of Microfine
    would be 565 bags, because its bag is 20 kg — the conversion must use the
    product, not a global constant."""
    login(client)
    g = db_session.query(models.Godown).order_by(models.Godown.id).first()
    d = dt.date(2026, 10, 3)
    result = import_sap_stock_file(db_session, _html(), godown_id=g.id, as_of_date=d,
                                  filename="sap_stock_daultabad.html")
    assert result.rows_total == 11
    assert result.rows_imported >= 5

    ppc = db_session.query(models.Product).filter_by(name="JSW PPC").first()
    snap = crud.latest_sap_stock(db_session, g.id, ppc.id)
    assert snap is not None
    assert abs(snap.bags - 11.300 / 0.05) < 0.001      # 226 bags
    assert snap.source == "html"
    assert snap.as_of_date == d


def test_unmapped_code_is_flagged_only_when_it_actually_holds_stock(client, db_session):
    """A SAP export lists the whole material master, most of it irrelevant to
    this godown and sitting at zero. Those must not nag the admin on the
    mapping page. A code WITH stock and no mapping is the opposite: it would
    silently leave a product out of the reconciliation, so it must be flagged.

    In this fixture the five zero-stock codes (Waterguard, PPC-BG-2S, AD STAR,
    Raksha, UFlex) are noise, while Microfine holds 27.680 MT and is not in
    the seeded code map — so exactly one code should be flagged."""
    login(client)
    g = db_session.query(models.Godown).order_by(models.Godown.id).first()

    # The suite shares one database, so set the precondition explicitly rather
    # than relying on whatever earlier modules left behind: this code unmapped.
    row = db_session.query(models.SapProductMap).filter_by(sap_code="FG-02-GGBS-MF-LP20").first()
    if row is None:
        row = models.SapProductMap(sap_code="FG-02-GGBS-MF-LP20", product_id=None)
        db_session.add(row)
    row.product_id = None
    db_session.commit()

    result = import_sap_stock_file(db_session, _html(), godown_id=g.id,
                                   as_of_date=dt.date(2026, 10, 4))
    # Every row is accounted for exactly once, and only the code with stock is
    # flagged. The exact skip count is not asserted: earlier modules in this
    # shared database may have mapped one of the zero-stock codes, which moves
    # it from "ignored" to "imported as zero" without changing what matters.
    assert (result.rows_imported + result.rows_skipped_unmapped
            + result.rows_skipped_other) == result.rows_total == 11
    assert result.rows_skipped_other >= 4          # zero stock, quietly ignored
    assert result.rows_skipped_unmapped == 1       # has stock, needs mapping
    assert result.unmapped_codes == {"FG-02-GGBS-MF-LP20"}

    # And once mapped, a re-upload picks it up — the whole point of flagging.
    microfine = db_session.query(models.Product).filter_by(name="JSW MICROFINE").first()
    if microfine is None:
        microfine = models.Product(name="JSW MICROFINE", bag_weight_mt=0.02)
        db_session.add(microfine)
        db_session.commit()
    row.product_id = microfine.id
    db_session.commit()

    again = import_sap_stock_file(db_session, _html(), godown_id=g.id,
                                 as_of_date=dt.date(2026, 10, 4))
    assert again.rows_skipped_unmapped == 0
    snap = crud.latest_sap_stock(db_session, g.id, microfine.id)
    # 27.680 MT at a 20 kg bag is 1,384 bags - not the 554 a 50 kg bag implies.
    assert abs(snap.bags - 1384) < 0.001


def test_reupload_of_the_same_godown_and_date_replaces(client, db_session):
    login(client)
    g = db_session.query(models.Godown).order_by(models.Godown.id).first()
    d = dt.date(2026, 10, 5)
    import_sap_stock_file(db_session, _html(), godown_id=g.id, as_of_date=d)
    first = db_session.query(models.SapStockSnapshot).filter_by(godown_id=g.id, as_of_date=d).count()
    import_sap_stock_file(db_session, _html(), godown_id=g.id, as_of_date=d)
    second = db_session.query(models.SapStockSnapshot).filter_by(godown_id=g.id, as_of_date=d).count()
    assert first == second and first > 0


def test_upload_through_the_endpoint_targets_the_chosen_godown(client, db_session):
    """The form's godown wins over the navbar's active godown — the two files
    look identical, so the wrong one must not be silently overwritten."""
    login(client)
    godowns = db_session.query(models.Godown).order_by(models.Godown.id).all()
    target = godowns[-1]
    d = "2026-10-06"
    r = client.post("/import/sap-stock",
                    data={"as_of_date": d, "godown_id": target.id},
                    files={"file": ("sap_stock_daultabad.html", _html(), "text/html")},
                    follow_redirects=True)
    assert r.status_code == 200
    rows = db_session.query(models.SapStockSnapshot).filter_by(
        godown_id=target.id, as_of_date=dt.date(2026, 10, 6)).all()
    assert rows
    log = db_session.query(models.ImportLog).filter_by(import_type="sap_stock").order_by(
        models.ImportLog.id.desc()).first()
    assert log is not None and log.godown_id == target.id


def test_several_sap_codes_mapping_to_one_product_are_totalled(client, db_session):
    """Concreel HD ACE and Concreel HD Raksha are both "JSW ACE" here. A real
    export had ACE at 66.800 MT followed by Raksha at 0.000 — writing each row
    on its own made the last one win and zeroed the stock. They must sum."""
    login(client)
    g = db_session.query(models.Godown).order_by(models.Godown.id).first()
    ace = db_session.query(models.Product).filter_by(name="JSW ACE").first()

    for code in ("FG-10-PPC-CH-BG-AC", "FG-10-PPC-CH-BG-RA"):
        row = db_session.query(models.SapProductMap).filter_by(sap_code=code).first()
        if row is None:
            row = models.SapProductMap(sap_code=code)
            db_session.add(row)
        row.product_id = ace.id
    db_session.commit()

    d = dt.date(2026, 11, 30)
    result = import_sap_stock_file(db_session, _html(), godown_id=g.id, as_of_date=d)

    snap = db_session.query(models.SapStockSnapshot).filter_by(
        godown_id=g.id, product_id=ace.id, as_of_date=d).one()
    # 66.800 MT + 0.000 MT at a 50 kg bag = 1336 bags, not 0.
    assert abs(snap.bags - 1336) < 0.001
    assert any("2 SAP codes totalled" in m for m in result.messages)

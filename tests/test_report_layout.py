"""Layout guarantees that are easy to lose in a refactor.

A totals row vanished once during a card reorder on the Stock page and the
page still rendered perfectly well without it — which is exactly why it needs
a test rather than an eye.
"""
import re

from tests.test_app import login

# Pages whose tables count something, with how many totals rows each should
# have. Claims is a statement of its own and Masters/Import/Backup are
# configuration, so none of them carry one.
PAGES_WITH_TOTALS = {
    "/": 2,                 # open dealer positions + stock by product
    "/stock": 2,            # current stock + SAP reconciliation
    "/grn": 1,
    "/dispatch": 1,
    "/billing": 1,
    "/dealer-report": 1,
    "/freight": 2,          # by vehicle + by transporter
}


def test_every_register_still_closes_with_a_totals_row(client, import_billing):
    login(client)
    # Give the registers something to total, so an empty page can't pass by
    # rendering the "no rows" branch.
    import_billing({"invoice_no": "LAY-1", "dealer": "Layout Dealer", "bags": 40})
    client.post("/dispatch/add", data={"date": "2026-09-09", "dealer_name": "",
                                       "new_dealer": "Layout Dealer", "product_id": 1,
                                       "bags": 25, "district": "Gurugram"})
    missing = {}
    for path, expected in PAGES_WITH_TOTALS.items():
        html = client.get(path).text
        found = len(re.findall(r"<tfoot>", html))
        if found != expected:
            missing[path] = f"expected {expected} totals row(s), found {found}"
    assert not missing, missing


def test_every_page_carries_the_report_header(client):
    """The header is what names the report, its godown and when it was run —
    and in print it is the letterhead."""
    login(client)
    for path in list(PAGES_WITH_TOTALS) + ["/claims", "/import", "/products", "/backup"]:
        html = client.get(path).text
        assert 'class="report-head"' in html, f"{path} has no report header"
        assert "A T TRADING CO" in html, f"{path} letterhead is empty — macro imported without context?"
        assert "IST" in html, f"{path} has no generated-at stamp"


def test_numbers_are_not_formatted_inline_anywhere(client):
    """All quantities go through the shared bags/signed_bags/mt/inr filters.
    An inline %.0f in a template is how the pages drifted apart last time."""
    import pathlib
    offenders = []
    for tpl in pathlib.Path("app/templates").glob("*.html"):
        for m in re.finditer(r'"%[+]?\.\d f?"\|format|"%[+]?\.\df"\|format', tpl.read_text()):
            line = tpl.read_text()[:m.start()].count("\n") + 1
            # Form input values legitimately need a fixed-decimal string.
            ctx = tpl.read_text()[max(0, m.start() - 120):m.start()]
            if "value=" in ctx:
                continue
            offenders.append(f"{tpl.name}:{line}")
    assert not offenders, f"inline number formatting: {offenders}"

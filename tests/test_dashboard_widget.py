"""Dashboard 'Dealers with Advance / Hold' widget: every open position, no settled ones."""
from tests.test_app import login


def _widget(html):
    return html.split("Dealers with Advance / Hold", 1)[1].split("Current Stock by Product", 1)[0]


def test_widget_shows_advance_and_hold_but_not_settled(client, import_billing):
    login(client)
    # Advance: billed 700, nothing dispatched
    import_billing({"invoice_no": "WID-1", "dealer": "Widget Advance Dealer", "bags": 700})
    # Hold: dispatched 250, nothing billed
    client.post("/dispatch/add", data={"date": "2026-09-02", "dealer_name": "", "new_dealer": "Widget Hold Dealer",
                                       "product_id": 1, "bags": 250, "district": "Gurugram"})
    # Settled: billed 100, dispatched 100
    import_billing({"invoice_no": "WID-2", "dealer": "Widget Settled Dealer", "bags": 100})
    client.post("/dispatch/add", data={"date": "2026-09-03", "dealer_name": "Widget Settled Dealer", "new_dealer": "",
                                       "product_id": 1, "bags": 100, "district": "Gurugram"})

    w = _widget(client.get("/").text)
    assert "Widget Advance Dealer" in w
    assert "Widget Hold Dealer" in w
    assert "Widget Settled Dealer" not in w
    assert "badge-settled" not in w
    # biggest exposure first: 700 advance before 250 hold
    assert w.index("Widget Advance Dealer") < w.index("Widget Hold Dealer")
    assert ">-250<" in w and ">700<" in w

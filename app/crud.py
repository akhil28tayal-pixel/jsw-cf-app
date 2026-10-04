import datetime as dt
from collections import defaultdict
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "bag_weight_mt": "0.05",              # 50kg bag = 0.05 MT
    "commission_rate_per_bag": "2.5",     # ₹ per bag dispatched, claimed from JSW
}


def get_setting(db: Session, key: str) -> str:
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    if row:
        return row.value
    return DEFAULT_SETTINGS.get(key, "")


def set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    if row:
        row.value = value
    else:
        row = models.Setting(key=key, value=value)
        db.add(row)
    db.commit()


def bag_weight_mt(db: Session) -> float:
    """The house default: a 50 kg cement bag = 0.05 MT."""
    return float(get_setting(db, "bag_weight_mt") or 0.05)


def product_bag_weight_mt(db: Session, product) -> float:
    """MT per bag FOR THIS PRODUCT. Bag size is a property of the product, not
    of the company: JSW Microfine is a 20 kg bag (0.02 MT — 50 bags to the
    tonne), while the cements are 50 kg. A product with nothing set falls back
    to the global setting, so this is only ever a correction, never a surprise.

    Everything that crosses between bags and tonnes goes through here — stock
    in MT, the MT→bags conversion on SAP import, and the freight weight a
    truck is rated on — so one product's bag size can't be right in one place
    and wrong in another."""
    weight = getattr(product, "bag_weight_mt", None) if product is not None else None
    if weight:
        return float(weight)
    return bag_weight_mt(db)


def commission_rate_per_bag(db: Session) -> float:
    return float(get_setting(db, "commission_rate_per_bag") or 0)


# ---------------------------------------------------------------------------
# Masters: Godown / Product / Dealer / Transporter (identical shape, small helpers)
#
# Godowns, like Dealers and Transporters, are a shared master list — but
# unlike them, "which godown" is also the scoping dimension for every
# stock/GRN/dispatch/billing/freight-rate row. list_godowns()/add_godown()
# manage the master list itself; the active godown for a given request is
# resolved separately in app/godown_context.py.
# ---------------------------------------------------------------------------
def _list_active(db: Session, model):
    return db.query(model).filter(model.active == True).order_by(model.name).all()  # noqa: E712


def _get_or_create(db: Session, model, name: str):
    name = name.strip()
    obj = db.query(model).filter(func.lower(model.name) == name.lower()).first()
    if obj:
        if not obj.active:
            obj.active = True
            db.commit()
        return obj
    obj = model(name=name)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_godowns(db: Session):
    return _list_active(db, models.Godown)


def add_godown(db: Session, name: str):
    return _get_or_create(db, models.Godown, name)


def list_products(db: Session):
    return _list_active(db, models.Product)


def add_product(db: Session, name: str, bag_weight_kg: Optional[float] = None):
    product = _get_or_create(db, models.Product, name)
    if bag_weight_kg:
        set_product_bag_weight_kg(db, product, bag_weight_kg)
    return product


def set_product_bag_weight_kg(db: Session, product, bag_weight_kg: Optional[float]):
    """Bag size is entered in kg (what's printed on the bag) and stored in MT
    (what every calculation uses). Blank/zero clears it back to the default."""
    if product is None:
        return None
    product.bag_weight_mt = (float(bag_weight_kg) / 1000.0) if bag_weight_kg else None
    db.commit()
    return product


def list_dealers(db: Session):
    return _list_active(db, models.Dealer)


def add_dealer(db: Session, name: str):
    return _get_or_create(db, models.Dealer, name)


def list_transporters(db: Session):
    return _list_active(db, models.Transporter)


def add_transporter(db: Session, name: str, contact: Optional[str] = None):
    t = _get_or_create(db, models.Transporter, name)
    if contact:
        t.contact = contact
        db.commit()
    return t


# ---------------------------------------------------------------------------
# Opening stock & current stock (computed, never stored as a mutable balance —
# this avoids the classic bug where a running balance quietly drifts out of
# sync with the transactions that are supposed to explain it)
#
# Every one of these is scoped to a single godown — Manesar's stock and
# Daultabad's stock are independent, computed the same way, from each
# godown's own GRN/Dispatch rows.
# ---------------------------------------------------------------------------
def upsert_opening_stock(db: Session, godown_id: int, product_id: int, bags: float, as_of_date: dt.date):
    row = db.query(models.OpeningStock).filter(
        models.OpeningStock.godown_id == godown_id, models.OpeningStock.product_id == product_id
    ).first()
    if row:
        row.bags = bags
        row.as_of_date = as_of_date
    else:
        row = models.OpeningStock(godown_id=godown_id, product_id=product_id, bags=bags, as_of_date=as_of_date)
        db.add(row)
    db.commit()


def get_current_stock(db: Session, godown_id: int):
    """Current stock per active product, for ONE godown = opening balance +
    every GRN receipt on/after the opening date - every dispatch on/after
    the opening date, all scoped to that godown."""
    results = []
    for product in list_products(db):
        bw = product_bag_weight_mt(db, product)
        opening = db.query(models.OpeningStock).filter(
            models.OpeningStock.godown_id == godown_id, models.OpeningStock.product_id == product.id
        ).first()
        opening_bags = opening.bags if opening else 0.0
        as_of = opening.as_of_date if opening else dt.date(1970, 1, 1)

        received = db.query(func.coalesce(func.sum(models.GRN.bags_received), 0.0)).filter(
            models.GRN.godown_id == godown_id, models.GRN.product_id == product.id, models.GRN.date >= as_of
        ).scalar()
        dispatched = db.query(func.coalesce(func.sum(models.Dispatch.bags), 0.0)).filter(
            models.Dispatch.godown_id == godown_id, models.Dispatch.product_id == product.id,
            models.Dispatch.date >= as_of
        ).scalar()

        current_bags = opening_bags + received - dispatched
        results.append({
            "product": product,
            "opening_bags": opening_bags,
            "as_of_date": as_of,
            "received_bags": received,
            "dispatched_bags": dispatched,
            "current_bags": current_bags,
            "current_mt": current_bags * bw,
            "bag_weight_kg": round(bw * 1000),
        })
    return results


# ---------------------------------------------------------------------------
# Requirement #5 — Dealer-wise / product-wise Advance & Hold reconciliation
#
# Running balance per (dealer, product[, godown]) = total billed bags - total
# dispatched bags, across ALL transactions to date (order doesn't matter — a
# running net balance self-corrects the moment a matching entry on the other
# side shows up).
#   balance > 0  -> billed ahead of dispatch  -> ADVANCE (owed to dealer)
#   balance < 0  -> dispatched ahead of billing -> HOLD  (owed to JSW/company)
#   balance == 0 -> settled
#
# godown_id=None combines both godowns for this dealer/product — useful for
# total exposure to a dealer regardless of which location served them.
# ---------------------------------------------------------------------------
def get_dealer_advance_hold(db: Session, dealer_id: Optional[int] = None, product_id: Optional[int] = None,
                             godown_id: Optional[int] = None):
    billed = defaultdict(float)
    dispatched = defaultdict(float)

    bq = db.query(models.Billing.dealer_id, models.Billing.product_id, func.sum(models.Billing.bags))
    dq = db.query(models.Dispatch.dealer_id, models.Dispatch.product_id, func.sum(models.Dispatch.bags))
    if godown_id:
        bq = bq.filter(models.Billing.godown_id == godown_id)
        dq = dq.filter(models.Dispatch.godown_id == godown_id)
    bq = bq.group_by(models.Billing.dealer_id, models.Billing.product_id)
    dq = dq.group_by(models.Dispatch.dealer_id, models.Dispatch.product_id)

    for d_id, p_id, total in bq.all():
        billed[(d_id, p_id)] = total or 0.0
    for d_id, p_id, total in dq.all():
        dispatched[(d_id, p_id)] = total or 0.0

    keys = set(billed) | set(dispatched)
    dealers = {d.id: d for d in db.query(models.Dealer).all()}
    products = {p.id: p for p in db.query(models.Product).all()}

    rows = []
    for (d_id, p_id) in keys:
        if dealer_id and d_id != dealer_id:
            continue
        if product_id and p_id != product_id:
            continue
        b = billed.get((d_id, p_id), 0.0)
        disp = dispatched.get((d_id, p_id), 0.0)
        balance = b - disp
        if balance > 1e-6:
            status = "Advance"
        elif balance < -1e-6:
            status = "Hold"
        else:
            status = "Settled"
        rows.append({
            "dealer": dealers.get(d_id),
            "product": products.get(p_id),
            "billed_bags": b,
            "dispatched_bags": disp,
            "balance_bags": balance,
            "status": status,
        })
    rows.sort(key=lambda r: (r["dealer"].name if r["dealer"] else "", r["product"].name if r["product"] else ""))
    return rows


# ---------------------------------------------------------------------------
# Requirement #6 — Truck-wise / transporter-wise freight, rated by district/pincode
#
# Rate cards are godown-specific (freight from Manesar to a district is a
# different number than freight from Daultabad to the same district).
# ---------------------------------------------------------------------------
def find_rate_card(db: Session, godown_id: int, district: Optional[str], pincode: Optional[str]):
    q = db.query(models.FreightRateCard).filter(models.FreightRateCard.godown_id == godown_id)
    if pincode:
        row = q.filter(models.FreightRateCard.pincode == pincode).first()
        if row:
            return row
    if district:
        row = q.filter(
            func.lower(models.FreightRateCard.district) == district.strip().lower(),
            models.FreightRateCard.pincode.is_(None),
        ).first()
        if row:
            return row
        row = q.filter(func.lower(models.FreightRateCard.district) == district.strip().lower()).first()
        if row:
            return row
    return None


def get_dispatch_freight_rows(db: Session, godown_id: Optional[int] = None, date_from=None, date_to=None):
    q = db.query(models.Dispatch)
    if godown_id:
        q = q.filter(models.Dispatch.godown_id == godown_id)
    if date_from:
        q = q.filter(models.Dispatch.date >= date_from)
    if date_to:
        q = q.filter(models.Dispatch.date <= date_to)

    dispatches = q.order_by(models.Dispatch.date, models.Dispatch.id).all()

    # One query for the hand-entered amounts rather than one per trip.
    entries = {}
    if dispatches:
        ids = [d.id for d in dispatches]
        for e in db.query(models.FreightEntry).filter(models.FreightEntry.dispatch_id.in_(ids)).all():
            entries[e.dispatch_id] = e

    rows = []
    for d in dispatches:
        rate = find_rate_card(db, d.godown_id, d.district, d.pincode)
        # Freight is charged per MT, so the truck's weight has to use the bag
        # size of the product actually on it — 400 bags of Microfine is 8 MT,
        # not 20 MT.
        weight_mt = (d.bags or 0) * product_bag_weight_mt(db, d.product)
        transporter_rate = rate.transporter_rate_per_mt if rate else None
        company_rate = rate.company_claim_rate_per_mt if rate else None

        card_payable = (weight_mt * transporter_rate) if transporter_rate is not None else None
        card_claimable = (weight_mt * company_rate) if company_rate is not None else None

        # A typed amount is what actually happened, so it beats the card. Each
        # side is decided on its own: someone may know what they paid long
        # before they know what they will claim.
        entry = entries.get(d.id)
        payable = entry.freight_paid if (entry and entry.freight_paid is not None) else card_payable
        claimable = entry.freight_claim if (entry and entry.freight_claim is not None) else card_claimable

        rows.append({
            "dispatch": d,
            "weight_mt": weight_mt,
            "rate_found": rate is not None,
            "transporter_rate_per_mt": transporter_rate,
            "company_rate_per_mt": company_rate,
            "card_payable": card_payable,
            "card_claimable": card_claimable,
            "entry": entry,
            "manual_payable": bool(entry and entry.freight_paid is not None),
            "manual_claimable": bool(entry and entry.freight_claim is not None),
            "freight_payable": payable,
            "freight_claimable": claimable,
        })
    return rows


def _aggregate_freight(rows, key_fn):
    agg = defaultdict(lambda: {"bags": 0.0, "weight_mt": 0.0, "freight_payable": 0.0,
                                "freight_claimable": 0.0, "trips": 0, "missing_rate": 0,
                                "manual_trips": 0, "key": None})
    for r in rows:
        a = agg[key_fn(r)]
        a["key"] = key_fn(r)
        if r["manual_payable"]:
            a["manual_trips"] += 1
        a["bags"] += r["dispatch"].bags or 0
        a["weight_mt"] += r["weight_mt"]
        a["trips"] += 1
        if r["freight_payable"] is not None:
            a["freight_payable"] += r["freight_payable"]
        else:
            a["missing_rate"] += 1
        if r["freight_claimable"] is not None:
            a["freight_claimable"] += r["freight_claimable"]
    return dict(sorted(agg.items()))


def get_truck_wise_report(db: Session, godown_id: Optional[int] = None, date_from=None, date_to=None):
    rows = get_dispatch_freight_rows(db, godown_id, date_from, date_to)
    return _aggregate_freight(rows, lambda r: r["dispatch"].vehicle_no or "(no vehicle no.)")


def get_transporter_wise_report(db: Session, godown_id: Optional[int] = None, date_from=None, date_to=None):
    rows = get_dispatch_freight_rows(db, godown_id, date_from, date_to)
    return _aggregate_freight(rows, lambda r: r["dispatch"].transporter.name if r["dispatch"].transporter else "(no transporter)")


# ---------------------------------------------------------------------------
# Requirement #7 — Commission & secondary-freight claim vs JSW (PROTECTED page)
# ---------------------------------------------------------------------------
def get_claims_report(db: Session, godown_id: Optional[int] = None, month: Optional[str] = None):
    """month: 'YYYY-MM' string, or None for all-time totals.
    godown_id: None combines both godowns into one claim total."""
    dq = db.query(models.Dispatch)
    if godown_id:
        dq = dq.filter(models.Dispatch.godown_id == godown_id)
    start = end = None
    if month:
        y, m = month.split("-")
        start = dt.date(int(y), int(m), 1)
        end = dt.date(int(y) + (1 if int(m) == 12 else 0), 1 if int(m) == 12 else int(m) + 1, 1)
        dq = dq.filter(models.Dispatch.date >= start, models.Dispatch.date < end)

    total_bags = dq.with_entities(func.coalesce(func.sum(models.Dispatch.bags), 0.0)).scalar()
    rate = commission_rate_per_bag(db)
    commission_amount = total_bags * rate

    rows = get_dispatch_freight_rows(
        db, godown_id=godown_id,
        date_from=start if month else None,
        date_to=(end - dt.timedelta(days=1)) if month else None,
    )
    freight_paid = sum(r["freight_payable"] for r in rows if r["freight_payable"] is not None)
    freight_claimable = sum(r["freight_claimable"] for r in rows if r["freight_claimable"] is not None)
    freight_margin = freight_claimable - freight_paid

    return {
        "month": month,
        "total_bags_dispatched": total_bags,
        "commission_rate_per_bag": rate,
        "commission_amount": commission_amount,
        "freight_paid_to_transporters": freight_paid,
        "freight_claimable_from_company": freight_claimable,
        "freight_margin": freight_margin,
        "total_claim_from_company": commission_amount + freight_claimable,
    }


def list_available_months(db: Session, godown_id: Optional[int] = None):
    """Distinct YYYY-MM months that have dispatch data, most recent first."""
    q = db.query(models.Dispatch.date).distinct()
    if godown_id:
        q = q.filter(models.Dispatch.godown_id == godown_id)
    dates = q.all()
    months = sorted({f"{d[0].year:04d}-{d[0].month:02d}" for d in dates if d[0]}, reverse=True)
    return months


# ---------------------------------------------------------------------------
# SAP stock reconciliation
#
# SAP and the godown disagree for reasons that are entirely legitimate, and
# the disagreement is predictable:
#
#   * ADVANCE — billed to the dealer, not yet dispatched. SAP dropped the
#     stock when the invoice was raised, but the bags are still in the
#     godown. Physical is HIGHER than SAP, so advance is ADDED to SAP.
#   * HOLD — dispatched, not yet billed. The bags have left the godown but
#     SAP still counts them. Physical is LOWER, so hold is SUBTRACTED.
#   * SHORTAGE — material short at a godown handover. SAP carries stock that
#     does not physically exist and never will, so it is SUBTRACTED too.
#
#       SAP stock + advance - hold - shortage  ==  physical stock
#
# Anything left over in `difference` is a real problem: a missed dispatch
# entry, a billing row not imported, or a genuine stock loss.
# ---------------------------------------------------------------------------
def latest_sap_stock(db: Session, godown_id: int, product_id: int):
    """The most recent SAP statement figure for this product at this godown."""
    return (
        db.query(models.SapStockSnapshot)
        .filter(models.SapStockSnapshot.godown_id == godown_id,
                models.SapStockSnapshot.product_id == product_id)
        .order_by(models.SapStockSnapshot.as_of_date.desc(), models.SapStockSnapshot.id.desc())
        .first()
    )


def sap_adjustment_bags(db: Session, godown_id: int, product_id: int) -> float:
    """Total recorded shortage for this product at this godown, in bags."""
    return float(db.query(func.coalesce(func.sum(models.SapStockAdjustment.bags), 0.0)).filter(
        models.SapStockAdjustment.godown_id == godown_id,
        models.SapStockAdjustment.product_id == product_id,
    ).scalar() or 0.0)


def get_stock_reconciliation(db: Session, godown_id: int):
    """One row per active product: SAP vs physical, with the adjustments that
    explain the gap. `sap_bags` is None where no statement has been uploaded
    yet, and the row then carries no expectation or difference rather than
    pretending SAP holds zero."""
    rows = []
    for s in get_current_stock(db, godown_id):
        product = s["product"]

        # Reuse the dealer report's own logic rather than re-deriving it, so
        # the two screens can never disagree about what is on advance or hold.
        positions = get_dealer_advance_hold(db, product_id=product.id, godown_id=godown_id)
        advance_bags = sum(r["balance_bags"] for r in positions if r["balance_bags"] > 0)
        hold_bags = sum(-r["balance_bags"] for r in positions if r["balance_bags"] < 0)

        snapshot = latest_sap_stock(db, godown_id, product.id)
        shortage_bags = sap_adjustment_bags(db, godown_id, product.id)

        sap_bags = snapshot.bags if snapshot else None
        if sap_bags is None:
            expected_bags = None
            difference_bags = None
        else:
            expected_bags = sap_bags + advance_bags - hold_bags - shortage_bags
            difference_bags = s["current_bags"] - expected_bags

        rows.append({
            "product": product,
            "sap_bags": sap_bags,
            "sap_as_of": snapshot.as_of_date if snapshot else None,
            "sap_source": snapshot.source if snapshot else None,
            "advance_bags": advance_bags,
            "hold_bags": hold_bags,
            "shortage_bags": shortage_bags,
            "expected_bags": expected_bags,
            "physical_bags": s["current_bags"],
            "difference_bags": difference_bags,
            # A fraction of a bag is rounding noise from an MT-denominated
            # statement, not a discrepancy worth flagging to anyone.
            "matched": difference_bags is not None and abs(difference_bags) < 0.5,
            "bag_weight_kg": s["bag_weight_kg"],
        })
    return rows


def upsert_sap_stock(db: Session, godown_id: int, product_id: int, as_of_date, bags: float,
                     source: str = "manual", filename: str = None, user_id: int = None):
    """Record (or replace) the SAP figure for one product on one date."""
    row = db.query(models.SapStockSnapshot).filter(
        models.SapStockSnapshot.godown_id == godown_id,
        models.SapStockSnapshot.product_id == product_id,
        models.SapStockSnapshot.as_of_date == as_of_date,
    ).first()
    if row:
        row.bags = bags
        row.source = source
        row.filename = filename
        row.created_by = user_id
        row.created_at = dt.datetime.utcnow()
    else:
        row = models.SapStockSnapshot(
            godown_id=godown_id, product_id=product_id, as_of_date=as_of_date,
            bags=bags, source=source, filename=filename, created_by=user_id,
        )
        db.add(row)
    db.commit()
    return row


def list_sap_stock(db: Session, godown_id: int, limit: int = 100):
    return (
        db.query(models.SapStockSnapshot)
        .filter(models.SapStockSnapshot.godown_id == godown_id)
        .order_by(models.SapStockSnapshot.as_of_date.desc(), models.SapStockSnapshot.id.desc())
        .limit(limit).all()
    )


def list_sap_adjustments(db: Session, godown_id: int = None):
    q = db.query(models.SapStockAdjustment)
    if godown_id:
        q = q.filter(models.SapStockAdjustment.godown_id == godown_id)
    return q.order_by(models.SapStockAdjustment.godown_id, models.SapStockAdjustment.product_id,
                      models.SapStockAdjustment.id).all()


def add_sap_adjustment(db: Session, godown_id: int, product_id: int, bags: float,
                       as_of_date=None, reason: str = None, user_id: int = None):
    row = models.SapStockAdjustment(
        godown_id=godown_id, product_id=product_id, bags=bags,
        as_of_date=as_of_date, reason=reason, created_by=user_id,
    )
    db.add(row)
    db.commit()
    return row


def delete_sap_adjustment(db: Session, adjustment_id: int) -> bool:
    row = db.query(models.SapStockAdjustment).get(adjustment_id)
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True


def list_opening_stock(db: Session, godown_id: int = None):
    """Every baseline currently set, for the Masters page. Ordered by godown
    then product so the two godowns read as separate blocks."""
    q = db.query(models.OpeningStock)
    if godown_id:
        q = q.filter(models.OpeningStock.godown_id == godown_id)
    return q.order_by(models.OpeningStock.godown_id, models.OpeningStock.product_id).all()


def freight_trips_for(db: Session, group: str, key: str, godown_id: Optional[int] = None,
                      date_from=None, date_to=None):
    """The individual trips behind one row of the freight report.

    `group` is "vehicle" or "transporter"; `key` is the label that row was
    grouped under, including the "(no vehicle no.)" / "(no transporter)"
    placeholders, so a row can always be opened even when the field is blank.
    """
    rows = get_dispatch_freight_rows(db, godown_id, date_from, date_to)
    if group == "transporter":
        def label(r):
            d = r["dispatch"]
            return d.transporter.name if d.transporter else "(no transporter)"
    else:
        def label(r):
            return r["dispatch"].vehicle_no or "(no vehicle no.)"
    return [r for r in rows if label(r) == key]


def upsert_freight_entry(db: Session, dispatch_id: int, freight_paid=None, freight_claim=None,
                         remarks: str = None, user_id: int = None, clear_paid: bool = False,
                         clear_claim: bool = False):
    """Record what a trip actually cost, and what will be claimed for it.

    A blank field leaves that side alone rather than wiping it, so someone
    entering the paid amount today does not erase a claim figure entered last
    week. Clearing is explicit via clear_paid / clear_claim.
    """
    entry = db.query(models.FreightEntry).filter(
        models.FreightEntry.dispatch_id == dispatch_id).first()
    if entry is None:
        entry = models.FreightEntry(dispatch_id=dispatch_id)
        db.add(entry)
    if clear_paid:
        entry.freight_paid = None
    elif freight_paid is not None:
        entry.freight_paid = freight_paid
    if clear_claim:
        entry.freight_claim = None
    elif freight_claim is not None:
        entry.freight_claim = freight_claim
    if remarks is not None:
        entry.remarks = remarks or None
    entry.created_by = user_id or entry.created_by
    db.commit()

    # A row holding nothing is noise; drop it so the trip reverts to the card.
    if entry.freight_paid is None and entry.freight_claim is None and not entry.remarks:
        db.delete(entry)
        db.commit()
        return None
    return entry


# ---------------------------------------------------------------------------
# Undoing an import
#
# GRN and Billing are SAP-only registers: nothing can be typed into them, so
# the only way to correct a bad upload is to remove what it brought in and
# import a corrected file. Because every row records the import that made it,
# that removal is exact rather than inferred from timestamps.
# ---------------------------------------------------------------------------
IMPORT_ROW_MODELS = {
    "billing": models.Billing,
    "grn": models.GRN,
    "sap_stock_snapshot": models.SapStockSnapshot,
}


def import_log_contents(db: Session, log_id: int) -> dict:
    """How many rows of each kind this import is still responsible for."""
    counts = {}
    for name, model in IMPORT_ROW_MODELS.items():
        counts[name] = db.query(func.count(model.id)).filter(
            model.import_log_id == log_id).scalar() or 0
    counts["total"] = sum(counts.values())
    return counts


def import_log_counts(db: Session, log_ids) -> dict:
    """The same thing for a page of logs, without a query per row."""
    out = {log_id: {"billing": 0, "grn": 0, "sap_stock_snapshot": 0, "total": 0}
           for log_id in log_ids}
    if not log_ids:
        return out
    for name, model in IMPORT_ROW_MODELS.items():
        rows = (db.query(model.import_log_id, func.count(model.id))
                .filter(model.import_log_id.in_(list(log_ids)))
                .group_by(model.import_log_id).all())
        for log_id, n in rows:
            if log_id in out:
                out[log_id][name] = n
                out[log_id]["total"] += n
    return out


def delete_import(db: Session, log_id: int) -> dict:
    """Remove everything one import brought in, then the log itself.

    Returns what was deleted. A SAP stock snapshot that was later corrected by
    hand is left alone: `source` stops being "html" once someone retypes it,
    and silently discarding that correction would be worse than leaving a row
    behind.
    """
    log = db.get(models.ImportLog, log_id)
    if log is None:
        return {}

    deleted = {}
    for name, model in IMPORT_ROW_MODELS.items():
        q = db.query(model).filter(model.import_log_id == log_id)
        if name == "sap_stock_snapshot":
            q = q.filter(model.source == "html")
        rows = q.all()
        for row in rows:
            db.delete(row)
        deleted[name] = len(rows)

    # Any snapshot kept above would dangle, so drop just the link.
    db.query(models.SapStockSnapshot).filter(
        models.SapStockSnapshot.import_log_id == log_id).update({"import_log_id": None})

    deleted["filename"] = log.filename
    deleted["import_type"] = log.import_type
    deleted["godown"] = log.godown.name if log.godown else None
    db.delete(log)
    db.commit()
    deleted["total"] = sum(v for k, v in deleted.items() if isinstance(v, int))
    return deleted

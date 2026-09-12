"""Billing — READ ONLY.

Billing rows are created exclusively by the SAP "Sale" import (`/import`).
There is deliberately no manual-entry endpoint: every invoice must exist in
SAP first, so Invoice No. stays the single source of truth for
de-duplication and the dealer advance/hold report can never be thrown off
by a hand-typed invoice that SAP doesn't have.
"""
from fastapi import APIRouter, Request, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, models
from app.query_utils import parse_date, parse_int

from app.templating import templates

router = APIRouter()


@router.get("/billing")
def billing_page(request: Request, date_from: str = Query(None), date_to: str = Query(None),
                  product_id: str = Query(None), dealer_id: str = Query(None), search: str = Query(None),
                  db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    date_from = parse_date(date_from)
    date_to = parse_date(date_to)
    product_id = parse_int(product_id)
    dealer_id = parse_int(dealer_id)
    q = db.query(models.Billing)
    if active_godown:
        q = q.filter(models.Billing.godown_id == active_godown.id)
    if date_from:
        q = q.filter(models.Billing.date >= date_from)
    if date_to:
        q = q.filter(models.Billing.date <= date_to)
    if product_id:
        q = q.filter(models.Billing.product_id == product_id)
    if dealer_id:
        q = q.filter(models.Billing.dealer_id == dealer_id)
    if search:
        q = q.filter(models.Billing.invoice_no.ilike(f"%{search}%"))
    entries = q.order_by(models.Billing.date.desc(), models.Billing.id.desc()).limit(500).all()
    last_import = db.query(models.ImportLog).filter(
        models.ImportLog.import_type == "sale"
    ).order_by(models.ImportLog.imported_at.desc()).first()
    return templates.TemplateResponse(request, "billing.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "products": crud.list_products(db), "dealers": crud.list_dealers(db), "entries": entries,
        "date_from": date_from, "date_to": date_to, "sel_product": product_id,
        "sel_dealer": dealer_id, "search": search or "",
        "last_import": last_import,
    })

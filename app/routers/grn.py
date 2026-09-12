"""GRN (inward) — READ ONLY.

GRN rows are created exclusively by the SAP "Material In" import
(`/import`). There is deliberately no manual-entry endpoint: every receipt
must exist in SAP first, so the app can never hold a GRN that SAP doesn't,
and Material Document stays the single source of truth for de-duplication.
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


@router.get("/grn")
def grn_page(request: Request, date_from: str = Query(None), date_to: str = Query(None),
             product_id: str = Query(None), vehicle_no: str = Query(None), search: str = Query(None),
             db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    date_from = parse_date(date_from)
    date_to = parse_date(date_to)
    product_id = parse_int(product_id)
    q = db.query(models.GRN)
    if active_godown:
        q = q.filter(models.GRN.godown_id == active_godown.id)
    if date_from:
        q = q.filter(models.GRN.date >= date_from)
    if date_to:
        q = q.filter(models.GRN.date <= date_to)
    if product_id:
        q = q.filter(models.GRN.product_id == product_id)
    if vehicle_no:
        q = q.filter(models.GRN.vehicle_no.ilike(f"%{vehicle_no}%"))
    if search:
        like = f"%{search}%"
        q = q.filter((models.GRN.sap_grn_no.ilike(like)) | (models.GRN.invoice_no.ilike(like)))
    entries = q.order_by(models.GRN.date.desc(), models.GRN.id.desc()).limit(500).all()
    last_import = db.query(models.ImportLog).filter(
        models.ImportLog.import_type == "material_in"
    ).order_by(models.ImportLog.imported_at.desc()).first()
    return templates.TemplateResponse(request, "grn.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "products": crud.list_products(db), "entries": entries,
        "date_from": date_from, "date_to": date_to, "sel_product": product_id,
        "vehicle_no": vehicle_no or "", "search": search or "",
        "last_import": last_import,
    })

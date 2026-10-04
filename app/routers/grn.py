"""GRN (inward) — SAP-import only, with an admin delete for corrections.

GRN rows are created exclusively by the SAP "Material In" import
(`/import`). There is deliberately no manual-entry endpoint: every receipt
must exist in SAP first, so the app can never hold a GRN that SAP doesn't,
and Material Document stays the single source of truth for de-duplication.

Deleting is the one write allowed here, and only for an admin. It exists to
undo a single bad row — a receipt imported against the wrong godown, or one
cancelled in SAP after import — without throwing away the whole import.
Because the importer de-duplicates on Material Document, removing a row
frees that number again, so re-uploading the correct Material In export
re-imports it rather than skipping it. The rule stays intact: whatever is in
GRN came from a SAP file.

Removing a receipt lowers stock by the bags it brought in, which is the
point — but it is also why this is admin-only.
"""
from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login, require_admin
from app.flash import flash, get_flashed_messages
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


@router.post("/grn/{grn_id}/delete")
def delete_grn(grn_id: int, request: Request, db: Session = Depends(get_db),
                user=Depends(require_admin)):
    """Delete one imported receipt. Admin only — see the module docstring."""
    entry = db.get(models.GRN, grn_id)
    if not entry:
        flash(request, "GRN entry not found.", "error")
        return RedirectResponse("/grn", status_code=303)
    summary = (f"GRN {entry.sap_grn_no or '(no number)'} — {entry.bags_received:.0f} bags of "
               f"{entry.product.name} received on {entry.date}"
               f"{' on ' + entry.vehicle_no if entry.vehicle_no else ''}")
    db.delete(entry)
    db.commit()
    flash(request, f"Deleted {summary}. Stock has dropped by those bags, and re-importing the "
                   "Material In export will bring this receipt back.")
    return RedirectResponse("/grn", status_code=303)

import datetime as dt

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login, require_admin
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, models, sap_import, sap_stock_import

from app.templating import templates

router = APIRouter()


@router.get("/import")
def import_page(request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    logs = db.query(models.ImportLog).order_by(models.ImportLog.imported_at.desc()).limit(20).all()
    unmapped = db.query(models.SapProductMap).filter(models.SapProductMap.product_id.is_(None)).all()
    return templates.TemplateResponse(request, "import.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "logs": logs, "unmapped": unmapped, "products": crud.list_products(db),
        "godowns": crud.list_godowns(db),
    })


@router.post("/import/sale")
async def import_sale(request: Request, file: UploadFile = File(...),
                       db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    content = await file.read()
    result = sap_import.import_sale_file(db, content, godown_id=active_godown.id, user_id=user.id)
    sap_import.log_import(db, "sale", file.filename, user.id, result, godown_id=active_godown.id)
    category = "warning" if (result.rows_skipped_unmapped or result.rows_skipped_other) else "success"
    flash(request, f"Sale file import into {active_godown.name}: {result.summary()}", category)
    return RedirectResponse("/import", status_code=303)


@router.post("/import/material-in")
async def import_material_in(request: Request, file: UploadFile = File(...),
                              db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    content = await file.read()
    result = sap_import.import_material_in_file(db, content, godown_id=active_godown.id, user_id=user.id)
    sap_import.log_import(db, "material_in", file.filename, user.id, result, godown_id=active_godown.id)
    category = "warning" if (result.rows_skipped_unmapped or result.rows_skipped_other) else "success"
    flash(request, f"Material In file import into {active_godown.name}: {result.summary()}", category)
    return RedirectResponse("/import", status_code=303)


@router.post("/import/map-product")
def map_product(request: Request, sap_code: str = Form(...), product_id: str = Form(""),
                 db: Session = Depends(get_db), user=Depends(require_admin)):
    row = db.query(models.SapProductMap).filter(models.SapProductMap.sap_code == sap_code).first()
    if row:
        row.product_id = int(product_id) if product_id else None
        db.commit()
        if product_id:
            flash(request, f'Mapped SAP code "{sap_code}" — re-run the import to pull in any rows that used it.')
        else:
            flash(request, f'SAP code "{sap_code}" marked as ignored.')
    return RedirectResponse("/import", status_code=303)


@router.post("/import/sap-stock")
async def import_sap_stock(request: Request, file: UploadFile = File(...),
                            as_of_date: dt.date = Form(...), godown_id: int = Form(...),
                            db: Session = Depends(get_db), user=Depends(require_login)):
    """Import the SAP actual-stock report for one godown, as of one date.

    The godown is chosen explicitly on the form rather than taken from the
    navbar: these files are per-godown and look identical, so uploading
    Daultabad's statement while Manesar is active would quietly overwrite the
    wrong figures. The date is asked for because the report's own From/To
    columns are 01.01.0000 / 31.12.9999 placeholders, not a statement date.
    """
    godown = db.query(models.Godown).get(godown_id)
    if godown is None:
        flash(request, "That godown no longer exists.", "warning")
        return RedirectResponse("/import", status_code=303)

    content = await file.read()
    result = sap_stock_import.import_sap_stock_file(
        db, content, godown_id=godown.id, as_of_date=as_of_date,
        user_id=user.id, filename=file.filename,
    )
    sap_import.log_import(db, "sap_stock", file.filename, user.id, result, godown_id=godown.id)
    if result.rows_imported == 0:
        category = "warning"
    elif result.rows_skipped_unmapped:
        category = "warning"
    else:
        category = "success"
    flash(request, f"SAP stock for {godown.name} as of {as_of_date}: {result.summary()} "
                   f"See the reconciliation on the Stock page.", category)
    return RedirectResponse("/stock", status_code=303)

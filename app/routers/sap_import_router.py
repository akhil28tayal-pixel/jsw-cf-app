from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login, require_admin
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, models, sap_import

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

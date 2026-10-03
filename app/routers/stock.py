import datetime as dt

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app import crud

from app.templating import templates

router = APIRouter()


@router.get("/stock")
def stock_page(request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    return templates.TemplateResponse(request, "stock.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "products": crud.list_products(db),
        "stock": crud.get_current_stock(db, active_godown.id) if active_godown else [],
        "reconciliation": crud.get_stock_reconciliation(db, active_godown.id) if active_godown else [],
        "sap_uploads": crud.list_sap_stock(db, active_godown.id, limit=20) if active_godown else [],
    })


@router.post("/stock/sap-figure")
def set_sap_stock_figure(request: Request, product_id: int = Form(...), bags: float = Form(...),
                          as_of_date: dt.date = Form(...), db: Session = Depends(get_db),
                          user=Depends(require_login)):
    """Enter one SAP stock figure by hand.

    The PDF upload fills these in automatically, but a hand-entry path has to
    exist: a statement that won't parse, or a single figure that needs
    correcting, must never leave the reconciliation unusable."""
    active_godown = get_active_godown(request, db)
    crud.upsert_sap_stock(db, active_godown.id, product_id, as_of_date, bags,
                          source="manual", user_id=user.id)
    flash(request, f"SAP stock figure saved for {as_of_date}.")
    return RedirectResponse("/stock", status_code=303)


@router.post("/stock/opening")
def set_opening_stock(request: Request, product_id: int = Form(...), bags: float = Form(...),
                       as_of_date: dt.date = Form(...), db: Session = Depends(get_db),
                       user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    crud.upsert_opening_stock(db, active_godown.id, product_id, bags, as_of_date)
    flash(request, f"Opening stock updated for {active_godown.name}.")
    return RedirectResponse("/stock", status_code=303)

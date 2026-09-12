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
    })


@router.post("/stock/opening")
def set_opening_stock(request: Request, product_id: int = Form(...), bags: float = Form(...),
                       as_of_date: dt.date = Form(...), db: Session = Depends(get_db),
                       user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    crud.upsert_opening_stock(db, active_godown.id, product_id, bags, as_of_date)
    flash(request, f"Opening stock updated for {active_godown.name}.")
    return RedirectResponse("/stock", status_code=303)

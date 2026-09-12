from fastapi import APIRouter, Request, Depends, Query, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_admin
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app import crud

from app.templating import templates

router = APIRouter()


@router.get("/claims")
def claims_page(request: Request, month: str = Query(None), all_godowns: str = Query(None),
                 db: Session = Depends(get_db), user=Depends(require_admin)):
    active_godown = get_active_godown(request, db)
    effective_godown_id = None if all_godowns else (active_godown.id if active_godown else None)
    report = crud.get_claims_report(db, godown_id=effective_godown_id, month=month)
    return templates.TemplateResponse(request, "claims.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "report": report, "months": crud.list_available_months(db, effective_godown_id),
        "commission_rate": crud.commission_rate_per_bag(db), "showing_all_godowns": bool(all_godowns),
    })


@router.post("/claims/commission-rate")
def update_commission_rate(request: Request, commission_rate_per_bag: float = Form(...),
                            db: Session = Depends(get_db), user=Depends(require_admin)):
    crud.set_setting(db, "commission_rate_per_bag", str(commission_rate_per_bag))
    flash(request, "Commission rate updated.")
    return RedirectResponse("/claims", status_code=303)

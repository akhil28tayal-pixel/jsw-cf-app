from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, models

from app.templating import templates

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    stock = crud.get_current_stock(db, active_godown.id) if active_godown else []
    advance_hold = crud.get_dealer_advance_hold(db, godown_id=active_godown.id if active_godown else None)
    total_advance = sum(r["balance_bags"] for r in advance_hold if r["status"] == "Advance")
    total_hold = sum(-r["balance_bags"] for r in advance_hold if r["status"] == "Hold")
    # Only dealer+product lines that are actually open, biggest exposure first.
    open_positions = sorted(
        (r for r in advance_hold if r["status"] != "Settled"),
        key=lambda r: -abs(r["balance_bags"]),
    )

    dispatch_q = db.query(models.Dispatch)
    grn_q = db.query(models.GRN)
    if active_godown:
        dispatch_q = dispatch_q.filter(models.Dispatch.godown_id == active_godown.id)
        grn_q = grn_q.filter(models.GRN.godown_id == active_godown.id)
    recent_dispatch = dispatch_q.order_by(models.Dispatch.date.desc(), models.Dispatch.id.desc()).limit(5).all()
    recent_grn = grn_q.order_by(models.GRN.date.desc(), models.GRN.id.desc()).limit(5).all()

    return templates.TemplateResponse(request, "dashboard.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "stock": stock, "total_advance": total_advance, "total_hold": total_hold,
        "recent_dispatch": recent_dispatch, "recent_grn": recent_grn,
        "open_positions": open_positions,
        "advance_hold_count": len(open_positions),
    })

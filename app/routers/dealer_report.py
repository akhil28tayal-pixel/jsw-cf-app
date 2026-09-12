from fastapi import APIRouter, Request, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import get_flashed_messages
from app.godown_context import get_active_godown
from app import crud
from app.query_utils import parse_int

from app.templating import templates

router = APIRouter()


@router.get("/dealer-report")
def dealer_report(request: Request, dealer_id: str = Query(None), product_id: str = Query(None),
                   godown_id: str = Query(None), all_godowns: str = Query(None),
                   db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    dealer_id = parse_int(dealer_id)
    product_id = parse_int(product_id)
    explicit_godown_id = parse_int(godown_id)
    # Default view: the active godown. "All Godowns" is an explicit opt-in
    # (useful for total dealer exposure across both locations).
    if all_godowns:
        effective_godown_id = None
    elif explicit_godown_id:
        effective_godown_id = explicit_godown_id
    else:
        effective_godown_id = active_godown.id if active_godown else None

    rows = crud.get_dealer_advance_hold(db, dealer_id=dealer_id, product_id=product_id,
                                         godown_id=effective_godown_id)
    return templates.TemplateResponse(request, "dealer_report.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "rows": rows, "dealers": crud.list_dealers(db), "products": crud.list_products(db),
        "godowns": crud.list_godowns(db),
        "sel_dealer": dealer_id, "sel_product": product_id,
        "sel_godown": explicit_godown_id, "showing_all_godowns": bool(all_godowns),
    })

from fastapi import APIRouter, Request, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import get_flashed_messages
from app.godown_context import get_active_godown
from app import crud
from app.query_utils import parse_date

from app.templating import templates

router = APIRouter()


@router.get("/freight")
def freight_page(request: Request, date_from: str = Query(None), date_to: str = Query(None),
                  all_godowns: str = Query(None),
                  db: Session = Depends(get_db), user=Depends(require_login)):
    active_godown = get_active_godown(request, db)
    date_from = parse_date(date_from)
    date_to = parse_date(date_to)
    effective_godown_id = None if all_godowns else (active_godown.id if active_godown else None)

    truck_report = crud.get_truck_wise_report(db, effective_godown_id, date_from, date_to)
    transporter_report = crud.get_transporter_wise_report(db, effective_godown_id, date_from, date_to)
    return templates.TemplateResponse(request, "freight.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "truck_report": truck_report, "transporter_report": transporter_report,
        "date_from": date_from, "date_to": date_to, "showing_all_godowns": bool(all_godowns),
    })

from fastapi import APIRouter, Request, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, models, reporting
from app.query_utils import parse_date

from app.templating import templates

router = APIRouter()

# Both freight sheets carry the same note, so both sheets start their table on
# the same row and look like one document rather than two.
FREIGHT_NOTE = ("Rated from the dispatching godown's own rate card, pincode first then district. "
                "This is what you pay transporters, not what you claim from JSW.")


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


@router.get("/freight/export")
def export_freight(request: Request, date_from: str = Query(None), date_to: str = Query(None),
                    all_godowns: str = Query(None),
                    db: Session = Depends(get_db), user=Depends(require_login)):
    """Freight payable as .xlsx — vehicle-wise and transporter-wise on two
    sheets, because they are two ways of reading the same trips and whoever
    receives this wants one or the other, not a merged table."""
    active_godown = get_active_godown(request, db)
    date_from = parse_date(date_from)
    date_to = parse_date(date_to)
    effective_godown_id = None if all_godowns else (active_godown.id if active_godown else None)
    scope = "All Godowns" if all_godowns else (active_godown.name if active_godown else "All Godowns")
    period = reporting.period_text(date_from, date_to)

    columns = [("Vehicle No.", 18), ("Trips", 8), ("Bags", 12), ("Weight (MT)", 13),
               ("Freight Payable (Rs)", 20), ("Trips Missing a Rate", 20)]

    def to_rows(report):
        return [[key, a["trips"], a["bags"], round(a["weight_mt"], 3),
                 round(a["freight_payable"], 2), a["missing_rate"] or ""]
                for key, a in report.items()]

    truck = crud.get_truck_wise_report(db, effective_godown_id, date_from, date_to)
    wb = reporting.write_report(
        "Freight Payable — Vehicle-wise", columns, to_rows(truck),
        sheet_name="By Vehicle", scope=scope, period=period, user=user.username,
        total_columns=(2, 3, 4, 5),
        number_formats={2: "#,##0", 3: "#,##0", 4: "#,##0.000", 5: '#,##0.00'},
        note=FREIGHT_NOTE,
    )

    # Same trips, grouped by transporter instead — a second sheet in the same
    # workbook rather than a second file.
    transporter = crud.get_transporter_wise_report(db, effective_godown_id, date_from, date_to)
    reporting.write_report(
        "Freight Payable — Transporter-wise",
        [("Transporter", 28)] + columns[1:], to_rows(transporter),
        sheet_name="By Transporter", scope=scope, period=period, user=user.username,
        total_columns=(2, 3, 4, 5),
        number_formats={2: "#,##0", 3: "#,##0", 4: "#,##0.000", 5: "#,##0.00"},
        note=FREIGHT_NOTE,
        wb=wb,
    )

    return reporting.xlsx_response(wb, reporting.report_filename("freight", scope, date_from, date_to))

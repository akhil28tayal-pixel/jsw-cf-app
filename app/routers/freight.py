from fastapi import APIRouter, Request, Depends, Query, Form
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, models, reporting
from app.query_utils import parse_date, parse_float

from app.templating import templates

router = APIRouter()

def _filter_query(date_from, date_to, all_godowns) -> str:
    """Just the filters, as a query string.

    Deliberately NOT request.url.query. Appending the whole query string meant
    that after one trip to a drill-down and back, /freight carried a stale
    group=/key= pair, every row link appended its own on top, and FastAPI —
    which keeps the LAST value of a repeated parameter — sent every vehicle to
    whichever one had been opened last.
    """
    parts = []
    if date_from:
        parts.append(("date_from", str(date_from)))
    if date_to:
        parts.append(("date_to", str(date_to)))
    if all_godowns:
        parts.append(("all_godowns", "1"))
    return urlencode(parts)

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
        "filter_query": _filter_query(date_from, date_to, all_godowns),
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


@router.get("/freight/trips")
def freight_trips(request: Request, group: str = Query("vehicle"), key: str = Query(...),
                   date_from: str = Query(None), date_to: str = Query(None),
                   all_godowns: str = Query(None),
                   db: Session = Depends(get_db), user=Depends(require_login)):
    """The individual trips behind one line of the freight report.

    Reached by clicking a vehicle or a transporter. Carries the same filters
    through, so the trips shown are exactly the ones that were totalled.
    """
    active_godown = get_active_godown(request, db)
    date_from = parse_date(date_from)
    date_to = parse_date(date_to)
    effective_godown_id = None if all_godowns else (active_godown.id if active_godown else None)

    trips = crud.freight_trips_for(db, group, key, effective_godown_id, date_from, date_to)
    return templates.TemplateResponse(request, "freight_trips.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "group": group, "group_key": key, "trips": trips,
        "date_from": date_from, "date_to": date_to,
        "showing_all_godowns": bool(all_godowns),
        "filter_query": _filter_query(date_from, date_to, all_godowns),
    })


@router.post("/freight/trip/{dispatch_id}")
def save_freight_for_trip(dispatch_id: int, request: Request,
                           freight_paid: str = Form(None), freight_claim: str = Form(None),
                           remarks: str = Form(None), back_to: str = Form("/freight"),
                           has_paid: str = Form(None), has_claim: str = Form(None),
                           db: Session = Depends(get_db), user=Depends(require_login)):
    """Save the freight actually agreed for one trip.

    Only an admin may touch the claim side: that figure is what is billed to
    JSW and it drives the freight margin on the Claims page, which staff are
    not shown at all. A staff member's post simply leaves it untouched.
    """
    dispatch = db.get(models.Dispatch, dispatch_id)
    if dispatch is None:
        flash(request, "That dispatch no longer exists.", "warning")
        return RedirectResponse("/freight", status_code=303)

    paid = parse_float(freight_paid)
    claim = parse_float(freight_claim) if user.role == "admin" else None

    # An empty box means "clear this figure", but FastAPI drops an empty form
    # field entirely rather than handing over "" — the same quirk query_utils
    # exists for — so an empty box and an absent one look identical here. The
    # form sends a hidden marker per side to tell them apart.
    is_admin = user.role == "admin"
    crud.upsert_freight_entry(
        db, dispatch_id,
        freight_paid=paid, freight_claim=claim, remarks=remarks, user_id=user.id,
        clear_paid=(has_paid is not None and paid is None),
        clear_claim=(is_admin and has_claim is not None and claim is None),
    )
    flash(request, f"Freight saved for {dispatch.vehicle_no or 'this trip'} on {dispatch.date}.")
    if not back_to.startswith("/"):
        back_to = "/freight"
    return RedirectResponse(back_to, status_code=303)

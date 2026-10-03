from fastapi import APIRouter, Request, Depends, Query, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_admin
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, reporting

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


@router.get("/claims/export")
def export_claims(request: Request, month: str = Query(None), all_godowns: str = Query(None),
                   db: Session = Depends(get_db), user=Depends(require_admin)):
    """The claim on JSW as .xlsx. Admin only, like the page — this carries the
    commission rate and the freight margin, which staff must not see."""
    active_godown = get_active_godown(request, db)
    effective_godown_id = None if all_godowns else (active_godown.id if active_godown else None)
    report = crud.get_claims_report(db, godown_id=effective_godown_id, month=month)
    scope = "All Godowns" if all_godowns else (active_godown.name if active_godown else "All Godowns")

    rate = report["commission_rate_per_bag"]
    rows = [
        ["Bags dispatched", report["total_bags_dispatched"], "bags"],
        [f"Handling commission at Rs {rate}/bag", report["commission_amount"], "Rs"],
        ["Secondary freight claimable from JSW", report["freight_claimable_from_company"], "Rs"],
        ["TOTAL CLAIM ON JSW", report["total_claim_from_company"], "Rs"],
        ["", "", ""],
        ["Freight actually paid to transporters", report["freight_paid_to_transporters"], "Rs"],
        ["Your freight margin", report["freight_margin"], "Rs"],
    ]
    wb = reporting.write_report(
        "Claims & Commission from JSW",
        [("Item", 42), ("Amount", 16), ("Unit", 8)], rows,
        sheet_name="Claim", scope=scope, period=(month or "All-time"), user=user.username,
        number_formats={2: "#,##0.00"},
        note="The freight rate claimed from JSW is deliberately not the rate paid to the "
             "transporter; the difference is the freight margin shown at the bottom.",
        # The rows are already a statement ending in its own total, and summing
        # a bag count together with rupee amounts would mean nothing.
        show_total_row=False,
        autofilter=False,
    )
    return reporting.xlsx_response(wb, reporting.report_filename("jsw-claim", scope))

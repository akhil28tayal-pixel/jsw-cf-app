import datetime as dt

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app import crud, reporting

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


@router.get("/stock/export")
def export_stock(request: Request, db: Session = Depends(get_db), user=Depends(require_login)):
    """Stock and the SAP reconciliation as .xlsx — two sheets, because they
    answer different questions: what is in the godown, and whether SAP agrees."""
    active_godown = get_active_godown(request, db)
    if active_godown is None:
        flash(request, "Pick a godown first.", "warning")
        return RedirectResponse("/stock", status_code=303)
    scope = active_godown.name

    stock = crud.get_current_stock(db, active_godown.id)
    wb = reporting.write_report(
        "Godown Stock",
        [("Product", 22), ("Bag (kg)", 10), ("Opening (Bags)", 14), ("Opening Date", 13),
         ("Received Since", 14), ("Dispatched Since", 16), ("Current (Bags)", 14), ("Current (MT)", 13)],
        [[s_["product"].name, s_["bag_weight_kg"], s_["opening_bags"], s_["as_of_date"],
          s_["received_bags"], s_["dispatched_bags"], s_["current_bags"], round(s_["current_mt"], 3)]
         for s_ in stock],
        sheet_name="Stock", scope=scope, user=user.username,
        total_columns=(3, 5, 6, 7, 8),
        number_formats={3: "#,##0", 5: "#,##0", 6: "#,##0", 7: "#,##0", 8: "#,##0.000",
                        4: "dd-mmm-yy"},
        note="Stock is computed, never stored: opening + GRN received - dispatched, from the "
             "opening date onward.",
    )

    rec = crud.get_stock_reconciliation(db, active_godown.id)
    reporting.write_report(
        "SAP vs Physical Reconciliation",
        [("Product", 22), ("SAP Stock", 12), ("SAP as of", 12), ("+ Advance", 12),
         ("- Hold", 12), ("- Shortage", 12), ("= Expected", 12), ("Physical", 12),
         ("Difference", 12), ("Status", 14)],
        [[r["product"].name,
          r["sap_bags"] if r["sap_bags"] is not None else "not uploaded",
          r["sap_as_of"] or "", r["advance_bags"], r["hold_bags"], r["shortage_bags"],
          r["expected_bags"] if r["expected_bags"] is not None else "",
          r["physical_bags"],
          r["difference_bags"] if r["difference_bags"] is not None else "",
          ("Matched" if r["matched"] else "Check") if r["sap_bags"] is not None else "Not uploaded"]
         for r in rec],
        sheet_name="SAP Reconciliation", scope=scope, user=user.username,
        total_columns=(4, 5, 6, 8),
        number_formats={2: "#,##0", 3: "dd-mmm-yy", 4: "#,##0", 5: "#,##0", 6: "#,##0",
                        7: "#,##0", 8: "#,##0", 9: "+#,##0;-#,##0;0"},
        note="SAP + Advance - Hold - Shortage should equal physical stock. SAP drops stock when "
             "an invoice is raised, not when the truck leaves.",
        wb=wb,
    )
    return reporting.xlsx_response(wb, reporting.report_filename("stock", scope))

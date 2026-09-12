import csv
import io
import shutil
import datetime as dt

from fastapi import APIRouter, Request, Depends
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.auth import require_admin
from app.flash import get_flashed_messages
from app import models

from app.templating import templates

router = APIRouter()


@router.get("/backup")
def backup_page(request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    counts = {
        "Products": db.query(models.Product).count(),
        "Dealers": db.query(models.Dealer).count(),
        "Transporters": db.query(models.Transporter).count(),
        "GRN entries": db.query(models.GRN).count(),
        "Dispatch entries": db.query(models.Dispatch).count(),
        "Billing entries": db.query(models.Billing).count(),
        "Users": db.query(models.User).count(),
    }
    is_sqlite = settings.DATABASE_URL.startswith("sqlite")
    return templates.TemplateResponse(request, "backup.html", {
        "user": user, "flashes": get_flashed_messages(request),
        "counts": counts, "is_sqlite": is_sqlite,
    })


@router.get("/backup/download-db")
def download_db(user=Depends(require_admin)):
    """Download a point-in-time copy of the live SQLite file. Copying it
    first (rather than serving the live file directly) avoids handing out a
    file that's being written to mid-download."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return {"error": "This deployment uses a non-SQLite database. Back it up with your database's own dump tool (e.g. pg_dump for Postgres)."}
    db_path = settings.DATABASE_URL.replace("sqlite:///", "")
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return FileResponse(
        backup_path, media_type="application/octet-stream",
        filename=f"jsw_cf_backup_{timestamp}.db",
    )


def _export_csv(rows, headers, row_to_list, filename):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow(row_to_list(r))
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/backup/export/grn")
def export_grn(db: Session = Depends(get_db), user=Depends(require_admin)):
    rows = db.query(models.GRN).order_by(models.GRN.date).all()
    return _export_csv(
        rows,
        ["Date", "SAP GRN No.", "Invoice No.", "Vehicle No.", "Product", "Bags Invoice",
         "Bags Received", "Shortage/Excess", "Source Plant", "Remarks"],
        lambda g: [g.date, g.sap_grn_no, g.invoice_no, g.vehicle_no, g.product.name,
                   g.bags_invoice, g.bags_received, g.shortage_excess, g.source_plant, g.remarks],
        "grn_export.csv",
    )


@router.get("/backup/export/dispatch")
def export_dispatch(db: Session = Depends(get_db), user=Depends(require_admin)):
    rows = db.query(models.Dispatch).order_by(models.Dispatch.date).all()
    return _export_csv(
        rows,
        ["Date", "DC No.", "Dealer", "Destination", "District", "Pincode", "Vehicle No.",
         "Transporter", "Product", "Bags", "Remarks"],
        lambda d: [d.date, d.dc_no, d.dealer.name if d.dealer else "", d.destination, d.district,
                   d.pincode, d.vehicle_no, d.transporter.name if d.transporter else "",
                   d.product.name, d.bags, d.remarks],
        "dispatch_export.csv",
    )


@router.get("/backup/export/billing")
def export_billing(db: Session = Depends(get_db), user=Depends(require_admin)):
    rows = db.query(models.Billing).order_by(models.Billing.date).all()
    return _export_csv(
        rows,
        ["Date", "Invoice No.", "Dealer", "Product", "Bags", "Amount", "Remarks"],
        lambda b: [b.date, b.invoice_no, b.dealer.name if b.dealer else "", b.product.name,
                   b.bags, b.amount, b.remarks],
        "billing_export.csv",
    )

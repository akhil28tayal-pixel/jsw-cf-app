"""Shared machinery for the .xlsx report downloads.

Five report pages export to Excel and they should all look like they came from
the same office: same navy header band, same title block naming the firm, the
report, the godown and the period, same bold totals row, same frozen header
and autofilter. Written once here rather than five times.

A sheet produced by `write_report` is laid out as:

    row 1   A T TRADING CO  -  <report title>          (bold, merged)
    row 2   Godown: ... | Period: ... | Generated: ...  (grey, merged)
    row 3   (blank)
    row 4   column headings                             (navy band, frozen)
    row 5+  data
    last    TOTAL + the totalled columns                (bold, tinted)
"""
import datetime as dt
import io
import re
from typing import Iterable, List, Optional, Sequence, Tuple

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TOTAL_FILL = PatternFill("solid", fgColor="DDEBF7")
TITLE_FONT = Font(bold=True, size=13, color="1F3864")
META_FONT = Font(size=9, color="595959")
THIN_TOP = Border(top=Side(style="thin", color="1F3864"))

FIRM_NAME = "A T TRADING CO"


def slug(text: str) -> str:
    """A filename-safe fragment. Blank becomes "all", never an empty segment."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "all"


def ist_stamp() -> str:
    """Generated-at in IST.

    Production runs in UTC, so a naive timestamp would be wrong by 5.5 hours on
    every file someone files away. IST has no daylight saving, so a fixed
    offset is exact and needs no tzdata in the container image.
    """
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.now(ist).strftime("%d %b %Y %H:%M IST")


def period_text(date_from=None, date_to=None, fallback: str = "All dates") -> str:
    if date_from and date_to:
        return f"{date_from} to {date_to}"
    if date_from:
        return f"From {date_from}"
    if date_to:
        return f"Up to {date_to}"
    return fallback


def report_filename(stem: str, scope: str = None, date_from=None, date_to=None) -> str:
    parts = [slug(stem)]
    if scope:
        parts.append(slug(scope))
    if date_from or date_to:
        parts.append(f"{date_from or 'start'}_to_{date_to or 'today'}")
    else:
        parts.append(dt.date.today().isoformat())
    return "_".join(parts) + ".xlsx"


def write_report(
    title: str,
    columns: Sequence[Tuple[str, int]],
    rows: Iterable[Sequence],
    *,
    sheet_name: str = "Report",
    scope: str = None,
    period: str = None,
    user: str = None,
    total_columns: Sequence[int] = (),
    number_formats: dict = None,
    note: str = None,
    wb: Optional[Workbook] = None,
    show_total_row: bool = True,
    autofilter: bool = True,
) -> Workbook:
    """Build a workbook, or add a sheet to one that already exists.

    `total_columns` are 1-based column numbers to sum; `number_formats` maps a
    1-based column number to an Excel format string. Pass `wb` to add a second
    sheet to an existing workbook (the freight report is the same trips read
    two ways, so it ships as two sheets). Pass show_total_row=False where a
    TOTAL line would be nonsense — the claims statement already ends in its
    own total, and summing bags together with rupees means nothing.
    """
    if wb is None:
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name[:31]
    else:
        ws = wb.create_sheet(sheet_name[:31])
    ncols = len(columns)
    last_col = get_column_letter(ncols)

    ws.merge_cells(f"A1:{last_col}1")
    ws["A1"] = f"{FIRM_NAME} — {title}"
    ws["A1"].font = TITLE_FONT

    meta = []
    if scope:
        meta.append(f"Godown: {scope}")
    if period:
        meta.append(f"Period: {period}")
    meta.append(f"Generated: {ist_stamp()}")
    if user:
        meta.append(f"By: {user}")
    ws.merge_cells(f"A2:{last_col}2")
    ws["A2"] = "     ".join(meta)
    ws["A2"].font = META_FONT

    if note:
        ws.merge_cells(f"A3:{last_col}3")
        ws["A3"] = note
        ws["A3"].font = META_FONT
        header_row = 5
    else:
        header_row = 4

    for idx, (label, width) in enumerate(columns, start=1):
        cell = ws.cell(row=header_row, column=idx, value=label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(idx)].width = width

    totals = {c: 0.0 for c in total_columns}
    n = 0
    for row in rows:
        n += 1
        ws.append(list(row))
        for c in total_columns:
            value = row[c - 1] if c - 1 < len(row) else None
            if isinstance(value, (int, float)):
                totals[c] += value

    first_data = header_row + 1
    last_data = header_row + n

    number_formats = number_formats or {}
    for col, fmt in number_formats.items():
        for r in range(first_data, last_data + 1):
            ws.cell(row=r, column=col).number_format = fmt

    if n and show_total_row:
        tr = last_data + 1
        ws.cell(row=tr, column=1, value=f"TOTAL — {n} record{'' if n == 1 else 's'}")
        for col in range(1, ncols + 1):
            cell = ws.cell(row=tr, column=col)
            cell.fill = TOTAL_FILL
            cell.font = Font(bold=True)
            cell.border = THIN_TOP
        for col, value in totals.items():
            cell = ws.cell(row=tr, column=col, value=round(value, 3))
            cell.number_format = number_formats.get(col, "#,##0")
        if autofilter:
            ws.auto_filter.ref = f"A{header_row}:{last_col}{last_data}"
    elif n:
        if autofilter:
            ws.auto_filter.ref = f"A{header_row}:{last_col}{last_data}"
    else:
        ws.cell(row=first_data, column=1, value="No records for these filters.")

    ws.freeze_panes = f"A{first_data}"
    return wb


def xlsx_response(wb: Workbook, filename: str) -> StreamingResponse:
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

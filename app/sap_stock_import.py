"""Import the SAP actual-stock report (ALV "Report output" HTML export).

SAP's list viewer exports as HTML, not xlsx, so this importer parses that
rather than a workbook. The shape of the file, from a real Daultabad export:

    Material | Material Description | From Date | To Date | Opening Stock |
    Total Receipt Qties | Total Issue Quantiti | Closing Stock | BUn | S

What matters, and what is easy to get wrong:

* **Closing Stock is the SAP stock figure.** Opening/Receipt/Issue are SAP's
  own movement columns and are deliberately ignored - our GRN and Dispatch
  registers already hold that, and mixing the two sources would double-count.
* **Quantities are in MT**, per the BUn column, so they are converted to bags
  with each product's own bag weight. Microfine's 20 kg bag matters here: the
  same 27.68 MT is 1,384 bags of Microfine but only 554 bags of 50 kg cement.
* **SAP writes negatives with a TRAILING minus** ("245.320-"), and thousands
  with commas ("1,325.000"). Parsed naively, "1,325.000-" is not a number.
* **The From/To Date columns are 01.01.0000 / 31.12.9999** - placeholders for
  "all time", not a statement date. The file therefore carries no usable date
  and the uploader must supply the as-of date.
* **Columns are located by header text, not by position**, so an extra column
  in a future export doesn't silently shift the figures.
* **The last row is a yellow totals row** with "*" in the Material cell. It is
  skipped; importing it would add a phantom product-less row. Its figure is
  kept as a cross-check and reported in the import log.
"""
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import List, Optional

from sqlalchemy.orm import Session

from app import crud
from app.sap_import import ImportResult, resolve_product


class _AlvTableParser(HTMLParser):
    """Collect the ALV table as a list of rows of cell strings.

    SAP nests the real text inside <font><nobr> and truncates long
    descriptions, putting the full text in a `title` attribute on a trailing
    SAPDings element. Those titles are captured too, so the full description
    is available for the unmapped-code list.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: List[List[str]] = []
        self.titles: List[List[str]] = []
        self._row = None
        self._row_titles = None
        self._cell = None
        self._cell_titles = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self._row, self._row_titles = [], []
        elif tag in ("td", "th") and self._row is not None:
            self._cell, self._cell_titles = [], []
        if self._cell is not None and a.get("title"):
            self._cell_titles.append(a["title"])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append("".join(self._cell))
            self._row_titles.append(" ".join(self._cell_titles))
            self._cell = self._cell_titles = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
                self.titles.append(self._row_titles)
            self._row = self._row_titles = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _clean(text: str) -> str:
    """Collapse SAP's non-breaking-space padding into ordinary spacing."""
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_sap_number(text: str) -> Optional[float]:
    """Parse a SAP ALV numeric cell.

    Handles comma thousands separators and SAP's trailing-minus convention for
    negatives ("2,604.970-" is -2604.97). Returns None for a cell holding no
    number at all, which is how a blank or a label cell is recognised.
    """
    s = _clean(text).replace(",", "").replace(" ", "")
    if not s:
        return None
    negative = s.endswith("-")
    if negative:
        s = s[:-1]
    elif s.startswith("-"):
        negative, s = True, s[1:]
    if not re.fullmatch(r"\d*\.?\d*", s) or s in ("", "."):
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


# Header text -> internal key. Matched on a normalised lowercase prefix,
# because SAP truncates headers to the column width ("Total Issue Quantiti").
_HEADER_KEYS = {
    "material description": "description",
    "material": "sap_code",
    "closing stock": "closing",
    "opening stock": "opening",
    "total receipt": "receipt",
    "total issue": "issue",
    "bun": "unit",
}


def _header_map(cells: List[str]) -> Optional[dict]:
    """Map column index -> key, if this row looks like the header row."""
    found = {}
    for i, cell in enumerate(cells):
        label = _clean(cell).lower()
        if not label:
            continue
        for prefix, key in _HEADER_KEYS.items():
            if label.startswith(prefix) and key not in found.values():
                found[i] = key
                break
    if "sap_code" in found.values() and "closing" in found.values():
        return found
    return None


@dataclass
class SapStockRow:
    sap_code: str
    description: str
    closing_qty: float
    unit: str


@dataclass
class ParsedSapStock:
    rows: List[SapStockRow] = field(default_factory=list)
    total_qty: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


def parse_sap_stock_html(content) -> ParsedSapStock:
    """Pull the per-material closing stock out of a SAP ALV HTML export."""
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content

    parser = _AlvTableParser()
    parser.feed(text)

    out = ParsedSapStock()
    headers = None
    for cells, titles in zip(parser.rows, parser.titles):
        if headers is None:
            headers = _header_map(cells)
            continue

        by_key = {key: cells[i] for i, key in headers.items() if i < len(cells)}
        title_by_key = {key: titles[i] for i, key in headers.items() if i < len(titles)}

        sap_code = _clean(by_key.get("sap_code", ""))
        closing = parse_sap_number(by_key.get("closing", ""))

        # The yellow totals row carries "*" where the material code goes.
        # Keep its figure as a cross-check, never as a product row.
        if sap_code in ("", "*") or sap_code.startswith("*"):
            if closing is not None:
                out.total_qty = closing
            continue
        if closing is None:
            out.warnings.append(f"{sap_code}: closing stock cell was not a number, row skipped")
            continue

        out.rows.append(SapStockRow(
            sap_code=sap_code,
            description=_clean(title_by_key.get("description") or by_key.get("description", "")),
            closing_qty=closing,
            unit=(_clean(by_key.get("unit", "")) or "MT").upper(),
        ))

    if headers is None:
        out.warnings.append(
            "No SAP stock table found in this file. Export the stock report from SAP as "
            "'Report output' (HTML) and upload that file unchanged."
        )
    return out


# Units SAP might report a bagged product in. Anything already counted in
# bags needs no conversion; MT does.
_BAG_UNITS = {"BAG", "BAGS", "EA", "PC", "PCS", "ST"}


def import_sap_stock_file(db: Session, content, godown_id: int, as_of_date,
                          user_id: Optional[int] = None, filename: str = None) -> ImportResult:
    """Parse a SAP stock export and record one snapshot per mapped product.

    Quantities are TOTALLED PER PRODUCT before being written, because several
    SAP materials legitimately map to one of our products — Concreel HD ACE
    and Concreel HD Raksha are both "JSW ACE" here. Writing each row
    separately would make the last code processed win and silently discard the
    others: a real export had ACE at 66.800 MT followed by Raksha at 0.000,
    which wrote the stock down to zero.
    """
    result = ImportResult()
    parsed = parse_sap_stock_html(content)
    result.messages.extend(parsed.warnings)
    result.rows_total = len(parsed.rows)

    totals = {}          # product_id -> bags
    products = {}        # product_id -> Product
    contributing = {}    # product_id -> [sap codes]

    for row in parsed.rows:
        product = resolve_product(db, row.sap_code, row.description)
        if product is None:
            # Every SAP export lists the whole material master, most of it
            # irrelevant to this godown and sitting at zero. Only a code with
            # actual stock is worth putting in front of an admin.
            if abs(row.closing_qty) < 1e-9:
                result.rows_skipped_other += 1
            else:
                result.rows_skipped_unmapped += 1
                result.unmapped_codes.add(row.sap_code)
            continue

        if row.unit in _BAG_UNITS:
            bags = row.closing_qty
        elif row.unit == "MT":
            bags = row.closing_qty / crud.product_bag_weight_mt(db, product)
        else:
            result.rows_skipped_other += 1
            result.messages.append(
                f"{row.sap_code}: unit '{row.unit}' not recognised (expected MT or BAG), row skipped"
            )
            continue

        totals[product.id] = totals.get(product.id, 0.0) + bags
        products[product.id] = product
        contributing.setdefault(product.id, []).append(row.sap_code)
        result.rows_imported += 1

    for product_id, bags in totals.items():
        crud.upsert_sap_stock(db, godown_id, product_id, as_of_date, bags,
                              source="html", filename=filename, user_id=user_id)
        codes = contributing[product_id]
        if len(codes) > 1:
            result.messages.append(
                f"{products[product_id].name}: {len(codes)} SAP codes totalled "
                f"({', '.join(sorted(codes))})"
            )

    if parsed.total_qty is not None:
        result.messages.append(
            f"SAP report total: {parsed.total_qty:,.3f} across all materials, as printed on the report"
        )
    return result

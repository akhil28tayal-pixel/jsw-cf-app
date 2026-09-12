"""
HTML <select>/<input> elements submit an empty string when left blank
(e.g. an "All Products" option with value=""), not an absent parameter.
FastAPI's typed Query(date) / Query(int) params reject an empty string
outright with a 422, since "provided but blank" isn't the same as "omitted"
to pydantic. Every filter route accepts these as plain strings and converts
them with the helpers below, treating "" the same as "not provided".
"""
import datetime as dt
from typing import Optional


def parse_date(value: Optional[str]) -> Optional[dt.date]:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def parse_float(value: Optional[str]) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def parse_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None

import os

from starlette.templating import Jinja2Templates
from starlette.requests import Request

from app.database import SessionLocal
from app.godown_context import get_active_godown
from app import crud


def asset_version() -> str:
    """Cache-buster for /static. Browsers hold on to style.css hard — same URL,
    same file as far as they're concerned — so a restyled app can keep serving
    the old look after an update. Stamping the CSS/JS links with the stylesheet's
    modification time means a changed file is always a new URL, and an unchanged
    one still comes from cache."""
    try:
        return str(int(os.path.getmtime("app/static/style.css")))
    except OSError:
        return "0"


def _asset_context_processor(request: Request) -> dict:
    return {"asset_v": asset_version()}


def _godown_context_processor(request: Request) -> dict:
    """Auto-injects active_godown/all_godowns into every template's context,
    so individual routes don't each need to remember to pass them for the
    navbar switcher to render. Only does anything once someone's logged in
    (a fresh session with no user yet has nothing to scope)."""
    if not request.session.get("user_id"):
        return {}
    db = SessionLocal()
    try:
        return {
            "active_godown": get_active_godown(request, db),
            "all_godowns": crud.list_godowns(db),
        }
    finally:
        db.close()


def format_inr(value, decimals: int = 0) -> str:
    """Money the way it's read here: Indian digit grouping (4,21,540 — not
    421540 or 421,540). Bag counts stay ungrouped; only rupee amounts and
    rates use this."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return value
    negative = n < 0
    text = "%.*f" % (decimals, abs(n))
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if negative else "") + whole + (("." + frac) if frac else "")


templates = Jinja2Templates(directory="app/templates", context_processors=[_godown_context_processor, _asset_context_processor])
templates.env.filters["inr"] = format_inr

from fastapi import Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models, crud


def get_active_godown(request: Request, db: Session = Depends(get_db)) -> models.Godown:
    """Every page that deals with stock/GRN/dispatch/billing/freight needs a
    godown to scope its data to. The active one lives in the session (set by
    the navbar switcher) and defaults to the oldest active godown by id —
    for anyone migrating from the old single-godown schema, that's always
    Manesar, since it's created first by the migration. (Deliberately NOT
    the first entry of crud.list_godowns(), which sorts alphabetically for
    the dropdown — "Daultabad" would incorrectly win that sort.)"""
    godowns = crud.list_godowns(db)
    if not godowns:
        # Should never happen post-migration, but fail safely rather than crash.
        return None
    godown_id = request.session.get("active_godown_id")
    if godown_id:
        for g in godowns:
            if g.id == godown_id:
                return g
    # Not set, or the stored id is no longer valid — default to the oldest one.
    return min(godowns, key=lambda g: g.id)


def set_active_godown(request: Request, godown_id: int) -> None:
    request.session["active_godown_id"] = godown_id


def godown_nav_context(request: Request, db: Session) -> dict:
    """Common context every page needs to render the godown switcher in the
    navbar. Merge this into each route's template context, e.g.:
        return templates.TemplateResponse(request, "x.html", {
            ..., **godown_nav_context(request, db),
        })
    """
    return {
        "active_godown": get_active_godown(request, db),
        "all_godowns": crud.list_godowns(db),
    }

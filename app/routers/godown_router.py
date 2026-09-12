from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import require_login
from app.godown_context import set_active_godown
from app import models

router = APIRouter()


@router.post("/set-godown")
def set_godown(request: Request, godown_id: int = Form(...), redirect_to: str = Form("/"),
               db: Session = Depends(get_db), user=Depends(require_login)):
    godown = db.get(models.Godown, godown_id)
    if godown and godown.active:
        set_active_godown(request, godown_id)
    # Never redirect off-site — only ever back to a path within this app.
    if not redirect_to or not redirect_to.startswith("/"):
        redirect_to = "/"
    return RedirectResponse(redirect_to, status_code=303)

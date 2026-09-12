from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app import models
from app.auth import verify_password, login_user, logout_user, get_current_user
from app.flash import flash, get_flashed_messages

from app.templating import templates

router = APIRouter()


@router.get("/login")
def login_form(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {
        "user": None, "flashes": get_flashed_messages(request)
    })


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...),
                  db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == username, models.User.is_active == True).first()  # noqa: E712
    if not user or not verify_password(password, user.password_hash):
        flash(request, "Invalid username or password.", "error")
        return RedirectResponse("/login", status_code=303)
    login_user(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request):
    logout_user(request)
    return RedirectResponse("/login", status_code=303)

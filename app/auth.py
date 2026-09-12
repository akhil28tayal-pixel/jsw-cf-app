import bcrypt
from fastapi import Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app import models


class NotAuthenticated(Exception):
    """Raised when a protected page is hit with no valid session.
    Caught by a handler in main.py that redirects to /login."""


class Forbidden(Exception):
    """Raised when a logged-in user lacks the role required for a page."""


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def login_user(request: Request, user: models.User) -> None:
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["role"] = user.role


def logout_user(request: Request) -> None:
    request.session.clear()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """Returns the logged-in User, or None. Does NOT redirect — use
    `require_login` in routes that must be protected."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(models.User).filter(models.User.id == user_id, models.User.is_active == True).first()  # noqa: E712


def require_login(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        raise NotAuthenticated()
    return user


def require_admin(request: Request, db: Session = Depends(get_db)):
    user = require_login(request, db)
    if user.role != "admin":
        raise Forbidden()
    return user

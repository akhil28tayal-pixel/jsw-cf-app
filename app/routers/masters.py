from fastapi import APIRouter, Request, Depends, Form, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.auth import require_admin, hash_password
from app.flash import flash, get_flashed_messages
from app.godown_context import get_active_godown
from app.query_utils import parse_int, parse_float, parse_date
from app import crud, models

from app.templating import templates

router = APIRouter()


def _rate_card_redirect(rate_godown_id) -> RedirectResponse:
    """Send the admin back to the rate card they were looking at, so editing
    Daultabad's rates doesn't bounce them to Manesar's list."""
    suffix = f"?rate_godown_id={rate_godown_id}" if rate_godown_id else ""
    return RedirectResponse(f"/products{suffix}", status_code=303)


@router.get("/products")
def masters_page(request: Request, rate_godown_id: str = Query(None),
                 db: Session = Depends(get_db), user=Depends(require_admin)):
    active_godown = get_active_godown(request, db)
    godowns = db.query(models.Godown).order_by(models.Godown.name).all()

    # Which godown's freight rates to show. Defaults to the active godown;
    # "all" shows every godown's rates side by side, which is the quickest way
    # to sanity-check that the same district really is priced differently from
    # each location.
    show_all_rates = (rate_godown_id == "all")
    rate_godown = None
    if not show_all_rates:
        wanted_id = parse_int(rate_godown_id) or (active_godown.id if active_godown else None)
        rate_godown = db.get(models.Godown, wanted_id) if wanted_id else None

    rq = db.query(models.FreightRateCard).join(
        models.Godown, models.FreightRateCard.godown_id == models.Godown.id
    )
    if rate_godown:
        rq = rq.filter(models.FreightRateCard.godown_id == rate_godown.id)
    rate_cards = rq.order_by(models.Godown.name, models.FreightRateCard.district,
                             models.FreightRateCard.pincode).all()

    return templates.TemplateResponse(request, "masters.html", {
        "sap_adjustments": crud.list_sap_adjustments(db),
        "user": user, "flashes": get_flashed_messages(request),
        "godowns": godowns,
        "active_godown": active_godown,
        "products": db.query(models.Product).order_by(models.Product.name).all(),
        "dealers": db.query(models.Dealer).order_by(models.Dealer.name).all(),
        "transporters": db.query(models.Transporter).order_by(models.Transporter.name).all(),
        "users": db.query(models.User).order_by(models.User.username).all(),
        "rate_cards": rate_cards,
        "rate_godown": rate_godown,
        "show_all_rates": show_all_rates,
    })


@router.post("/godowns/add")
def add_godown(request: Request, name: str = Form(...), db: Session = Depends(get_db), user=Depends(require_admin)):
    if name.strip():
        crud.add_godown(db, name)
        flash(request, f'Godown "{name}" added. Set up its own freight rate card below — rates are per godown.')
    return RedirectResponse("/products", status_code=303)


@router.post("/godowns/{godown_id}/toggle")
def toggle_godown(godown_id: int, request: Request, db: Session = Depends(get_db), user=Depends(require_admin)):
    g = db.get(models.Godown, godown_id)
    active_count = db.query(models.Godown).filter(models.Godown.active == True).count()  # noqa: E712
    if g:
        if g.active and active_count <= 1:
            flash(request, "Can't deactivate the last remaining active godown.", "error")
        else:
            g.active = not g.active
            db.commit()
    return RedirectResponse("/products", status_code=303)


@router.post("/products/add")
def add_product(request: Request, name: str = Form(...), bag_weight_kg: str = Form(""),
                db: Session = Depends(get_db), user=Depends(require_admin)):
    if name.strip():
        kg = parse_float(bag_weight_kg)
        crud.add_product(db, name, bag_weight_kg=kg)
        note = f" at a {kg:.0f} kg bag" if kg else ""
        flash(request, f'Product "{name}" added{note}.')
    return RedirectResponse("/products", status_code=303)


@router.post("/products/{product_id}/bag-weight")
def set_product_bag_weight(product_id: int, request: Request, bag_weight_kg: str = Form(""),
                            db: Session = Depends(get_db), user=Depends(require_admin)):
    """Bag size per product — 50 kg for the cements, 20 kg for Microfine.
    It drives stock in MT, the MT→bags conversion on SAP import, and the
    weight a truck is rated on for freight, so it's worth getting right."""
    product = db.get(models.Product, product_id)
    if not product:
        flash(request, "That product no longer exists.", "error")
        return RedirectResponse("/products", status_code=303)
    kg = parse_float(bag_weight_kg)
    if kg is not None and kg <= 0:
        flash(request, "Bag size has to be more than 0 kg.", "error")
        return RedirectResponse("/products", status_code=303)
    crud.set_product_bag_weight_kg(db, product, kg)
    if kg:
        flash(request, f'{product.name}: 1 bag = {kg:.0f} kg, so 1 MT = {1000 / kg:.0f} bags.')
    else:
        flash(request, f"{product.name}: bag size cleared — back to the default 50 kg bag.")
    return RedirectResponse("/products", status_code=303)


@router.post("/products/{product_id}/toggle")
def toggle_product(product_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    p = db.get(models.Product, product_id)
    if p:
        p.active = not p.active
        db.commit()
    return RedirectResponse("/products", status_code=303)


@router.post("/dealers/add")
def add_dealer(request: Request, name: str = Form(...), db: Session = Depends(get_db), user=Depends(require_admin)):
    if name.strip():
        crud.add_dealer(db, name)
        flash(request, f'Dealer "{name}" added.')
    return RedirectResponse("/products", status_code=303)


@router.post("/transporters/add")
def add_transporter(request: Request, name: str = Form(...), contact: str = Form(""),
                     db: Session = Depends(get_db), user=Depends(require_admin)):
    if name.strip():
        crud.add_transporter(db, name, contact)
        flash(request, f'Transporter "{name}" added.')
    return RedirectResponse("/products", status_code=303)


# ---------------------------------------------------------------------------
# Freight rate card — one independent rate card per godown.
#
# The same district is a different distance from each godown, so it gets its
# own pair of rates per godown. The godown is chosen explicitly on the form
# (defaulting to whichever rate card is on screen) rather than being taken
# from the navbar switcher, so rates can't land on the wrong location just
# because the switcher was left on the other one.
# ---------------------------------------------------------------------------
@router.post("/rate-card/add")
def add_rate_card(request: Request, district: str = Form(...), pincode: str = Form(""),
                   transporter_rate_per_mt: float = Form(...), company_claim_rate_per_mt: float = Form(...),
                   godown_id: str = Form(""), db: Session = Depends(get_db), user=Depends(require_admin)):
    target_id = parse_int(godown_id)
    godown = db.get(models.Godown, target_id) if target_id else get_active_godown(request, db)
    if godown is None:
        flash(request, "Pick a godown for this rate.", "error")
        return _rate_card_redirect(None)

    row = models.FreightRateCard(
        godown_id=godown.id,
        district=district.strip(), pincode=pincode.strip() or None,
        transporter_rate_per_mt=transporter_rate_per_mt,
        company_claim_rate_per_mt=company_claim_rate_per_mt,
    )
    db.add(row)
    try:
        db.commit()
        flash(request, f"Rate card added for {district} at {godown.name}.")
    except IntegrityError:
        db.rollback()
        flash(request, f"A rate for that district/pincode already exists at {godown.name} — "
                       f"edit that row instead of adding a second one.", "error")
    return _rate_card_redirect(godown.id)


@router.post("/rate-card/{rate_id}/update")
def update_rate_card(rate_id: int, request: Request,
                      transporter_rate_per_mt: float = Form(...), company_claim_rate_per_mt: float = Form(...),
                      db: Session = Depends(get_db), user=Depends(require_admin)):
    row = db.get(models.FreightRateCard, rate_id)
    if not row:
        flash(request, "That rate card row no longer exists.", "error")
        return _rate_card_redirect(None)
    row.transporter_rate_per_mt = transporter_rate_per_mt
    row.company_claim_rate_per_mt = company_claim_rate_per_mt
    db.commit()
    flash(request, f"Updated {row.district} rate at {row.godown.name}.")
    return _rate_card_redirect(row.godown_id)


@router.post("/rate-card/{rate_id}/delete")
def delete_rate_card(rate_id: int, request: Request,
                      db: Session = Depends(get_db), user=Depends(require_admin)):
    row = db.get(models.FreightRateCard, rate_id)
    if not row:
        flash(request, "That rate card row no longer exists.", "error")
        return _rate_card_redirect(None)
    godown_id, district, godown_name = row.godown_id, row.district, row.godown.name
    db.delete(row)
    db.commit()
    flash(request, f"Deleted the {district} rate at {godown_name}. Dispatches to that district "
                   f"will now show as missing a rate until you add one.")
    return _rate_card_redirect(godown_id)


@router.post("/users/add")
def add_user(request: Request, username: str = Form(...), password: str = Form(...),
             role: str = Form("staff"), db: Session = Depends(get_db), user=Depends(require_admin)):
    if db.query(models.User).filter(models.User.username == username).first():
        flash(request, "That username already exists.", "error")
        return RedirectResponse("/products", status_code=303)
    new_user = models.User(username=username, password_hash=hash_password(password), role=role)
    db.add(new_user)
    db.commit()
    flash(request, f'User "{username}" ({role}) created.')
    return RedirectResponse("/products", status_code=303)


@router.post("/users/{user_id}/toggle")
def toggle_user(user_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    u = db.get(models.User, user_id)
    if u and u.id != user.id:  # can't deactivate yourself
        u.is_active = not u.is_active
        db.commit()
    return RedirectResponse("/products", status_code=303)


@router.post("/sap-adjustment/add")
def add_sap_adjustment(request: Request, godown_id: int = Form(...), product_id: int = Form(...),
                        bags: float = Form(...), as_of_date: str = Form(None),
                        reason: str = Form(None), db: Session = Depends(get_db),
                        user=Depends(require_admin)):
    """Record a permanent SAP-vs-physical shortage, e.g. material short at a
    godown handover. Positive bags = SAP over-states and the figure is
    subtracted from the SAP side on every reconciliation."""
    crud.add_sap_adjustment(db, godown_id, product_id, bags,
                            as_of_date=parse_date(as_of_date), reason=(reason or None),
                            user_id=user.id)
    flash(request, "SAP stock adjustment saved.")
    return RedirectResponse("/products", status_code=303)


@router.post("/sap-adjustment/{adjustment_id}/delete")
def delete_sap_adjustment(adjustment_id: int, request: Request, db: Session = Depends(get_db),
                           user=Depends(require_admin)):
    if crud.delete_sap_adjustment(db, adjustment_id):
        flash(request, "SAP stock adjustment deleted.")
    else:
        flash(request, "That adjustment no longer exists.", "warning")
    return RedirectResponse("/products", status_code=303)

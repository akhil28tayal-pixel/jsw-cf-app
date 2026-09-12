import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.seed import init_db_and_seed
from app.auth import NotAuthenticated, Forbidden
from app.routers import auth_router, dashboard, masters, stock, grn, dispatch, billing, dealer_report, freight, claims, sap_import_router, backup, godown_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("jsw_cf_app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db_and_seed()
    if settings.SECRET_KEY.startswith("insecure-dev-key"):
        logger.warning(
            "SECRET_KEY is still the default placeholder. Set a real random "
            "SECRET_KEY in your .env before exposing this app beyond localhost."
        )
    yield


app = FastAPI(title="JSW C&F Operations", lifespan=lifespan)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
    https_only=(settings.ENV == "production"),
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/robots.txt", include_in_schema=False)
def robots_txt():
    # This is an internal operations app on a public URL. Nothing in it
    # should ever turn up in a search result.
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.exception_handler(NotAuthenticated)
def handle_not_authenticated(request: Request, exc: NotAuthenticated):
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(Forbidden)
def handle_forbidden(request: Request, exc: Forbidden):
    return PlainTextResponse("403 Forbidden — admin access required.", status_code=403)


app.include_router(auth_router.router)
app.include_router(dashboard.router)
app.include_router(masters.router)
app.include_router(stock.router)
app.include_router(grn.router)
app.include_router(dispatch.router)
app.include_router(billing.router)
app.include_router(dealer_report.router)
app.include_router(freight.router)
app.include_router(claims.router)
app.include_router(sap_import_router.router)
app.include_router(backup.router)
app.include_router(godown_router.router)

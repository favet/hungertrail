from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from .config import settings
from .daily import daily_for, today

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(settings.db_path)
    yield


app = FastAPI(title="Hunger Trail", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    daily = daily_for(settings, today(settings))
    return templates.TemplateResponse(request, "index.html", {"daily": daily})


@app.get("/api/daily")
def api_daily():
    daily = daily_for(settings, today(settings))
    # The seed stays server-side; the client only learns which day it is.
    return {"date": daily.date.isoformat(), "number": daily.number}


@app.get("/healthz")
def healthz():
    return {"ok": True}

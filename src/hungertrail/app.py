from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import runs
from .config import settings
from .daily import daily_for, today
from .game import lore
from .leaderboard import BoardError, clean_nickname, make_board

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
STATIC_VERSION = str(int(max(p.stat().st_mtime for p in (HERE / "static").iterdir())))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.board = make_board(settings, settings.leaderboard_url)
    yield


app = FastAPI(title="Hunger Trail", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def _daily_info() -> dict:
    now = datetime.now(settings.timezone)
    d = daily_for(settings, today(settings, now))
    tomorrow = datetime.combine(d.date + timedelta(days=1), datetime.min.time(), settings.timezone)
    return {"date": d.date.isoformat(), "number": d.number, "next_in": int((tomorrow - now).total_seconds())}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    info = _daily_info()
    config = {
        "daily": info,
        "districts": [
            {"id": k, "name": v, "mult": lore.multiplier(k), "career": k in lore.CAREERS}
            for k, v in lore.DISTRICTS.items()
        ],
        "traits": [{"id": k, "name": n, "desc": d} for k, (n, d) in lore.TRAITS.items()],
        "names": lore.DEFAULT_NAMES,
    }
    return templates.TemplateResponse(
        request, "index.html", {"config": config, "daily": info, "v": STATIC_VERSION}
    )


class StartBody(BaseModel):
    mode: str
    district: int
    trait: str
    partner: str = Field("", max_length=40)
    allies: list[str] = Field(default_factory=list, max_length=2)


class TokenBody(BaseModel):
    token: str = Field(max_length=4000)


class ChooseBody(TokenBody):
    index: int
    param: int | None = None


class SubmitBody(TokenBody):
    nickname: str = Field(max_length=40)


def _fail(e: Exception):
    raise HTTPException(400, str(e))


@app.post("/api/run")
def start_run(body: StartBody):
    try:
        d = today(settings)
        token, game, p = runs.start(settings, d, body.mode, body.district, body.trait, body.partner, body.allies)
    except runs.RunError as e:
        _fail(e)
    return runs.view(settings, token, game, p)


@app.post("/api/run/view")
def view_run(body: TokenBody):
    try:
        game, p = runs.load(settings, body.token)
    except runs.RunError as e:
        _fail(e)
    return runs.view(settings, body.token, game, p)


@app.post("/api/run/choose")
def choose(body: ChooseBody):
    try:
        token, game, p = runs.choose(settings, body.token, body.index, body.param)
    except runs.RunError as e:
        _fail(e)
    return runs.view(settings, token, game, p)


@app.get("/api/daily")
def api_daily():
    return _daily_info()


@app.get("/api/leaderboard")
def leaderboard(request: Request, day: str | None = None):
    day = day or today(settings).isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "bad date")
    return {"date": day, "rows": request.app.state.board.top(day)}


@app.post("/api/leaderboard")
def submit(request: Request, body: SubmitBody):
    try:
        game, p = runs.load(settings, body.token)
        nickname = clean_nickname(body.nickname)
    except (runs.RunError, BoardError) as e:
        _fail(e)
    if p["m"] != "daily":
        raise HTTPException(400, "Practice runs aren't ranked.")
    if not game.over:
        raise HTTPException(400, "Finish the run first.")
    run_day = date.fromisoformat(p["d"])
    if (today(settings) - run_day).days > 1:
        raise HTTPException(400, "That arena has closed.")
    entry = {
        "date": p["d"], "nickname": nickname, "run_id": p["id"], "score": game.score(),
        "placement": game.placement, "kills": game.kills, "day": game.day,
        "district": game.district, "cause": game.cause or "", "emoji": game.emoji_row(),
    }
    try:
        request.app.state.board.submit(entry)
    except BoardError as e:
        raise HTTPException(409, str(e))
    rows = request.app.state.board.top(p["d"], 200)
    rank = next((i + 1 for i, r in enumerate(rows) if r["nickname"] == nickname), None)
    return {"ok": True, "rank": rank}


@app.get("/healthz")
def healthz(request: Request):
    board = request.app.state.board
    return {"ok": True, "leaderboard": "remote" if hasattr(board, "registered") and board.registered else "local" if not hasattr(board, "registered") else "unregistered"}

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import runs
from .config import settings
from .daily import daily_for, today
from .beta import lore as beta_lore
from .beta import runs as beta_runs
from .game import lore
from .leaderboard import BoardError, LocalBoard, clean_nickname, make_board

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")
STATIC_VERSION = str(int(max(p.stat().st_mtime for p in (HERE / "static").rglob("*") if p.is_file())))


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.board = make_board(settings, settings.leaderboard_url)
    # The beta keeps its own throwaway board (Render's disk resets on deploy, which is fine for a beta).
    app.state.beta_board = LocalBoard(replace(settings, db_path=settings.db_path.with_name("beta.sqlite3")))
    yield


app = FastAPI(title="Hunger Trail", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def _daily_info() -> dict:
    now = datetime.now(settings.timezone)
    d = daily_for(settings, today(settings, now))
    tomorrow = datetime.combine(d.date + timedelta(days=1), datetime.min.time(), settings.timezone)
    return {"date": d.date.isoformat(), "number": d.number, "next_in": int((tomorrow - now).total_seconds())}


def _is_beta(request: Request) -> bool:
    # beta.hungertrail.com reaches us through a Cloudflare Worker that sets x-ht-site.
    host = request.headers.get("host", "")
    return host.startswith("beta.") or request.headers.get("x-ht-site") == "beta"


def _beta_page(request: Request):
    info = _daily_info()
    config = {
        "daily": info,
        "districts": [{"id": k, "name": v, "perk": beta_lore.PERKS[k][0], "desc": beta_lore.PERKS[k][1]} for k, v in beta_lore.DISTRICTS.items()],
        "names": beta_lore.DEFAULT_NAMES,
    }
    return templates.TemplateResponse(request, "beta.html", {"config": config, "daily": info, "v": STATIC_VERSION})


@app.get("/beta", response_class=HTMLResponse)
def beta_index(request: Request):
    return _beta_page(request)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if _is_beta(request):
        return _beta_page(request)
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


# ---------------------------------------------------------------- beta API


class BetaStartBody(BaseModel):
    mode: str
    district: int
    partner: str = Field("", max_length=40)
    allies: list[str] = Field(default_factory=list, max_length=2)


def _beta_view(token, game, p):
    v = beta_runs.view(settings, token, game, p)
    if game.card == "draft":
        v["layout"] = [
            {"emoji": beta_lore.item_emoji(i), "ring": ring, "taken": n in game.taken}
            for n, (i, ring) in enumerate(game.arena.layout)
        ]
    return v


@app.post("/api/beta/run")
def beta_start(body: BetaStartBody):
    try:
        token, game, p = beta_runs.start(settings, today(settings), body.mode, body.district, body.partner, body.allies)
    except runs.RunError as e:
        _fail(e)
    return _beta_view(token, game, p)


@app.post("/api/beta/run/view")
def beta_view(body: TokenBody):
    try:
        game, p = beta_runs.load(settings, body.token)
    except runs.RunError as e:
        _fail(e)
    return _beta_view(body.token, game, p)


class BetaChooseBody(TokenBody):
    index: int
    param: int | Annotated[list[int], Field(max_length=400)] | None = None


@app.post("/api/beta/run/choose")
def beta_choose(body: BetaChooseBody):
    try:
        token, game, p = beta_runs.choose(settings, body.token, body.index, body.param)
    except runs.RunError as e:
        _fail(e)
    return _beta_view(token, game, p)


@app.get("/api/beta/leaderboard")
def beta_leaderboard(request: Request, day: str | None = None):
    day = day or today(settings).isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, "bad date")
    return {"date": day, "rows": request.app.state.beta_board.top(day)}


@app.post("/api/beta/leaderboard")
def beta_submit(request: Request, body: SubmitBody):
    try:
        game, p = beta_runs.load(settings, body.token)
        nickname = clean_nickname(body.nickname)
    except (runs.RunError, BoardError) as e:
        _fail(e)
    if p["m"] != "daily":
        raise HTTPException(400, "Practice runs aren't ranked.")
    if not game.over:
        raise HTTPException(400, "Finish the run first.")
    if (today(settings) - date.fromisoformat(p["d"])).days > 1:
        raise HTTPException(400, "That arena has closed.")
    entry = {
        "date": p["d"], "nickname": nickname, "run_id": p["id"], "score": game.score(),
        "placement": game.placement, "kills": game.kills, "day": game.day,
        "district": game.district, "cause": game.cause or "", "emoji": game.emoji_row(),
    }
    try:
        request.app.state.beta_board.submit(entry)
    except BoardError as e:
        raise HTTPException(409, str(e))
    rows = request.app.state.beta_board.top(p["d"], 200)
    rank = next((i + 1 for i, r in enumerate(rows) if r["nickname"] == nickname), None)
    return {"ok": True, "rank": rank}


@app.get("/healthz")
def healthz(request: Request):
    board = request.app.state.board
    return {"ok": True, "leaderboard": "remote" if hasattr(board, "registered") and board.registered else "local" if not hasattr(board, "registered") else "unregistered"}

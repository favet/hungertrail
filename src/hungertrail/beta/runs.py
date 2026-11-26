"""Beta run tokens: same scheme as the live game (signed setup + choices, replayed on the server),
with a different signing label and version so live and beta tokens can never be swapped."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
from datetime import date

from ..config import Settings
from ..daily import daily_for, seed_for
from ..runs import RunError, _clean_name, ordinal
from . import lore
from .engine import Game, new_setup, replay

VERSION = "b1"


def _mac(settings: Settings, body: bytes) -> str:
    return hmac.new(settings.seed_secret.encode(), b"beta-run|" + body, hashlib.sha256).hexdigest()[:32]


def _derive(settings: Settings, label: str, run_id: str) -> int:
    d = hmac.new(settings.seed_secret.encode(), f"beta-{label}|{run_id}".encode(), hashlib.sha256).digest()
    return int.from_bytes(d[:8], "big")


def encode(settings: Settings, payload: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    return body.decode() + "." + _mac(settings, body)


def decode(settings: Settings, token: str) -> dict:
    try:
        body, mac = token.rsplit(".", 1)
    except (ValueError, AttributeError):
        raise RunError("bad token")
    if not hmac.compare_digest(mac, _mac(settings, body.encode())):
        raise RunError("bad token")
    p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if p.get("v") != VERSION:
        raise RunError("This run is from an older beta. Start a new one.")
    return p


_latest: dict[str, int] = {}
_lock = threading.Lock()


def start(settings: Settings, today: date, mode: str, district: int, partner: str, allies: list[str]) -> tuple[str, Game, dict]:
    if mode not in ("daily", "practice"):
        raise RunError("bad mode")
    run_id = secrets.token_urlsafe(9)
    partner = _clean_name(partner) or lore.DEFAULT_NAMES[secrets.randbelow(len(lore.DEFAULT_NAMES))]
    allies = [a for a in (_clean_name(x) for x in allies[:2]) if a and a != partner]
    try:
        setup = new_setup(int(district), partner, list(dict.fromkeys(allies)))
    except (TypeError, ValueError) as e:
        raise RunError(str(e))
    payload = {"v": VERSION, "id": run_id, "m": mode, "d": today.isoformat(), "s": setup, "c": []}
    return encode(settings, payload), _build(settings, payload), payload


def _build(settings: Settings, p: dict) -> Game:
    if p["m"] == "daily":
        seed = seed_for(settings, date.fromisoformat(p["d"]))
    else:
        seed = _derive(settings, "practice", p["id"])
    try:
        return replay(seed, _derive(settings, "salt", p["id"]), p["s"], p["c"])
    except (ValueError, KeyError, IndexError) as e:
        raise RunError(f"This run can't be replayed ({e}). Start a new one.")


def load(settings: Settings, token: str) -> tuple[Game, dict]:
    p = decode(settings, token)
    return _build(settings, p), p


def choose(settings: Settings, token: str, index: int, param: int | None) -> tuple[str, Game, dict]:
    p = decode(settings, token)
    with _lock:
        if len(p["c"]) < _latest.get(p["id"], 0):
            raise RunError("This run has already moved on. Reload to continue it.")
    game = _build(settings, p)
    param = None if param is None else max(0, min(3, int(param)))
    try:
        game.choose(int(index), param)
    except ValueError as e:
        raise RunError(str(e))
    p["c"].append([int(index), param])
    with _lock:
        _latest[p["id"]] = len(p["c"])
        if len(_latest) > 50_000:
            _latest.clear()
    return encode(settings, p), game, p


def result(settings: Settings, game: Game, p: dict) -> dict | None:
    if not game.over:
        return None
    number = daily_for(settings, date.fromisoformat(p["d"])).number
    title = f"HUNGER TRAIL β #{number}" if p["m"] == "daily" else "HUNGER TRAIL β (practice)"
    kills = f"{game.kills} kill{'s' if game.kills != 1 else ''}"
    if game.won:
        line, end = f"👑 VICTOR · {kills} · Day {game.day}", "Won the Hunger Games."
    else:
        line, end = f"Placed {ordinal(game.placement)} of 24 · {kills} · Day {game.day}", f"Killed by {game.cause}"
    share = "\n".join([f"{title} · District {game.district}", line, game.emoji_row(), end, "beta.hungertrail.com"])
    return {
        "won": game.won,
        "placement": game.placement,
        "placement_text": "Victor" if game.won else f"{ordinal(game.placement)} of 24",
        "kills": game.kills,
        "day": game.day,
        "cause": game.cause,
        "score": game.score(),
        "emoji": game.emoji_row(),
        "district": game.district,
        "share": share,
    }


def view(settings: Settings, token: str, game: Game, p: dict) -> dict:
    d = date.fromisoformat(p["d"])
    return {
        "token": token,
        "mode": p["m"],
        "date": p["d"],
        "number": daily_for(settings, d).number,
        "district": game.district,
        "district_name": lore.DISTRICTS[game.district],
        "perk": lore.PERKS[game.district][0],
        "status": game.status(),
        "log": game.log,
        "card": game.card,
        "options": game.options(),
        "over": game.over,
        "result": result(settings, game, p),
    }

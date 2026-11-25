"""Runs live in signed tokens held by the browser; the server rebuilds them by replay.

The token carries only the setup and the choices made. Seeds and dice salt are derived from the
server secret, so the browser never learns what's coming and can't forge an outcome.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
from datetime import date

from .config import Settings
from .daily import daily_for, seed_for
from .game import lore
from .game.engine import Game, new_setup, replay

ORDINAL = {1: "st", 2: "nd", 3: "rd"}


class RunError(ValueError):
    pass


def _mac(settings: Settings, body: bytes) -> str:
    return hmac.new(settings.seed_secret.encode(), b"run|" + body, hashlib.sha256).hexdigest()[:32]


def _derive(settings: Settings, label: str, run_id: str) -> int:
    d = hmac.new(settings.seed_secret.encode(), f"{label}|{run_id}".encode(), hashlib.sha256).digest()
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
    pad = "=" * (-len(body) % 4)
    return json.loads(base64.urlsafe_b64decode(body + pad))


# Newest step seen per run, so an old token can't be used to retry a bad roll.
_latest: dict[str, int] = {}
_lock = threading.Lock()


def _clean_name(s: str | None) -> str:
    s = re.sub(r"[^\w .'-]", "", (s or ""), flags=re.UNICODE).strip()
    return s[:14]


def start(settings: Settings, today: date, mode: str, district: int, trait: str, partner: str, allies: list[str]) -> tuple[str, Game, dict]:
    if mode not in ("daily", "practice"):
        raise RunError("bad mode")
    run_id = secrets.token_urlsafe(9)
    partner = _clean_name(partner) or lore.DEFAULT_NAMES[secrets.randbelow(len(lore.DEFAULT_NAMES))]
    allies = [a for a in (_clean_name(x) for x in allies[:2]) if a and a != partner]
    try:
        setup = new_setup(int(district), trait, partner, list(dict.fromkeys(allies)))
    except (TypeError, ValueError) as e:
        raise RunError(str(e))
    payload = {"v": 1, "id": run_id, "m": mode, "d": today.isoformat(), "s": setup, "c": []}
    game = _build(settings, payload)
    return encode(settings, payload), game, payload


def _build(settings: Settings, p: dict) -> Game:
    if p["m"] == "daily":
        seed = seed_for(settings, date.fromisoformat(p["d"]))
    else:
        seed = _derive(settings, "practice", p["id"])
    salt = _derive(settings, "salt", p["id"])
    try:
        return replay(seed, salt, p["s"], p["c"])
    except ValueError as e:
        raise RunError(str(e))


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


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else ORDINAL.get(n % 10, 'th')}"


def result(settings: Settings, game: Game, p: dict) -> dict | None:
    if not game.over:
        return None
    number = daily_for(settings, date.fromisoformat(p["d"])).number
    title = f"HUNGER TRAIL #{number}" if p["m"] == "daily" else "HUNGER TRAIL (practice)"
    if game.won:
        line = f"👑 VICTOR · {game.kills} kill{'s' if game.kills != 1 else ''} · Day {game.day}"
        end = "Won the Hunger Games."
    else:
        line = f"Placed {ordinal(game.placement)} of 24 · {game.kills} kill{'s' if game.kills != 1 else ''} · Day {game.day}"
        end = f"Killed by: {game.cause}"
    share = "\n".join([f"{title} · District {game.district}", line, game.emoji_row(), end, "hungertrail.com"])
    return {
        "won": game.won,
        "placement": game.placement,
        "placement_text": "Victor" if game.won else f"{ordinal(game.placement)} of 24",
        "kills": game.kills,
        "day": game.day,
        "cause": game.cause,
        "score": game.score(),
        "multiplier": lore.multiplier(game.district),
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
        "trait": lore.TRAITS[game.trait][0],
        "status": game.status(),
        "log": game.log,
        "card": game.card,
        "options": game.options(),
        "over": game.over,
        "result": result(settings, game, p),
    }

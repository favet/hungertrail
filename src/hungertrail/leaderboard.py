"""Daily leaderboard. SQLite when running locally; a Cloudflare Worker + D1 in production.

Render's free disk is wiped on every deploy, so production scores live in D1 behind a small
Worker (ops/leaderboard). The Worker trusts the first key that registers with it; this app
derives that key from its own secret and registers on startup.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request

from .config import Settings

NICK_RE = re.compile(r"^[A-Za-z0-9_.\- ]{2,16}$")
BLOCKED = ("fuck", "shit", "cunt", "nigg", "fag", "rape", "nazi", "hitler", "kike", "retard", "slut", "whore")
FIELDS = ("date", "nickname", "run_id", "score", "placement", "kills", "day", "district", "cause", "emoji")


class BoardError(ValueError):
    pass


def clean_nickname(nick: str) -> str:
    nick = " ".join((nick or "").split())
    if not NICK_RE.match(nick):
        raise BoardError("Nicknames are 2-16 letters, numbers, spaces, dots, dashes or underscores.")
    if any(b in nick.lower().replace(" ", "") for b in BLOCKED):
        raise BoardError("Pick a different nickname.")
    return nick


class LocalBoard:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS scores (
        date TEXT NOT NULL, nickname TEXT NOT NULL, nick_key TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE, score INTEGER NOT NULL, placement INTEGER NOT NULL,
        kills INTEGER NOT NULL, day INTEGER NOT NULL, district INTEGER NOT NULL,
        cause TEXT, emoji TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (date, nick_key)
    );
    CREATE INDEX IF NOT EXISTS scores_by_day ON scores (date, score DESC);
    """

    def __init__(self, settings: Settings):
        self.path = settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(self.SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def submit(self, entry: dict) -> None:
        try:
            with self._conn() as c:
                c.execute(
                    f"INSERT INTO scores ({', '.join(FIELDS)}, nick_key) VALUES ({', '.join('?' * len(FIELDS))}, ?)",
                    [entry[f] for f in FIELDS] + [entry["nickname"].lower()],
                )
        except sqlite3.IntegrityError:
            raise BoardError("That nickname already has a score today, or this run was already submitted.")

    def top(self, day: str, limit: int = 50) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT nickname, score, placement, kills, day, district, emoji FROM scores "
                "WHERE date = ? ORDER BY score DESC, created_at ASC LIMIT ?",
                (day, limit),
            ).fetchall()
        return [dict(r) for r in rows]


class RemoteBoard:
    def __init__(self, settings: Settings, url: str):
        self.url = url.rstrip("/")
        self.key = hmac.new(settings.seed_secret.encode(), b"leaderboard", hashlib.sha256).hexdigest()
        self._cache: dict[str, tuple[float, list]] = {}
        self.registered = False

    def _req(self, method: str, path: str, body: dict | None = None, auth: bool = False) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("content-type", "application/json")
        req.add_header("user-agent", "hungertrail-app")
        if auth:
            req.add_header("authorization", f"Bearer {self.key}")
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def register(self) -> None:
        status, _ = self._req("POST", "/register", {"key": self.key})
        self.registered = status in (200, 201)

    def submit(self, entry: dict) -> None:
        status, body = self._req("POST", "/submit", entry, auth=True)
        if status == 409:
            raise BoardError("That nickname already has a score today, or this run was already submitted.")
        if status != 200:
            raise BoardError(body.get("error", "The leaderboard is unavailable right now."))
        self._cache.pop(entry["date"], None)

    def top(self, day: str, limit: int = 50) -> list[dict]:
        hit = self._cache.get(day)
        if hit and time.monotonic() - hit[0] < 20:
            return hit[1]
        status, body = self._req("GET", f"/board?date={day}&limit={limit}")
        rows = body.get("rows", []) if status == 200 else []
        self._cache[day] = (time.monotonic(), rows)
        return rows


def make_board(settings: Settings, url: str | None):
    if url:
        board = RemoteBoard(settings, url)
        threading.Thread(target=board.register, daemon=True).start()
        return board
    return LocalBoard(settings)

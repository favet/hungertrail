"""SQLite storage. Schema is a placeholder until the game loop is designed."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    day         TEXT    NOT NULL,          -- ISO date of the daily seed, NULL-able later for practice runs
    nickname    TEXT    NOT NULL,
    placement   INTEGER,                   -- 1 = victor, 24 = first to fall
    score       INTEGER,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS runs_day_score ON runs (day, score DESC);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(SCHEMA)

"""Runtime settings, read from environment variables (or a .env file loaded by the shell)."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    # Secret mixed into every daily seed so players can't precompute tomorrow's arena.
    seed_secret: str
    # The timezone whose midnight starts a new daily game (one global day for the leaderboard).
    timezone: ZoneInfo
    # Day #1 of the daily game.
    launch_date: date
    host: str
    port: int
    db_path: Path


def load_settings() -> Settings:
    return Settings(
        seed_secret=os.environ.get("HT_SEED_SECRET", "dev-only-not-secret"),
        timezone=ZoneInfo(os.environ.get("HT_TIMEZONE", "America/Los_Angeles")),
        launch_date=date.fromisoformat(os.environ.get("HT_LAUNCH_DATE", "2026-11-23")),
        # Bind to localhost only: Cloudflare Tunnel connects from this same machine.
        host=os.environ.get("HT_HOST", "127.0.0.1"),
        port=int(os.environ.get("HT_PORT", "8090")),
        db_path=Path(os.environ.get("HT_DB_PATH", "data/hungertrail.sqlite3")),
    )


settings = load_settings()

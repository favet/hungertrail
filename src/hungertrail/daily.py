"""Daily seed: everyone playing on the same calendar day gets the same arena."""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import date, datetime

from .config import Settings


@dataclass(frozen=True)
class Daily:
    date: date
    number: int  # "Hunger Trail #N", for share text
    seed: int  # never sent to the client


def today(settings: Settings, now: datetime | None = None) -> date:
    now = now or datetime.now(settings.timezone)
    return now.astimezone(settings.timezone).date()


def seed_for(settings: Settings, day: date) -> int:
    digest = hmac.new(
        settings.seed_secret.encode(), day.isoformat().encode(), hashlib.sha256
    ).digest()
    return int.from_bytes(digest[:8], "big")


def daily_for(settings: Settings, day: date) -> Daily:
    return Daily(
        date=day,
        number=(day - settings.launch_date).days + 1,
        seed=seed_for(settings, day),
    )

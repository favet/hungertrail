"""Scripted players for balance testing: `uv run python -m hungertrail.game.bots`."""

from __future__ import annotations

import random
import statistics
import sys
from collections import Counter

from . import lore
from .engine import Game, new_setup


def _best(opts, key):
    return max(opts, key=key)


def random_bot(g: Game, r: random.Random):
    o = r.choice(g.options())
    return o["index"], r.randint(0, 3)


def hider(g: Game, r: random.Random):
    opts = g.options()
    for tag in ("hide", "safe"):
        pick = [o for o in opts if o["tag"] == tag]
        if pick:
            return _best(pick, lambda o: o["chance"] or 100)["index"], 1
    return opts[0]["index"], 1


def brute(g: Game, r: random.Random):
    opts = g.options()
    bold = [o for o in opts if o["tag"] == "bold"]
    if bold:
        return _best(bold, lambda o: o["chance"] or 100)["index"], 2
    return _best(opts, lambda o: o["chance"] or 100)["index"], 2


def smart(g: Game, r: random.Random):
    """Plays the visible numbers the way an attentive human would."""
    opts = g.options()
    by_label = {o["label"]: o for o in opts}
    if g.card == "camp":
        if "Tend your wounds" in by_label and g.health < 55:
            return by_label["Tend your wounds"]["index"], None
        if g.health < 45 and g.water >= 1:
            return by_label["Rest"]["index"], None
        if g.food == 0 and "Hunt" in by_label:
            return by_label["Hunt"]["index"], r.choice([1, 2, 2, 3])
        if g.food == 0 and "Set snares" in by_label:
            return by_label["Set snares"]["index"], None
        if g.water <= 1 or g.food == 0:
            return by_label["Forage"]["index"], None
        if g.heat >= 30:
            return by_label["Play to the cameras"]["index"], None
        if g.health < 75:
            return by_label["Rest"]["index"], None
        return by_label["Play to the cameras"]["index"], None

    def value(o):
        c = o["chance"]
        if c is None:
            v = 70 if o["tag"] != "hide" else 55 - g.heat / 2
            if o["tag"] == "bold":
                v = 60
        else:
            v = c
            if o["tag"] == "bold":
                v += 10
            if g.health < 45:
                v -= 15 if o["tag"] == "bold" else 0
        return v

    return _best(opts, value)["index"], r.choice([1, 2, 2, 3])


BOTS = {"random": random_bot, "hider": hider, "brute": brute, "smart": smart}


def play(bot, seed: int, salt: int, r: random.Random) -> Game:
    district = r.randint(1, 12)
    trait = r.choice(list(lore.TRAITS))
    allies = r.sample(lore.DEFAULT_NAMES, r.randint(0, 2))
    g = Game(seed, salt, new_setup(district, trait, "Partner", allies))
    guard = 0
    while not g.over:
        idx, param = bot(g, r)
        g.choose(idx, param)
        guard += 1
        if guard > 200:
            raise RuntimeError(f"run did not end: seed={seed}")
    return g


def report(runs: int = 2000, seeds: int = 30) -> dict:
    out = {}
    for name, bot in BOTS.items():
        r = random.Random(name)
        games = [play(bot, i % seeds, i, r) for i in range(runs)]
        wins = sum(g.won for g in games)
        out[name] = {
            "win%": round(100 * wins / runs, 1),
            "placement": round(statistics.mean(g.placement for g in games), 1),
            "steps": round(statistics.mean(g.steps for g in games), 1),
            "steps_max": max(g.steps for g in games),
            "day": round(statistics.mean(g.day for g in games), 1),
            "kills": round(statistics.mean(g.kills for g in games), 2),
            "causes": Counter(g.cause for g in games if not g.won).most_common(6),
            "died_day1%": round(100 * sum(1 for g in games if not g.won and g.day == 1) / runs, 1),
        }
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for name, row in report().items():
        causes = row.pop("causes")
        print(name.ljust(7), row)
        print("        ", causes)

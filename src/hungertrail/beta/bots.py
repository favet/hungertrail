"""Scripted players for beta balance: `uv run python -m hungertrail.beta.bots [--districts]`."""

from __future__ import annotations

import random
import statistics
import sys
from collections import Counter

from . import lore
from .engine import CAMP, DRAFT, DUEL, SWAP, Game, new_setup

VALUE = {"trident": 9, "bow": 9, "spear": 7, "axe": 6, "rope": 6, "medkit": 5, "knife": 5, "sleeping_bag": 4, "iodine": 4}


def _best(opts, key):
    return max(opts, key=key)


def random_bot(g: Game, r: random.Random):
    return r.choice(g.options())["index"], r.randint(0, 3)


def hider(g: Game, r: random.Random):
    opts = g.options()
    for tag in ("hide", "safe"):
        pick = [o for o in opts if o["tag"] == tag]
        if pick:
            return _best(pick, lambda o: o["chance"] or 100)["index"], 1
    return opts[0]["index"], 1


def brute(g: Game, r: random.Random):
    opts = g.options()
    if g.card == SWAP:
        return opts[0]["index"], None
    bold = [o for o in opts if o["tag"] == "bold"]
    if bold:
        return _best(bold, lambda o: o["chance"] or 100)["index"], 2
    return _best(opts, lambda o: o["chance"] or 100)["index"], 2


def smart(g: Game, r: random.Random):
    """Reads the stakes on screen the way an attentive player would."""
    opts = g.options()
    by = {o["label"]: o for o in opts}
    if g.card == DRAFT:
        grabs = [o for o in opts if o["chance"] is not None and (o["chance"] >= 55 or g.grabs == 0)]
        if grabs and g.health >= 60:
            def v(o):
                item = next(k for k in list(lore.ITEMS) + list(lore.PACKS) if o["label"].endswith(lore.item_name(k)))
                return (VALUE.get(item, 4) + 1) * o["chance"]
            return _best(grabs, v)["index"], None
        return opts[-1]["index"], None
    if g.card == SWAP:
        worst = min(range(len(g.kit)), key=lambda i: VALUE.get(g.kit[i], 3))
        if VALUE.get(g.pending_item, 3) > VALUE.get(g.kit[worst], 3):
            return worst, None
        return opts[-1]["index"], None
    if g.card == DUEL:
        return _best(opts, lambda o: o["chance"])["index"], None
    if g.card == CAMP:
        if "Use the medkit" in by and g.health < 55:
            return by["Use the medkit"]["index"], None
        if g.health < 45:
            return by["Rest"]["index"], None
        if g.supplies <= 1:
            for k in ("Hunt", "Set snares", "Forage"):
                if k in by:
                    return by[k]["index"], r.choice([1, 2, 2, 3])
        if g.crowd < 10 or g.health >= 80:
            return by["Play to the cameras"]["index"], None
        return by["Rest"]["index"], None

    def value(o):
        c = o["chance"] if o["chance"] is not None else 100
        v = c + (8 if o["crowd"] else 0)
        if o["deadly"]:
            v -= 40
        v -= 5 * sum(1 for x in o["lose"] if x.startswith("❤️"))
        v += 5 * len(o["win"])
        return v

    return _best(opts, value)["index"], r.choice([1, 2, 2, 3])


BOTS = {"random": random_bot, "hider": hider, "brute": brute, "smart": smart}


def play(bot, seed: int, district: int | None = None) -> Game:
    r = random.Random(seed)
    d = district or r.randint(1, 12)
    g = Game(seed, seed * 7919 + 1, new_setup(d, "Partner", ["Ally"] if r.random() < 0.5 else []))
    for _ in range(200):
        if g.over:
            break
        idx, param = bot(g, r)
        g.choose(idx, param)
    assert g.over, "run did not end"
    return g


def report(n: int = 2000) -> None:
    for name, bot in BOTS.items():
        games = [play(bot, s) for s in range(n)]
        wins = sum(g.won for g in games) / n
        steps = statistics.mean(g.steps for g in games)
        place = statistics.mean(g.placement for g in games)
        causes = Counter(g.cause for g in games if not g.won).most_common(5)
        gm = sum(any(c.startswith("gm_") for c in g.used) for g in games) / n
        duel = sum(g.opponent is not None for g in games) / n
        print(f"{name:7} win {wins:5.1%}  place {place:4.1f}  steps {steps:4.1f}  gamemakers {gm:4.0%}  duel {duel:4.0%}")
        print("        deaths:", ", ".join(f"{c} {k / n:.0%}" for c, k in causes))


def districts(n: int = 1500) -> None:
    for d in range(1, 13):
        games = [play(smart, s, d) for s in range(n)]
        wins = sum(g.won for g in games) / n
        print(f"District {d:2} ({lore.PERKS[d][0]:10}) win {wins:5.1%}  place {statistics.mean(g.placement for g in games):4.1f}")


if __name__ == "__main__":
    districts() if "--districts" in sys.argv else report()

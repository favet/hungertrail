"""Scripted players for beta balance: `uv run python -m hungertrail.beta.bots [--districts]`."""

from __future__ import annotations

import random
import statistics
import sys
from collections import Counter

from . import lore
from . import minigames as mg
from .engine import DRAFT, DUEL, PLACES, SIFT, SIFT_BIG, SIFT_SMALL, SIFT_STOP, SWAP, USE, Game, new_setup  # noqa: F401

VALUE = {"trident": 9, "bow": 9, "spear": 7, "axe": 6, "rope": 6, "medkit": 5, "knife": 5, "sleeping_bag": 4, "iodine": 4}


def _best(opts, key):
    return max(opts, key=key)


def random_bot(g: Game, r: random.Random):
    return r.choice(g.options())["index"]


def hider(g: Game, r: random.Random):
    opts = g.options()
    if g.card == SIFT:
        v = g.sift_view()
        return 0 if v["how"] is None and g.supplies >= 1 else SIFT_STOP if v["how"] else opts[-1]["index"]
    for tag in ("hide", "safe"):
        pick = [o for o in opts if o["tag"] == tag]
        if pick:
            return _best(pick, lambda o: o["chance"] or 100)["index"]
    return opts[0]["index"]


def brute(g: Game, r: random.Random):
    opts = g.options()
    if g.card == SWAP:
        return opts[0]["index"]
    if g.card == SIFT:
        return sifter(g, r, greedy=True)
    bold = [o for o in opts if o["tag"] == "bold"]
    if bold:
        return _best(bold, lambda o: o["chance"] or 100)["index"]
    return _best(opts, lambda o: o["chance"] or 100)["index"]


def sifter(g: Game, r: random.Random, greedy: bool = False):
    """Digs the way a player who reads the pieces would: finish what's half dug, else open new ground."""
    v = g.sift_view()
    if v["how"] is None:
        return 1 if greedy or g.supplies < 2 else 0
    if g.health < 30 and not greedy:
        return SIFT_STOP
    n = v["size"]
    tiles = v["tiles"]
    closed = {i for i in range(n * n) if not tiles[i]["open"]}

    def fits(cells, sid):
        return all(c in closed or tiles[c].get("shape") == sid for c in cells)

    def area(i):
        x, y = i % n, i // n
        return [(y + dy) * n + x + dx for dy in (-1, 0, 1) for dx in (-1, 0, 1) if 0 <= x + dx < n and 0 <= y + dy < n]

    # 1. Finish a treasure you've found a piece of: count where its rectangle could still lie.
    for t in v["legend"]:
        if t["trap"] or t["done"] or not t["found"]:
            continue
        seen = [i for i in range(n * n) if tiles[i].get("shape") == t["shape"]]
        votes: dict[int, int] = {}
        for w, h in {(t["w"], t["h"]), (t["h"], t["w"])}:
            for y in range(n - h + 1):
                for x in range(n - w + 1):
                    cells = [(y + dy) * n + x + dx for dy in range(h) for dx in range(w)]
                    if all(c in cells for c in seen) and fits(cells, t["shape"]):
                        for c in cells:
                            if c in closed:
                                votes[c] = votes.get(c, 0) + 1
        if votes:
            best = max(votes, key=lambda c: (votes[c], r.random()))
            left = t["w"] * t["h"] - t["found"]
            return (SIFT_BIG if left >= 4 and v["digs"] > 0 else SIFT_SMALL) + best
    # 2. Open new ground where a big dig turns the most closed tiles.
    best = max(closed, key=lambda i: (sum(c in closed for c in area(i)), r.random()))
    return SIFT_BIG + best


def shopper(g: Game):
    """Spend stars on what keeps you alive: medicine when hurt, food when there's none."""
    menu = {m["key"]: m for m in g.sponsor_menu() if m["can"]}
    if g.health < 50 and "medicine" in menu:
        return menu["medicine"]["index"]
    if g.supplies == 0 and "food" in menu:
        return menu["food"]["index"]
    return None


def smart(g: Game, r: random.Random):
    """Reads the stakes on screen the way an attentive player would."""
    if g.can_use_medkit() and g.health < 45:
        return USE
    buy = shopper(g)
    if buy is not None:
        return buy
    opts = g.options()
    if g.card == SIFT:
        return sifter(g, r)
    if g.card == DRAFT:
        grabs = [o for o in opts if o["chance"] is not None and (o["chance"] >= 55 or g.grabs == 0)]
        if grabs and g.health >= 60:
            def v(o):
                item = next(k for k in list(lore.ITEMS) + list(lore.PACKS) if o["label"].endswith(lore.item_name(k)))
                return (VALUE.get(item, 4) + 1) * o["chance"]
            return _best(grabs, v)["index"]
        return opts[-1]["index"]
    if g.card == SWAP:
        worst = min(range(len(g.kit)), key=lambda i: VALUE.get(g.kit[i], 3))
        if VALUE.get(g.pending_item, 3) > VALUE.get(g.kit[worst], 3):
            return worst
        return opts[-1]["index"]
    if g.card == DUEL:
        # Heavy strikes while you can afford to take a hit; guard when you can't.
        aggressive, careful = opts
        take = g._fight_take("aggressive")
        return aggressive["index"] if g.health > take * 2 or "you win" in aggressive["win"] else careful["index"]
    if g.card == PLACES:
        by = {o["place"]: o for o in opts}
        safe_from_gm = {k: o for k, o in by.items() if not any("Gamemakers" in x for x in o["lose"])}
        if g.health < 45:
            for k in ("rocks", "stream", "open"):
                if k in by and (k in safe_from_gm or g.health < 30):
                    return by[k]["index"]
        if g.supplies <= 1:
            for k in ("stream", "ridge"):
                if k in safe_from_gm:
                    return by[k]["index"]
        armed = any(w in g.kit for w in lore.WEAPONS)
        prefs = ("feast", "horn", "open", "stream") if armed and g.health >= 70 else ("feast", "open", "stream", "ridge", "horn")
        for k in prefs:
            if k in safe_from_gm:
                return by[k]["index"]
        return opts[0]["index"]

    def value(o):
        c = o["chance"] if o["chance"] is not None else 85 if o.get("minigame") else 100
        v = c
        if o["deadly"]:
            v -= 40
        v -= 5 * sum(1 for x in o["lose"] if x.endswith(" health"))
        v += 5 * len(o["win"])
        return v

    return _best(opts, value)["index"]


def casual(g: Game, r: random.Random):
    """Smart choices, first-time hands (see SKILL)."""
    return smart(g, r)


BOTS = {"random": random_bot, "hider": hider, "brute": brute, "smart": smart, "casual": casual}
# How good each bot's hands are in the minigames: 0 is a first-time player, 1 a sharp one.
# "smart" is a good player (about 0.65), "casual" an average one. Calibrate against playtests.
SKILL = {"random": 0.4, "hider": 0.4, "brute": 0.4, "smart": 0.65, "casual": 0.4}
PLAYERS = {"fish": mg.bot_fish, "hunt": mg.bot_hunt, "hide": mg.bot_hide, "fight": mg.bot_fight}


def hands(g: Game, idx: int, skill: float, r: random.Random):
    """What the bot's thumbs do in the minigame behind option idx, if it has one."""
    o = next((o for o in g.options() if o["index"] == idx), None)
    game = o and o.get("minigame")
    return PLAYERS[game["game"]](game, skill, r) if game else None


def play(bot, seed: int, district: int | None = None, skill: float | None = None) -> Game:
    r = random.Random(seed)
    d = district or r.randint(1, 12)
    g = Game(seed, seed * 7919 + 1, new_setup(d, "Partner", ["Ally"] if r.random() < 0.5 else []))
    if skill is None:
        skill = SKILL.get(next((k for k, v in BOTS.items() if v is bot), ""), 0.5)
    for _ in range(300):
        if g.over:
            break
        idx = bot(g, r)
        g.choose(idx, hands(g, idx, skill, r))
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
        nem = sum(g.opponent_id == g.nemesis for g in games) / n
        print(f"{name:7} win {wins:5.1%}  place {place:4.1f}  steps {steps:4.1f}  gamemakers {gm:4.0%}  duel {duel:4.0%} (vs nemesis {nem:4.0%})")
        print("        deaths:", ", ".join(f"{c} {k / n:.0%}" for c, k in causes))
        stars = statistics.mean(g.stars for g in games)
        bought = statistics.mean(g.bought for g in games)
        print(f"        stars at the end {stars:.1f}, parachutes bought {bought:.1f}, score {statistics.mean(g.score() for g in games):.0f}")


def districts(n: int = 1500) -> None:
    for d in range(1, 13):
        games = [play(smart, s, d) for s in range(n)]
        wins = sum(g.won for g in games) / n
        print(f"District {d:2} ({lore.PERKS[d][0]:10}) win {wins:5.1%}  place {statistics.mean(g.placement for g in games):4.1f}")


if __name__ == "__main__":
    districts() if "--districts" in sys.argv else report()

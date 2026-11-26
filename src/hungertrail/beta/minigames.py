"""Beta minigames: pure, integer-only simulations the server replays from the player's inputs.

The browser plays each game and sends back only what the player did (the ticks they pressed,
released or tapped). The server runs the same simulation on those inputs and gets the same
result, so a run is still (seed, salt, setup, choices) and nobody can just claim three hits.

`static/beta/minigames.js` is the browser copy of the `*_run` functions and `path_at`. Keep the
two in step: `tests/test_minigames.py` runs both on the same inputs and compares.

Everything runs at TPS ticks a second. Positions are integers from 0 to 10000. A path is a list
of [tick, position] points; between points the position moves in a straight line.
"""

from __future__ import annotations

import random

TPS = 60
SPAN = 10000
MAX_INPUTS = 400


# ------------------------------------------------------------------ shared
def path_at(path: list[list[int]], t: int) -> int:
    if t <= path[0][0]:
        return path[0][1]
    for (t0, p0), (t1, p1) in zip(path, path[1:]):
        if t <= t1:
            return p0 + (p1 - p0) * (t - t0) // (t1 - t0)
    return path[-1][1]


def tri(x: int) -> int:
    """A marker sweeping 0 -> SPAN -> 0 -> ... as x grows."""
    d = x % (2 * SPAN)
    return d if d <= SPAN else 2 * SPAN - d


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def clean_toggles(raw, duration: int) -> list[int]:
    """Press/release ticks: increasing, inside the game, not too many."""
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_INPUTS:
        raise ValueError("bad minigame input")
    out = []
    for x in raw:
        if not isinstance(x, int) or isinstance(x, bool) or not 0 <= x < duration or (out and x <= out[-1]):
            raise ValueError("bad minigame input")
        out.append(x)
    return out


def _ints(raw, n: int) -> list[int]:
    if raw is None:
        return [-1] * n
    if not isinstance(raw, list) or len(raw) > n or not all(isinstance(x, int) and not isinstance(x, bool) for x in raw):
        raise ValueError("bad minigame input")
    return [max(-1, x) for x in raw] + [-1] * (n - len(raw))


# ------------------------------------------------------------------ fishing (the stream)
# Hold to raise the bar, let go and it falls. Keep it over the fish to fill the catch meter.
FISH_KINDS = {
    # kind: emoji, supplies if caught, health if caught, catch meter gain per tick on the fish
    "minnow": ("🐟", 1, 0, 5),
    "trout": ("🐠", 2, 0, 4),
    "leech": ("🪱", 0, -12, 5),
}


def fish_params(r: random.Random, kind: str, bar: int) -> dict:
    duration = 20 * TPS
    # A leech stays above your bar while it rests, so letting it go means just not reeling.
    low = bar + 1200 if kind == "leech" else 500
    pos = max(low, r.randint(3500, 6500))
    path = [[0, pos]]
    t = 0
    while t < duration:
        if kind == "trout":
            if r.random() < 0.35:
                t += r.randint(15, 40)  # it holds still, then darts
            else:
                t += r.randint(14, 30)
                pos = _clamp(pos + r.choice((-1, 1)) * r.randint(1500, 4500), 500, 9500)
        elif kind == "leech":
            t += r.randint(50, 90)
            pos = _clamp(pos + r.choice((-1, 1)) * r.randint(500, 1800), low, 9500)
        else:
            if r.random() < 0.25:
                t += r.randint(20, 50)
            else:
                t += r.randint(40, 80)
                pos = _clamp(pos + r.choice((-1, 1)) * r.randint(800, 2500), 500, 9500)
        path.append([t, pos])
    emoji, food, health, gain = FISH_KINDS[kind]
    return {
        "game": "fish", "kind": kind, "emoji": emoji, "duration": duration, "path": path,
        "bar": bar, "up": 6, "down": 4, "vmax": 140, "start": 300, "gain": gain, "loss": 3,
    }


def fish_run(p: dict, toggles: list[int]) -> tuple[bool, int]:
    """Returns (caught, ticks played)."""
    hold, y, v, c, i = False, 0, 0, p["start"], 0
    top = SPAN - p["bar"]
    for t in range(p["duration"]):
        while i < len(toggles) and toggles[i] <= t:
            hold = not hold
            i += 1
        v += p["up"] if hold else -p["down"]
        v = _clamp(v, -p["vmax"], p["vmax"])
        y += v
        if y < 0:
            y, v = 0, -v // 2
        if y > top:
            y, v = top, 0
        f = path_at(p["path"], t)
        c += p["gain"] if y <= f <= y + p["bar"] else -p["loss"]
        if c >= 1000:
            return True, t + 1
        if c <= 0:
            return False, t + 1
    return False, p["duration"]


# ------------------------------------------------------------------ hunting (the ridge)
# A marker sweeps the track; animals move along it. Tap when the marker is on one.
ANIMALS = {
    # kind: emoji, supplies, half-width of the target
    "rabbit": ("🐇", 1, 500),
    "deer": ("🦌", 2, 900),
    "groosling": ("🦃", 1, 700),
}


def _animal_path(r: random.Random, kind: str, duration: int) -> list[list[int]]:
    pos = r.randint(1500, 8500)
    path = [[0, pos]]
    t = 0
    while t < duration:
        if kind == "rabbit":
            t += r.randint(12, 26)  # sits...
            path.append([t, pos])
            t += r.randint(6, 10)  # ...then hops
            pos = _clamp(pos + r.choice((-1, 1)) * r.randint(900, 1900), 700, 9300)
        elif kind == "deer":
            t += r.randint(60, 100)
            pos = _clamp(pos + r.choice((-1, 1)) * r.randint(600, 1600), 1000, 9000)
        else:
            t += r.randint(25, 45)
            pos = _clamp(pos + r.choice((-1, 1)) * r.randint(700, 1500), 800, 9200)
        path.append([t, pos])
    return path


def hunt_params(r: random.Random, weapon: str, extra_bird: bool) -> dict:
    """Bow: three arrows. Spear or trident: one throw at bigger targets, and more meat."""
    spear = weapon != "bow"
    speeds = (170,) if spear else (150, 200, 250)
    duration = 5 * TPS
    shots = []
    for speed in speeds:
        kinds = ["rabbit", "deer"] + (["groosling"] if extra_bird else [])
        animals = []
        for kind in kinds:
            emoji, food, half = ANIMALS[kind]
            animals.append({
                "kind": kind, "emoji": emoji, "food": food + (1 if spear else 0),
                "half": half * 3 // 2 if spear else half, "path": _animal_path(r, kind, duration),
            })
        shots.append({"speed": speed, "duration": duration, "animals": animals})
    return {"game": "hunt", "weapon": weapon, "shots": shots}


def hunt_run(p: dict, taps: list[int]) -> tuple[int, list[str | None]]:
    """Returns (supplies, what each shot hit). One deer per hunt: it bolts at your first miss."""
    deer, food, hits = True, 0, []
    for n, s in enumerate(p["shots"]):
        t = taps[n] if n < len(taps) else -1
        hit = None
        if 0 <= t < s["duration"]:
            m = tri(s["speed"] * t)
            best = None
            for a in s["animals"]:
                if a["kind"] == "deer" and not deer:
                    continue
                d = abs(m - path_at(a["path"], t))
                if d <= a["half"] and (best is None or d < best[0]):
                    best = (d, a)
            hit = best[1] if best else None
        if hit:
            food += hit["food"]
            hits.append(hit["kind"])
            if hit["kind"] == "deer":
                deer = False
        else:
            hits.append(None)
            deer = False
    return food, hits


# ------------------------------------------------------------------ hold still (hiding)
# A searcher's light sweeps the ground. Hold to stay down (your breath drains), let go to breathe,
# but never while the light is on you.
def _cover(path: list[list[int]], me: int, half: int, duration: int) -> list[tuple[int, int]]:
    out, start = [], None
    for t in range(duration):
        on = abs(path_at(path, t) - me) <= half
        if on and start is None:
            start = t
        if not on and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, duration))
    return out


def hide_params(r: random.Random, base: int, breath_bonus: int, half_bonus: int, boss: bool) -> dict:
    duration = 9 * TPS
    me = 5000
    half = _clamp(1300 - (base - 60) * 15, 800, 1700) + half_bonus + (300 if boss else 0)
    breath = 130 + breath_bonus
    margin = 22
    lo, hi = (45, 80) if boss else (60, 110)
    for _ in range(300):
        side = r.choice((-2000, 12000))
        path, t, pos = [[0, side]], 0, side
        while t < duration:
            if r.random() < 0.3:
                t += r.randint(15, 45)  # stops and looks around
            else:
                nxt = r.randint(-1500, 11500)
                t += max(10, abs(nxt - pos) // r.randint(lo, hi))
                pos = nxt
            path.append([t, pos])
        cover = _cover(path, me, half, duration)
        ok = (
            len(cover) >= 2 and cover[0][0] >= 50 and cover[-1][1] <= duration - 10
            and all(b - a <= breath - margin for a, b in cover)
            and all(c[0] - b[1] >= 30 for b, c in zip(cover, cover[1:]))
        )
        if ok:
            break
    else:  # a plain sweep back and forth: always fair
        path = [[0, -2000], [120, 12000], [140, 12000], [260, -2000], [280, -2000], [400, 12000], [540, 12000]]
    return {"game": "hide", "duration": duration, "me": me, "half": half, "breath": breath, "refill": 3,
            "path": path, "boss": boss}


def hide_run(p: dict, toggles: list[int]) -> tuple[str, int]:
    """Returns ("hidden" | "spotted" | "gasp", tick it ended)."""
    hold, b, i = False, p["breath"], 0
    for t in range(p["duration"]):
        while i < len(toggles) and toggles[i] <= t:
            hold = not hold
            i += 1
        if hold:
            b -= 1
            if b <= 0:
                return "gasp", t
        else:
            b = min(p["breath"], b + p["refill"])
        if not hold and abs(path_at(p["path"], t) - p["me"]) <= p["half"]:
            return "spotted", t
    return "hidden", p["duration"]


# ------------------------------------------------------------------ read and react (the final fight)
# They wind up on one side. Block that side as the blow lands; a clean block opens a moment to strike.
def fight_params(r: random.Random, stance: str, boss: bool, rope: bool, weapon_bonus: int, exchange: int = 0) -> dict:
    """They wind up on `first`; if they feint, the blow comes from the other side at `feint`.
    Every exchange they get a little faster and feint a little more: nobody can block forever."""
    tell = r.randint(40, 90)
    lo, hi = (28, 48) if boss else (36, 60)
    windup = max(20, r.randint(lo, hi) - 3 * exchange)
    impact = tell + windup
    first = r.randint(0, 1)
    feint = tell + windup // 2 if r.random() < (0.5 if boss else 0.3) + 0.06 * exchange else -1
    side = 1 - first if feint >= 0 else first
    early = (20 if stance == "careful" else 12) + (8 if rope else 0)
    open_at = impact + 8
    open_len = (20 if stance == "careful" else 26) + weapon_bonus // 2
    return {
        "game": "fight", "stance": stance, "first": first, "side": side, "feint": feint, "tell": tell,
        "impact": impact, "block": [impact - early, impact + 5], "open": [open_at, open_at + open_len],
        "duration": open_at + open_len + 20,
    }


def fight_run(p: dict, inputs: list[int]) -> tuple[bool, bool]:
    """inputs = [block tick, block side, strike tick]. Returns (blocked, struck)."""
    bt, bs, st = inputs[:3]
    blocked = p["block"][0] <= bt <= p["block"][1] and bs == p["side"]
    struck = blocked and p["open"][0] <= st < p["open"][1]
    return blocked, struck


def clean_fight(raw) -> list[int]:
    return _ints(raw, 3)


def clean_taps(raw, shots: int) -> list[int]:
    return _ints(raw, shots)


# ------------------------------------------------------------------ players for the bots
# Skill is how good the hands are: 0 is a first-time player, 1 a sharp one.
def bot_fish(p: dict, skill: float, r: random.Random) -> list[int]:
    lag = round(14 - 8 * skill + r.randint(0, 4))
    slack = round(900 - 600 * skill)  # how far off the fish it lets the bar drift before reacting
    hold, y, v = False, 0, 0
    top = SPAN - p["bar"]
    toggles = []
    for t in range(p["duration"]):
        target = path_at(p["path"], max(0, t - lag))
        err = (target - (y + p["bar"] // 2)) - v * 10
        want = err > slack // 4 if not hold else err > -slack
        if want != hold and len(toggles) < MAX_INPUTS:
            toggles.append(t)
            hold = want
        v += p["up"] if hold else -p["down"]
        v = _clamp(v, -p["vmax"], p["vmax"])
        y += v
        if y < 0:
            y, v = 0, -v // 2
        if y > top:
            y, v = top, 0
    caught, n = fish_run(p, toggles)
    return [x for x in toggles if x < n] if p["kind"] != "leech" or not caught else []


def bot_hunt(p: dict, skill: float, r: random.Random) -> list[int]:
    taps, deer = [], True
    for s in p["shots"]:
        animals = [a for a in s["animals"] if deer or a["kind"] != "deer"]
        goal = max(animals, key=lambda a: a["food"] * (1.2 if a["kind"] == "deer" else 1))
        tap = -1
        for t in range(20, s["duration"]):
            if abs(tri(s["speed"] * t) - path_at(goal["path"], t)) <= goal["half"] // 3:
                tap = t
                break
        if tap >= 0:
            tap = max(0, min(s["duration"] - 1, tap + round(r.gauss(0, 4.5 - 3 * skill))))
        taps.append(tap)
        food, hits = hunt_run(p, taps)
        deer = deer and hits[-1] is not None and hits[-1] != "deer"
    return taps


def bot_hide(p: dict, skill: float, r: random.Random) -> list[int]:
    toggles = []
    for a, b in _cover(p["path"], p["me"], p["half"], p["duration"]):
        lead = round(r.gauss(16 + 6 * skill, 10 - 6 * skill))
        lag = round(abs(r.gauss(8, 7 - 4 * skill)))
        on, off = a - lead, b + lag
        if toggles and on <= toggles[-1]:
            toggles.pop()  # still holding from the last pass
        else:
            toggles.append(max(0, on))
        toggles.append(min(p["duration"] - 1, max(off, toggles[-1] + 1)))
    return _fix(toggles, p["duration"])


def _fix(toggles: list[int], duration: int) -> list[int]:
    out = []
    for x in toggles:
        x = max(0, min(duration - 1, x))
        if out and x <= out[-1]:
            x = out[-1] + 1
        if x < duration:
            out.append(x)
    return out


def bot_fight(p: dict, skill: float, r: random.Random) -> list[int]:
    fooled = p["feint"] >= 0 and r.random() < 0.15 + 0.45 * (1 - skill)
    side = p["side"] if r.random() < 0.82 + 0.15 * skill else 1 - p["side"]
    if fooled:
        side = p["first"]
    lo, hi = p["block"]
    bt = round((lo + hi) / 2 + r.gauss(0, 13 - 7 * skill))
    st = round(p["open"][0] + abs(r.gauss(14 + 8 * (1 - skill), 9 - 4 * skill)))
    return [bt, side, st]

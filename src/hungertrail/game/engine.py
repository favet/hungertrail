"""The Hunger Trail rules engine.

Pure and deterministic: no I/O, no clocks. A run is fully defined by (seed, salt, setup, choices),
so the server can rebuild any run by replaying its choices.

- `seed` shapes the arena (which cards, in what order, who dies when). Same for everyone that day.
- `salt` rolls the dice on close calls. Different per run, so a posted "perfect path" can't be copied.
"""

from __future__ import annotations

import hashlib
import random
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from . import lore

START_TRIBUTES = 24
MAX_DAYS = 8
HEAT_TRIGGER = 60
DAMAGE = 1.7  # global lethality dial for event damage, tuned with the bots
HUNT = "hunt"


@cache
def cards() -> dict[str, dict]:
    data = yaml.safe_load((Path(__file__).parent / "cards.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data}


def _rng(*parts: Any) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def health_word(h: int) -> str:
    if h >= 70:
        return "Good"
    if h >= 45:
        return "Fair"
    if h >= 20:
        return "Poor"
    return "Very poor"


class Arena:
    """Everything decided by the daily seed alone."""

    def __init__(self, seed: int, district: int):
        r = _rng(seed, "arena")
        self.seed = seed
        self.launch = r.choice(["launch_meadow", "launch_lake", "launch_pines"])
        events = sorted(k for k, c in cards().items() if c["kind"] == "event")
        r.shuffle(events)
        self.deck = events
        gms = sorted(k for k, c in cards().items() if c["kind"] == "gamemaker")
        r.shuffle(gms)
        self.gamemakers = gms
        self.feast_day = r.choice([5, 6])
        self.weather = [r.choice(["Clear", "Warm", "Cool", "Cold", "Rainy", "Foggy"]) for _ in range(MAX_DAYS + 2)]

        # The other 22 tributes plus your district partner, in the order they'll fall.
        others = [
            f"the District {d} {g}"
            for d in range(1, 13)
            for g in ("boy", "girl")
            if d != district
        ]
        r.shuffle(others)
        self.death_order = others + ["partner"]
        r.shuffle(self.death_order)

        # Deaths per day, not counting your kills. Day 1 is the bloodbath.
        sched = [0] * (MAX_DAYS + 2)
        sched[1] = r.randint(8, 11)
        left = (START_TRIBUTES - 1 - sched[1]) - 2
        for _ in range(left):
            sched[r.randint(2, MAX_DAYS - 1)] += 1
        sched[self.feast_day] += 1
        self.schedule = sched

    def district_for(self, card_id: str) -> int:
        return _rng(self.seed, card_id).randint(1, 12)


class Game:
    def __init__(self, seed: int, salt: int, setup: dict):
        self.setup = setup
        self.district: int = setup["district"]
        self.trait: str = setup["trait"]
        self.partner: str = setup["partner"]
        self.arena = Arena(seed, self.district)
        self.salt = salt
        self.schedule = list(self.arena.schedule)

        self.day = 1
        self.health = 100
        self.food = 1
        self.water = 2
        self.items: list[str] = []
        self.favor = 10
        self.heat = 0
        self.kills = 0
        self.pool = list(self.arena.death_order)  # alive, not with you
        self.allies: list[str] = []  # names travelling with you
        self.ally_names: list[str] = list(setup.get("allies", []))
        self.fallen_today: list[str] = []
        self.announced = 0  # cannons already heard today
        self.used: set[str] = set()
        self.log: list[dict] = []
        self.days: list[list[str]] = [[] for _ in range(MAX_DAYS + 2)]
        self.bold_today = False
        self.over = False
        self.won = False
        self.cause: str | None = None
        self.placement: int | None = None
        self.steps = 0
        self.card: str = ""

        # Allies are real tributes from the roster: they leave the pool while they travel with you.
        r = _rng(seed, "allies", self.district)
        for name in self.ally_names:
            candidates = [t for t in self.pool if t != "partner"]
            slot = r.choice(candidates)
            self.pool.remove(slot)
        self.reserved_allies = len(self.ally_names)

        self._say(
            f"District {self.district} ({lore.DISTRICTS[self.district]}). "
            f"{START_TRIBUTES} tributes rise into the arena.",
            "system",
        )
        self._draw(self.arena.launch)

    # ------------------------------------------------------------------ public API
    @property
    def remaining(self) -> int:
        return 1 + len(self.pool) + len(self.allies) + self._pending_allies()

    def _pending_allies(self) -> int:
        # Allies named at the reaping who haven't met up yet (they join after the bloodbath).
        return self.reserved_allies

    def options(self) -> list[dict]:
        if self.over:
            return []
        if self.card == "camp":
            return self._camp_options()
        card = cards()[self.card]
        out = []
        for i, o in enumerate(card["options"]):
            if not self._has(o.get("need")):
                continue
            chance = self._chance(o["roll"]) if "roll" in o else None
            out.append(
                {
                    "index": i,
                    "label": self._fmt(o["label"]),
                    "hint": self._fmt(o.get("hint", "")),
                    "chance": chance,
                    "tag": o.get("tag", "safe"),
                }
            )
        return out

    def choose(self, index: int, param: int | None = None) -> None:
        if self.over:
            raise ValueError("run is over")
        valid = {o["index"]: o for o in self.options()}
        if index not in valid:
            raise ValueError("invalid option")
        self.steps += 1
        if valid[index]["tag"] == "bold":
            self.bold_today = True
        if self.card == "camp":
            self._resolve_camp(index, param)
            if not self.over:
                self._night()
            return
        card = cards()[self.card]
        opt = card["options"][index]
        self._say(self._fmt(opt["label"]), "choice")
        if "roll" in opt:
            ok = self._roll(self._chance(opt["roll"]))
            branch = opt["success" if ok else "fail"]
            text, effects = branch.get("text", ""), branch.get("effects", {})
        else:
            text, effects = opt.get("text", ""), opt.get("effects", {})
        if text:
            self._say(self._fmt(text), "result")
        nxt = self._apply(effects, card)
        if self.over:
            return
        if nxt:
            self._draw(nxt)
        elif card["kind"] in ("launch", "chain") and self.day == 1:
            self._end_bloodbath()
        else:
            self._draw("camp")

    # ------------------------------------------------------------------ flow
    def _draw(self, card_id: str) -> None:
        self.card = card_id
        if card_id == "camp":
            self._say("Night falls. What do you do before you sleep?", "prompt")
            return
        card = cards()[card_id]
        self.used.add(card_id)
        self.days[self.day].append(card["emoji"])
        self._say(self._fmt(card["text"]), "event")

    def _end_bloodbath(self) -> None:
        deaths = self._scheduled_deaths(1)
        n = len(self.fallen_today)
        self.announced = n
        self._say(
            f"The bloodbath ends. The cannons fire all at once. You count {n}.", "cannon"
        )
        if self.ally_names:
            names = " and ".join(self.ally_names)
            self.allies = list(self.ally_names)
            self.reserved_allies = 0
            self._say(f"At dusk, {names} find{'s' if len(self.ally_names) == 1 else ''} you. The alliance holds. For now.", "story")
        self._draw("camp")

    def _morning(self) -> None:
        self.bold_today = False
        self.fallen_today = []
        self.announced = 0
        self._say(f"Day {self.day}. {self.arena.weather[self.day]}. {self.remaining} tributes remain.", "day")
        if self.remaining <= 2:
            return self._draw("finale_duel")
        if self.day >= MAX_DAYS:
            return self._draw("finale_melee")
        if self.heat >= HEAT_TRIGGER:
            gm = next((g for g in self.arena.gamemakers if g not in self.used), self.arena.gamemakers[0])
            self.heat = max(0, self.heat - 50)
            return self._draw(gm)
        if self.day == self.arena.feast_day and "feast" not in self.used:
            return self._draw("feast")
        if "betrayal" not in self.used and self._eligible(cards()["betrayal"]):
            return self._draw("betrayal")
        for cid in self.arena.deck:
            if cid not in self.used and self._eligible(cards()[cid]):
                return self._draw(cid)
        return self._draw("cameras")

    def _eligible(self, card: dict) -> bool:
        lo, hi = card.get("days", [1, MAX_DAYS + 1])
        if not lo <= self.day <= hi:
            return False
        req = card.get("requires", {})
        if req.get("ally") and not self.allies:
            return False
        if req.get("partner") and "partner" not in self.pool:
            return False
        if "min_remaining" in req and self.remaining < req["min_remaining"]:
            return False
        if "max_remaining" in req and self.remaining > req["max_remaining"]:
            return False
        if "max_health" in req and self.health > req["max_health"]:
            return False
        return True

    def _night(self) -> None:
        # Eat and drink.
        fed = watered = True
        if self.food > 0:
            self.food -= 1
        else:
            fed = False
            self._say("You go to sleep hungry.", "status")
            self._hurt(15, "starvation")
        if self.water > 0:
            self.water -= 1
        else:
            watered = False
            if not self.over:
                self._say("Your lips are cracked. You need water.", "status")
                self._hurt(20, "dehydration")
        if self.over:
            return
        if fed and watered:
            self.health = min(100, self.health + 5)

        # Boredom: the Gamemakers punish tributes who play it safe.
        self.heat = min(100, max(0, self.heat + (-5 if self.bold_today else 10)))

        # Cannons and the sky.
        deaths = self._scheduled_deaths(self.day)
        n = len(self.fallen_today) - self.announced
        if n:
            self._say(f"{'A cannon fires' if n == 1 else f'{n} cannons fire'} in the distance today.", "cannon")
        if self.fallen_today:
            faces = ", ".join(self._name(t) for t in self.fallen_today)
            self._say(f"The anthem plays. Faces in the sky: {faces}.", "sky")
        else:
            self._say("The anthem plays. No faces in the sky tonight.", "sky")
        del deaths

        # Sponsors.
        cost = 30 + 5 * self.day
        if self.favor >= cost:
            self.favor -= cost
            self._gift()

        self.day += 1
        self._morning()

    def _scheduled_deaths(self, day: int) -> list[str]:
        n = self.schedule[day]
        self.schedule[day] = 0
        # Never leave you alone in the arena by accident: keep at least one rival alive.
        keep = 0 if self.allies or self.reserved_allies else 1
        n = max(0, min(n, len(self.pool) - keep))
        fallen = self.pool[:n]
        self.pool = self.pool[n:]
        self.fallen_today.extend(fallen)
        return fallen

    def _gift(self) -> None:
        if self.water == 0:
            self.water += 3
            what = "a canteen of water"
        elif self.health < 45:
            self.items.append("ointment")
            what = "burn ointment"
        elif self.food == 0:
            self.food += 3
            what = "a loaf of bread"
        elif not any(w in self.items for w in lore.WEAPONS):
            self.items.append("knife")
            what = "a knife"
        else:
            self.food += 2
            self.water += 1
            what = "a hot meal"
        self.days[self.day].append("🎁")
        self._say(f"A silver parachute drifts down. A sponsor sent {what}.", "gift")

    # ------------------------------------------------------------------ camp
    def _camp_options(self) -> list[dict]:
        o = [{"index": 0, "label": "Rest", "hint": "Recover health. The Gamemakers hate a quiet tribute.", "chance": None, "tag": "hide"}]
        o.append({"index": 1, "label": "Forage", "hint": "Look for food and water nearby.", "chance": self._chance({"base": 60, "mods": ["plants"]}), "tag": "safe"})
        if any(w in self.items for w in ("bow", "spear", "knife", "axe", "trident")):
            o.append({"index": 2, "label": "Hunt", "hint": "Food if you're a good shot. Noise if you're not.", "chance": None, "tag": "bold", "minigame": HUNT})
        if self.trait == "snares" or "rope" in self.items:
            o.append({"index": 3, "label": "Set snares", "hint": "Food by morning, quietly.", "chance": self._snare_chance(), "tag": "safe"})
        o.append({"index": 4, "label": "Play to the cameras", "hint": "Win sponsors. Being seen has risks.", "chance": self._chance({"base": 85, "mods": ["stealth"]}), "tag": "bold"})
        if "ointment" in self.items and self.health < 100:
            o.append({"index": 5, "label": "Tend your wounds", "hint": "Use the ointment.", "chance": None, "tag": "safe"})
        return o

    def _snare_chance(self) -> int:
        return 95 if self.trait == "snares" else 70

    def _resolve_camp(self, index: int, param: int | None) -> None:
        label = next(o["label"] for o in self._camp_options() if o["index"] == index)
        self._say(label, "choice")
        if index == 0:
            gain = 10 + (10 if "sleeping_bag" in self.items else 0)
            self.health = min(100, self.health + gain)
            self.heat = min(100, self.heat + 10)
            self.days[self.day].append("💤")
            self._say(self._pick(
                "You sleep, and wake a little stronger.",
                "You sleep like the dead. Not literally. Not yet.",
                "You curl up under a fallen log and sleep until the birds wake you.",
            ), "result")
        elif index == 1:
            if self._roll(self._chance({"base": 60, "mods": ["plants"]})):
                self.food += 1
                self.water += 2
                self._say(self._pick(
                    "Roots, a trickle of spring water. Enough.",
                    "Wild onions and a seep of clean water between the rocks.",
                    "Pine nuts, and rainwater pooled in a hollow stump.",
                    "Cattail roots and a cold creek. Not bad.",
                ), "result")
            else:
                self._say(self._pick(
                    "You find nothing but blisters.",
                    "Everything edible here was eaten days ago.",
                    "Dark comes before you find anything.",
                ), "result")
        elif index == 2:
            hits = max(0, min(3, int(param or 0)))
            self.days[self.day].append("🏹")
            if hits:
                self.food += min(hits, 2)
                self._say(f"{hits} hit{'s' if hits > 1 else ''}. You eat tonight." + (" The Capitol loves a marksman." if hits == 3 else ""), "result")
                if hits == 3:
                    self._favor(10)
            else:
                self._say("You miss everything. The noise carries.", "result")
                if not self._roll(70):
                    self._say("Someone heard.", "result")
                    self._hurt(20, "a tribute who heard you hunting")
        elif index == 3:
            if self._roll(self._snare_chance()):
                self.food += 2 if self.trait == "snares" else 1
                self._say("Something is caught by morning.", "result")
            else:
                self._say("The snares are empty.", "result")
        elif index == 4:
            self._favor(15)
            self.heat = max(0, self.heat - 15)
            self.days[self.day].append("🎥")
            if self._roll(self._chance({"base": 85, "mods": ["stealth"]})):
                self._say(self._pick(
                    "You wave at the sky. Somewhere, a sponsor opens their wallet.",
                    "You tell the cameras about home. The Capitol eats it up.",
                    "You sing. Badly. They love it anyway.",
                    "You carve your district number into a tree, slowly, for the cameras.",
                ), "result")
            else:
                self._say("You weren't the only one watching.", "result")
                self._hurt(20, "a tribute who saw you showing off")
        elif index == 5:
            self.items.remove("ointment")
            self.health = min(100, self.health + 40)
            self._say("The ointment works like magic. Capitol magic.", "result")

    # ------------------------------------------------------------------ effects
    def _apply(self, e: dict, card: dict) -> str | None:
        cause = self._fmt(e.get("cause", card.get("cause", "the arena")))
        for stat in ("food", "water"):
            if stat in e:
                setattr(self, stat, max(0, getattr(self, stat) + e[stat]))
        if "favor" in e:
            self._favor(e["favor"])
        if "heat" in e:
            self.heat = min(100, max(0, self.heat + e["heat"]))
        for key in ("add", "remove"):
            vals = e.get(key, [])
            for item in [vals] if isinstance(vals, str) else vals:
                if key == "add":
                    self.items.append(item)
                elif item in self.items:
                    self.items.remove(item)
        if e.get("gift"):
            pool = [i for i in ("knife", "spear", "rope", "ointment", "iodine", "axe") if i not in self.items]
            if pool:
                item = self._dice().choice(pool)
                self.items.append(item)
                self._say(f"You found {lore.ITEMS[item]}.", "status")
        if e.get("kill"):
            self._kill(e["kill"])
        if e.get("partner_dies") and "partner" in self.pool:
            self.pool.remove("partner")
            self.fallen_today.append("partner")
        ally = e.get("ally")
        if ally and self.allies:
            name = self.allies.pop(0)
            if ally == "dies":
                self.fallen_today.append(name)
            elif ally == "lost":
                self.pool.append(name)
        if ally == "joins_partner" and "partner" in self.pool:
            self.pool.remove("partner")
            self.allies.append(self.partner)
        if "health" in e:
            if e["health"] < 0:
                self._hurt(round(-e["health"] * DAMAGE), cause)
            else:
                self.health = min(100, self.health + e["health"])
        if e.get("die"):
            self._die(self._fmt(e["die"]))
        if e.get("win") or (not self.over and self.remaining == 1):
            self._win()
        return None if self.over else e.get("next")

    def _kill(self, n: int) -> None:
        for _ in range(n):
            victims = [t for t in self.pool if t != "partner"] or self.pool
            if not victims:
                return
            v = victims[0]
            self.pool.remove(v)
            self.fallen_today.append(v)
            self.kills += 1
            # Your kills come out of the day's scheduled deaths.
            for d in range(self.day, MAX_DAYS + 1):
                if self.schedule[d] > 0:
                    self.schedule[d] -= 1
                    break
            self._favor(10)
        if "⚔️" not in self.days[self.day]:
            self.days[self.day].append("⚔️")

    def _favor(self, n: int) -> None:
        if n > 0 and self.trait == "charm":
            n = int(n * 1.5)
        self.favor = max(0, min(200, self.favor + n))

    def _hurt(self, n: int, cause: str) -> None:
        self.health -= n
        if self.health <= 0:
            self._die(cause)

    def _die(self, cause: str) -> None:
        if self.over:
            return
        self.health = 0
        self.over = True
        self.cause = cause
        self.placement = self.remaining
        self.days[self.day].append("☠️")
        self._say(f"You have died. Cause of death: {cause}.", "death")
        self.card = ""

    def _win(self) -> None:
        self.over = True
        self.won = True
        self.placement = 1
        self.days[self.day].append("👑")
        self._say("Ladies and gentlemen, the victor of the Hunger Games!", "victory")
        self.card = ""

    # ------------------------------------------------------------------ odds
    def _chance(self, roll: dict) -> int:
        c = roll["base"]
        h = self.health
        penalty = 0 if h >= 70 else -5 if h >= 45 else -12 if h >= 20 else -20
        for m in roll.get("mods", []):
            if m == "combat":
                c += max([lore.WEAPONS.get(i, 0) for i in self.items] + [0])
                c += 15 if self.trait == "fighter" else 0
                c += 10 if self.district in lore.CAREERS else 0
                c += 5 * len([a for a in self.allies if a != self._opponent()])
                c += penalty
            elif m == "escape":
                c += 15 if self.trait == "climber" else 0
                c += penalty
            elif m == "stealth":
                c -= 5 * len(self.allies)
                c += penalty
            elif m == "plants":
                c += 35 if self.trait == "plants" else 0
            elif m == "swim":
                c += 30 if self.trait == "swimmer" else 0
                c += 15 if self.district == 4 else 0
        return max(5, min(95, c))

    def _dice(self) -> random.Random:
        return _rng(self.salt, self.steps, len(self.log))

    def _roll(self, chance: int) -> bool:
        return self._dice().randint(1, 100) <= chance

    def _has(self, need: dict | None) -> bool:
        if not need:
            return True
        if "item" in need and need["item"] not in self.items:
            return False
        if "any" in need and not any(i in self.items for i in need["any"]):
            return False
        if "trait" in need and self.trait != need["trait"]:
            return False
        if need.get("ally") and not self.allies:
            return False
        if "water" in need and self.water < need["water"]:
            return False
        if "food" in need and self.food < need["food"]:
            return False
        return True

    # ------------------------------------------------------------------ text
    def _name(self, t: str) -> str:
        return f"{self.partner}, your district partner" if t == "partner" else t

    def _pick(self, *lines: str) -> str:
        return self._dice().choice(lines)

    def _opponent(self) -> str:
        if self.allies:
            return self.allies[0]
        if self.pool:
            return self._name(self.pool[-1])
        return "the last Career"

    def _fmt(self, s: str) -> str:
        if "{" not in s:
            return s
        return s.format(
            ally=self.allies[0] if self.allies else "your ally",
            partner=self.partner,
            n=self.arena.district_for(self.card),
            remaining=self.remaining,
            host=lore.HOST,
            opponent=self._opponent(),
            district=self.district,
        )

    def _say(self, text: str, kind: str) -> None:
        self.log.append({"day": self.day, "kind": kind, "text": text})

    # ------------------------------------------------------------------ views
    def status(self) -> dict:
        return {
            "day": self.day,
            "weather": self.arena.weather[min(self.day, MAX_DAYS)],
            "health": self.health,
            "health_word": health_word(self.health),
            "food": self.food,
            "water": self.water,
            "items": [lore.ITEMS[i] for i in self.items],
            "favor": self.favor,
            "heat": self.heat,
            "kills": self.kills,
            "remaining": self.remaining,
            "allies": list(self.allies),
        }

    def emoji_row(self) -> str:
        priority = ["👑", "☠️", "⚔️", "🎁"]
        row = []
        for d in range(1, min(self.day, MAX_DAYS) + 1):
            marks = self.days[d]
            if not marks:
                continue
            pick = next((p for p in priority if p in marks), marks[0])
            row.append(pick)
        return "".join(row)

    def score(self) -> int:
        if not self.over:
            return 0
        place = self.placement or START_TRIBUTES
        pts = (START_TRIBUTES + 1 - place) * 40 + self.kills * 60 + self.day * 25
        if self.won:
            pts += 500
        return pts * lore.multiplier(self.district)


def new_setup(district: int, trait: str, partner: str, allies: list[str]) -> dict:
    if district not in lore.DISTRICTS:
        raise ValueError("bad district")
    if trait not in lore.TRAITS:
        raise ValueError("bad trait")
    return {"district": district, "trait": trait, "partner": partner, "allies": allies[:2]}


def replay(seed: int, salt: int, setup: dict, choices: list[list[int | None]]) -> Game:
    g = Game(seed, salt, setup)
    for idx, param in choices:
        g.choose(idx, param)
    return g

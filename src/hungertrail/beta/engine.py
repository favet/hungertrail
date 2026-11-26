"""Hunger Trail BETA rules engine (beta 2).

Same contract as the live engine: pure and deterministic, a run is (seed, salt, setup, choices).

- Three stats on screen: health, supplies (food and water together, in days) and a 3-slot kit.
- Day 1 is the bloodbath draft: seeded loot around the Cornucopia, closer is better and deadlier.
- Every other day you choose where to go: 3 of 5 places, picked by the daily seed. Each place
  does one thing for you (water, rest, food, loot, sponsors), then deals an event from its own
  stack. Night runs itself.
- Nothing is hidden. Every option states its stakes as chips computed from the same effects the
  engine applies. Sponsors pay only through options that say so.
- The Gamemakers come for players who stay at the edge (two days running, or any day from day
  5), and the edge closes on day 6. Caesar says so out loud the night before.
- A failed roll within NEAR points is a near miss: the damage is halved.
- One named Career, the nemesis, is the same for everyone today: their kills show in the sky,
  they cross your path, and if they're alive at the end they're your final opponent.
- The final duel is best of three rounds; your edges (kit, health, kills) set each round's odds.
- Rolls the player chose are logged as "roll" entries so the screen can show what happened.
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
DAMAGE = 2.6  # global lethality dial for card damage, tuned with the bots
NEAR = 10  # a failed roll this close to the target is a near miss
DUEL_BASE = 34  # odds of winning a duel round before your edges
DUEL_HIT = {"press": 25, "hold": 12}  # what a lost round costs
OPEN_GIFT = 55  # odds a sponsor pays when you go out in the open
HORN_LOOT = 60  # odds of picking something up when you head for the horn
HIDE_HEAL = 25
EDGE_WATCHED = 5  # from this day, any day at the edge brings the Gamemakers
EDGE_CLOSES = 6  # from this day the edge places are gone
USE = 99  # choice index for tapping the medkit in your kit
HUNT = "hunt"
DRAFT, PLACES, SWAP, DUEL = "draft", "places", "swap", "duel"

# Cornucopia rings: base odds, damage on a miss, how it reads.
RINGS = {
    "edge": (85, 15, "At the edge"),
    "mid": (65, 30, "Halfway in"),
    "horn": (45, 45, "At the mouth of the horn"),
}

# Places, in a fixed order (the order is the choice index): emoji, label, hint, tag.
PLACE_KEYS = ("stream", "rocks", "ridge", "horn", "open", "feast")
PLACES_INFO = {
    "stream": ("🌊", "Go to the stream", "Water and food. Everyone else needs it too.", "safe"),
    "rocks": ("🪨", "Hide in the rocks", "Rest and heal. The edge of the arena, where it's quiet.", "hide"),
    "ridge": ("⛰️", "Climb the ridge", "Food up high, if you can get it. The edge of the arena.", "safe"),
    "horn": ("🔺", "Head for the horn", "Loot and kills. The Careers camp there.", "bold"),
    "open": ("🎥", "Go out in the open", "The cameras find you. So might everyone else.", "bold"),
    "feast": ("🍽️", "Go to the feast", "A pack with your district's number on it. Everyone left alive will be there.", "bold"),
}
EDGE = ("rocks", "ridge")
EPITHETS = ("the Butcher", "the Wolf", "Smiler", "the Hound", "the Knife")


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


def _sign(n: int) -> str:
    return f"+{n}" if n >= 0 else f"−{-n}"


class Arena:
    """Everything decided by the daily seed alone: the same for every player that day."""

    def __init__(self, seed: int, district: int):
        r = _rng(seed, "arena")
        self.seed = seed
        events = sorted(k for k, c in cards().items() if c["kind"] == "event")
        r.shuffle(events)
        self.deck = events
        gms = sorted(k for k, c in cards().items() if c["kind"] == "gamemaker")
        r.shuffle(gms)
        self.gamemakers = gms
        self.feast_day = r.choice([4, 5])

        # The loot around the horn. Same layout for everyone today: the bloodbath talking point.
        h = _rng(seed, "horn")
        edge = h.sample(["pack", "knife", "rope", "medkit"], 2)
        mid = h.sample([i for i in ("spear", "axe", "sleeping_bag", "iodine", "rope", "medkit") if i not in edge], 2)
        top = h.choice(["bow", "trident"])
        second = h.choice([i for i in ("crate", "bow", "trident", "axe") if i != top and i not in mid])
        self.layout = [(i, "edge") for i in edge] + [(i, "mid") for i in mid] + [(top, "horn"), (second, "horn")]

        # The nemesis: a Career from the seed. Same for everyone, except a player from that district.
        n = _rng(seed, "nemesis")
        careers = [f"the District {d} {g}" for d in (1, 2, 4) for g in ("boy", "girl")]
        self.nemesis = n.choice([c for c in careers if not c.startswith(f"the District {district} ")])
        self.epithet = n.choice(EPITHETS)

        others = [f"the District {d} {g}" for d in range(1, 13) for g in ("boy", "girl") if d != district]
        others.remove(self.nemesis)
        r.shuffle(others)
        self.death_order = others + ["partner"]
        r.shuffle(self.death_order)

        sched = [0] * (MAX_DAYS + 2)
        sched[1] = r.randint(8, 11)
        left = (START_TRIBUTES - 1 - sched[1]) - 2
        for _ in range(left):
            sched[r.randint(2, MAX_DAYS - 1)] += 1
        self.schedule = sched

        # Where you can go each day: 3 of the 5 places until the edge closes.
        self.offers: dict[int, list[str]] = {}
        for day in range(2, MAX_DAYS + 1):
            if day >= EDGE_CLOSES:
                offer = ["stream", "horn", "open"]
            else:
                offer = _rng(seed, "places", day).sample(PLACE_KEYS[:5], 3)
            if day == self.feast_day:
                offer = [p for p in offer if p != "horn"][:2] + ["feast"]
            self.offers[day] = sorted(offer, key=PLACE_KEYS.index)


class Game:
    def __init__(self, seed: int, salt: int, setup: dict):
        self.setup = setup
        self.district: int = setup["district"]
        self.partner: str = setup["partner"]
        self.arena = Arena(seed, self.district)
        self.seed = seed
        self.salt = salt
        self.schedule = list(self.arena.schedule)
        self.nemesis = self.arena.nemesis
        self.nemesis_kills = 2  # the bloodbath

        self.day = 1
        self.health = 100
        self.supplies = 2 + (2 if self.district == 9 else 0)
        self.kit: list[str] = []
        self.gm_due = False
        self.edge_streak = 0
        self.place: str | None = None
        self.kills = 0
        self.pool = list(self.arena.death_order) + [self.nemesis]
        self.allies: list[str] = []
        self.ally_names: list[str] = list(setup.get("allies", []))
        self.fallen_today: list[str] = []
        self.announced = 0
        self.used: set[str] = set()
        self.log: list[dict] = []
        self.days: list[list[str]] = [[] for _ in range(MAX_DAYS + 2)]
        self.over = False
        self.won = False
        self.cause: str | None = None
        self.recap: str | None = None
        self.placement: int | None = None
        self.steps = 0
        self.card = ""
        self.target: str | None = None
        self.opponent: str | None = None
        self.opponent_id: str | None = None
        self.duel_wins = 0
        self.duel_losses = 0
        self.grabs = 0
        self.taken: set[int] = set()
        self.pending_item: str | None = None
        self.resume: tuple = ()
        self.new_kills: list[str] = []
        self.last: tuple = ("", None, 100)
        self.kills_today = 0

        r = _rng(seed, "allies", self.district)
        for _ in self.ally_names:
            self.pool.remove(r.choice([t for t in self.pool if t not in ("partner", self.nemesis)]))
        self.reserved_allies = len(self.ally_names)

        self._say(f"District {self.district} ({lore.DISTRICTS[self.district]}). {START_TRIBUTES} tributes rise into the arena.", "system")
        self.card = DRAFT
        self._say(
            "Sixty seconds on the plate. Then the gong. Supplies lie scattered around the Cornucopia: "
            "the closer to the horn, the better they are, and the more people will die for them.",
            "event",
        )

    # ------------------------------------------------------------------ public API
    @property
    def remaining(self) -> int:
        return 1 + len(self.pool) + len(self.allies) + self.reserved_allies

    @property
    def nemesis_name(self) -> str:
        return self.arena.epithet

    def options(self) -> list[dict]:
        if self.over:
            return []
        if self.card == DRAFT:
            return self._draft_options()
        if self.card == PLACES:
            return self._place_options()
        if self.card == SWAP:
            return self._swap_options()
        if self.card == DUEL:
            return self._duel_options()
        card = cards()[self.card]
        out = []
        for i, o in enumerate(card["options"]):
            if not self._has(o.get("need")):
                continue
            if "roll" in o:
                win = self._chips(o["success"].get("effects", {}))
                lose = self._chips(o["fail"].get("effects", {}), card)
                out.append(self._opt(i, o["label"], o.get("hint", ""), self._chance(o["roll"]), o.get("tag", "safe"), win, lose))
            else:
                e = o.get("effects", {})
                out.append(self._opt(i, o["label"], o.get("hint", ""), None, o.get("tag", "safe"), self._chips(e, card), []))
        return out

    def can_use_medkit(self) -> bool:
        return not self.over and "medkit" in self.kit and self.health < 100 and self.card != SWAP

    def choose(self, index: int, param: int | None = None) -> None:
        if self.over:
            raise ValueError("run is over")
        if index == USE:
            return self._use_medkit()
        valid = {o["index"]: o for o in self.options()}
        if index not in valid:
            raise ValueError("invalid option")
        self.steps += 1
        o = valid[index]
        self.last = (o["label"], o["chance"], self.health)
        snap = self._snap()
        self._say(o["label"], "choice")
        if self.card == DRAFT:
            dest = self._resolve_draft(index)
        elif self.card == PLACES:
            dest = self._resolve_place(index, param)
        elif self.card == SWAP:
            dest = self._resolve_swap(index)
        elif self.card == DUEL:
            dest = ("round", self._resolve_duel(index))
        else:
            dest = self._resolve_card(index)
        self._delta(snap)
        if dest and dest[0] == "round":
            if dest[1]:
                self._say(dest[1], "prompt")  # the next round, after this round's result
            return
        self._advance(dest)

    def _use_medkit(self) -> None:
        if not self.can_use_medkit():
            raise ValueError("can't use the medkit now")
        self.steps += 1
        snap = self._snap()
        self._say("Use the medkit", "choice")
        self.kit.remove("medkit")
        self.health = min(100, self.health + 40)
        self._say("You clean and bind your wounds. It stings like nothing else.", "result")
        self._delta(snap)

    # ------------------------------------------------------------------ option helpers
    def _opt(self, index, label, hint, chance, tag, win, lose, **extra) -> dict:
        deadly = "☠️" in lose
        return {
            "index": index, "label": self._fmt(label), "hint": self._fmt(hint), "chance": chance,
            "tag": tag, "win": win, "lose": lose, "deadly": deadly, **extra,
        }

    def _chips(self, e: dict, card: dict | None = None) -> list[str]:
        out = []
        if "health" in e:
            n = e["health"] if e["health"] > 0 else -round(-e["health"] * DAMAGE)
            out.append(f"❤️{_sign(n)}")
        if e.get("supplies"):
            out.append(f"🎒{_sign(e['supplies'])}")
        for item in self._list(e.get("add")):
            out.append(f"{lore.item_emoji(item)} {lore.item_name(item)}")
        for item in self._list(e.get("remove")):
            if item in self.kit:
                out.append(f"−{lore.item_emoji(item)}")
        if e.get("sponsor"):
            out.append("🎁 sponsor")
        if e.get("gift"):
            out.append("🎁 a find")
        if e.get("kill"):
            out.append("⚔️ kill")
        if e.get("ally") in ("dies", "lost") and self.allies:
            out.append(f"−{self.allies[0]}")
        if e.get("ally") == "joins_partner":
            out.append(f"+{self.partner}")
        if e.get("die") or ("health" in e and e["health"] < 0 and round(-e["health"] * DAMAGE) >= self.health):
            out.append("☠️")
        return out

    @staticmethod
    def _list(v) -> list:
        return [] if not v else [v] if isinstance(v, str) else list(v)

    # ------------------------------------------------------------------ the bloodbath draft
    def _draft_options(self) -> list[dict]:
        out = []
        for i, (item, ring) in enumerate(self.arena.layout):
            if i in self.taken:
                continue
            base, dmg, where = RINGS[ring]
            chance = self._chance({"base": base - 15 * self.grabs, "mods": ["escape", "grab"]})
            desc = f"{lore.PACKS[item][2]} days of supplies." if item in lore.PACKS else lore.ITEMS[item][2]
            lose = [f"❤️−{dmg}"] + (["☠️"] if dmg >= self.health else [])
            out.append(self._opt(i, f"Grab the {lore.item_name(item)}", f"{where}. {desc}", chance,
                                 "bold" if ring != "edge" else "safe",
                                 [f"{lore.item_emoji(item)} {lore.item_name(item)}"], lose))
        label = "Run for the trees" if not self.grabs else "Run while you can"
        hint = "You'll arrive with nothing but your life." if not self.grabs else "Take what you have and go."
        out.append(self._opt(len(self.arena.layout), label, hint, None, "hide", [], []))
        return out

    def _resolve_draft(self, index: int) -> tuple:
        if index == len(self.arena.layout):
            self._say(self._pick(
                "You sprint into the trees and don't look back until the screaming stops.",
                "You run. Behind you, the cannons will wait until it's over.",
            ) if not self.grabs else "You run with what you've got.", "result")
            if not self.grabs:
                self.days[1].append("🌲")
            return ("bloodbath",)
        item, ring = self.arena.layout[index]
        base, dmg, _ = RINGS[ring]
        chance = self._chance({"base": base - 15 * self.grabs, "mods": ["escape", "grab"]})
        self.taken.add(index)
        self.grabs += 1
        roll, ok, near = self._reveal(chance)
        name = lore.item_name(item)
        if ok:
            self._say(self._pick(f"You snatch the {name} and keep moving.", f"The {name} is yours. Nobody touched you."), "result")
            self._take(item)
            return ("draft",) if self.grabs < 2 else ("bloodbath",)
        if near:
            self._say(f"Close call. You get the {name}, and a blade across the arm for it. You run.", "result")
            self._take(item)
            self._hurt(dmg // 2, "the bloodbath")
        else:
            self._say(self._pick(
                f"Someone gets to the {name} first. You get a fist, then a knife, and you run.",
                f"You never reach the {name}. You crawl away bleeding.",
            ), "result")
            self._hurt(dmg, "the bloodbath")
        return ("bloodbath",)

    def _take(self, item: str) -> None:
        if item in lore.PACKS:
            self.supplies += lore.PACKS[item][2]
        else:
            self._gain(item)
        self.days[1].append(lore.item_emoji(item))

    def _end_bloodbath(self) -> None:
        self._scheduled_deaths(1)
        n = len(self.fallen_today)
        self.announced = n
        self._say(f"The bloodbath ends. The cannons fire all at once. You count {n}.", "cannon")
        self._say(f"{lore.HOST}: \"Two of those were {self.nemesis}. The Capitol already has a name for that one: "
                  f"{self.nemesis_name}.\"", "caesar")
        if self.ally_names:
            names = " and ".join(self.ally_names)
            self.allies = list(self.ally_names)
            self.reserved_allies = 0
            self._say(f"At dusk, {names} find{'s' if len(self.ally_names) == 1 else ''} you. The alliance holds. For now.", "story")
        self._night()

    # ------------------------------------------------------------------ kit
    def _gain(self, item: str) -> None:
        if len(self.kit) < lore.KIT_SLOTS:
            self.kit.append(item)
        else:
            self.pending_item = item

    def _swap_options(self) -> list[dict]:
        new = self.pending_item
        out = [
            self._opt(i, f"Drop the {lore.item_name(k)}", lore.ITEMS[k][2], None, "safe",
                      [f"{lore.item_emoji(new)} {lore.item_name(new)}"], [])
            for i, k in enumerate(self.kit)
        ]
        out.append(self._opt(lore.KIT_SLOTS, f"Leave the {lore.item_name(new)}", lore.ITEMS[new][2], None, "safe", [], []))
        return out

    def _resolve_swap(self, index: int) -> tuple:
        new = self.pending_item
        self.pending_item = None
        if index < lore.KIT_SLOTS:
            old = self.kit[index]
            self.kit[index] = new
            self._say(f"You leave the {lore.item_name(old)} behind and take the {lore.item_name(new)}.", "result")
        else:
            self._say(f"You leave the {lore.item_name(new)} where it lies.", "result")
        return self.resume

    # ------------------------------------------------------------------ flow
    def _advance(self, dest: tuple) -> None:
        if self.over or not dest:
            return
        if self.pending_item:
            self.resume = dest
            self.card = SWAP
            self._say(f"Your pack is full. Something has to go if you want the {lore.item_name(self.pending_item)}.", "prompt")
            return
        kind = dest[0]
        if kind == "draw":
            self._draw(dest[1])
        elif kind == "night":
            self._night()
        elif kind == "bloodbath":
            self._end_bloodbath()
        elif kind == "draft":
            self.card = DRAFT
            self._say("The Careers are at the horn now. One more grab, or run?", "prompt")
        elif kind == "duel":
            self._start_duel()

    def _draw(self, card_id: str) -> None:
        self.card = card_id
        card = cards()[card_id]
        self.used.add(card_id)
        self.target = self._pick_target(card) if card.get("target") else None
        self.days[self.day].append(card["emoji"])
        self._say(self._fmt(card["text"]), "event")

    def _pick_target(self, card: dict) -> str | None:
        want = card["target"]
        if want == "nemesis":
            return self.nemesis if self.nemesis in self.pool else None
        cands = [t for t in self.pool if t not in ("partner", self.nemesis) and (want == "any" or t.endswith(want))]
        if not cands:
            return None
        return _rng(self.seed, card["id"], self.district).choice(sorted(cands))

    def _resolve_card(self, index: int) -> tuple:
        card = cards()[self.card]
        opt = card["options"][index]
        near = False
        if "roll" in opt:
            _, ok, near = self._reveal(self._chance(opt["roll"]))
            branch = opt["success" if ok else "fail"]
            text, effects = branch.get("text", ""), branch.get("effects", {})
        else:
            text, effects = opt.get("text", ""), opt.get("effects", {})
        if near and effects.get("health", 0) < 0 and not effects.get("die"):
            text = "Close call. " + text
        if text:
            self._say(self._fmt(text), "result")
        nxt = self._apply(effects, card, near)
        if self.over:
            return ()
        if nxt == "duel":
            return ("duel",)
        if nxt:
            return ("draw", nxt)
        return ("night",)

    def _morning(self) -> None:
        self.fallen_today = []
        self.announced = 0
        self.kills_today = 0
        self.place = None
        self._say(f"Day {self.day}. {self.remaining} tributes remain.", "day")
        if self.remaining <= 2:
            return self._start_duel()
        if self.day >= MAX_DAYS:
            return self._draw("flood")
        if self.gm_due:
            self.gm_due = False
            gm = next((g for g in self.arena.gamemakers if g not in self.used), self.arena.gamemakers[0])
            return self._draw(gm)
        if "betrayal" not in self.used and self._eligible(cards()["betrayal"]):
            return self._draw("betrayal")
        self.card = PLACES
        self._say("Where do you go today?", "prompt")

    def _eligible(self, card: dict) -> bool:
        lo, hi = card.get("days", [1, MAX_DAYS + 1])
        if not lo <= self.day <= hi:
            return False
        if card.get("target") and not self._pick_target(card):
            return False
        req = card.get("requires", {})
        if req.get("ally") and not self.allies:
            return False
        if req.get("partner") and "partner" not in self.pool:
            return False
        if req.get("nemesis") and self.nemesis not in self.pool:
            return False
        if "min_remaining" in req and self.remaining < req["min_remaining"]:
            return False
        if "max_remaining" in req and self.remaining > req["max_remaining"]:
            return False
        if "max_health" in req and self.health > req["max_health"]:
            return False
        return True

    def _night(self) -> None:
        snap = self._snap()
        fed = self.supplies > 0
        if fed:
            self.supplies -= 1
        else:
            self._say("You go to sleep with nothing to eat or drink.", "status")
            self._hurt(10 if self.district == 12 else 20, "starvation")
            if self.over:
                return
        if fed:
            self.health = min(100, self.health + 5)
        self._delta(snap, night=True)

        # The Gamemakers watch the edge of the arena.
        at_edge = self.place in EDGE
        self.edge_streak = self.edge_streak + 1 if at_edge else 0
        if at_edge and (self.edge_streak >= 2 or self.day >= EDGE_WATCHED):
            self.gm_due = True
            self.edge_streak = 0
        for line in self._caesar(at_edge):
            self._say(line, "caesar")
        if self.day + 1 == self.arena.feast_day:
            self._say(f"{lore.HOST} announces a feast at the Cornucopia tomorrow at dawn.", "caesar")

        scheduled = self._scheduled_deaths(self.day)
        n = len(self.fallen_today) - self.announced
        if n:
            self._say(f"{'A cannon fires' if n == 1 else f'{n} cannons fire'} in the distance today.", "cannon")
        sky = "The anthem plays. "
        sky += ("Faces in the sky: " + ", ".join(self._name(t) for t in self.fallen_today) + ".") if self.fallen_today else "No faces in the sky tonight."
        if scheduled and self.nemesis in self.pool and self.day > 1:
            self.nemesis_kills += 1 + (len(scheduled) >= 4)
            sky += f" {self.nemesis_name[0].upper()}{self.nemesis_name[1:]} is up to {self.nemesis_kills} kills."
        self._say(sky, "sky")
        self.day += 1
        self._morning()

    def _caesar(self, at_edge: bool) -> list[str]:
        d = f"District {self.district}"
        out = []
        if self.gm_due:
            out.append(f"{lore.HOST}: \"{d} has been hiding at the edge of the arena, and the Gamemakers have noticed. "
                       "Tomorrow should be exciting.\"")
        elif at_edge and self.day + 1 < EDGE_CLOSES:
            out.append(f"{lore.HOST}: \"{d} spent the day at the edge. One more, and the Gamemakers will come looking.\"")
        elif self.kills_today:
            out.append(self._pick(f"{lore.HOST}: \"What a day for {d}! The Capitol can't look away.\"",
                                  f"{lore.HOST}: \"{d} is out for blood, ladies and gentlemen.\""))
        elif self.day > 1:
            out.append(self._pick(f"{lore.HOST}: \"{d} is still with us. Quietly.\"",
                                  f"{lore.HOST}: \"A steady day for {d}. The arena won't stay this kind.\""))
        if self.day + 1 == EDGE_WATCHED:
            out.append(f"{lore.HOST}: \"From tomorrow the Gamemakers are watching the edge of the arena. Anyone hiding there "
                       "will be found.\"")
        elif self.day + 1 == EDGE_CLOSES:
            out.append(f"{lore.HOST}: \"Tomorrow the arena closes in. The rocks and the ridge are gone.\"")
        return out

    def _scheduled_deaths(self, day: int) -> list[str]:
        n = self.schedule[day]
        self.schedule[day] = 0
        cands = [t for t in self.pool if t != self.nemesis]
        alive_rival = self.allies or self.reserved_allies or self.nemesis in self.pool
        n = max(0, min(n, len(cands) - (0 if alive_rival else 1)))
        fallen = cands[:n]
        self.pool = [t for t in self.pool if t not in fallen]
        self.fallen_today.extend(fallen)
        return fallen

    # ------------------------------------------------------------------ places
    def _hide_heal(self) -> int:
        return HIDE_HEAL * (2 if self.district == 7 else 1) + (15 if "sleeping_bag" in self.kit else 0)

    def _ridge_chance(self) -> int:
        if self.district == 3:
            return 90
        if "rope" in self.kit:
            return 70
        return self._chance({"base": 50, "mods": ["forage"]})

    def _open_chance(self) -> int:
        return min(95, OPEN_GIFT + (20 if self.district == 1 else 0))

    def _place_options(self) -> list[dict]:
        out = []
        for key in self.arena.offers[self.day]:
            emoji, label, hint, tag = PLACES_INFO[key]
            extra = {}
            if key == "stream":
                win, lose = ["🎒+1"], ["⚔️ others drink here too"]
            elif key == "rocks":
                win, lose = [f"❤️+{self._hide_heal()}"], ["🎒−1"]
            elif key == "ridge":
                if any(w in self.kit for w in lore.HUNTERS):
                    per = 2 if self.district == 10 else 1
                    win, lose = [f"🎒+{per} a hit"], ["🐾 noise if you miss"]
                    extra["minigame"] = HUNT
                    hint = "Hunt up high: three shots. The edge of the arena."
                else:
                    snares = self.district == 3 or "rope" in self.kit
                    win, lose = [f"🎒+{2 if self.district == 3 else 1 if snares else 2} likely"], ["🐾 some danger"]
                    hint = ("Set snares up high." if snares else "Forage up high.") + " The edge of the arena."
            elif key == "horn":
                win, lose = ["🎁 loot likely"], ["⚔️ fights likely"]
            elif key == "open":
                win, lose = ["🎁 sponsor gift likely"], ["👁️ you'll be seen"]
            else:  # feast
                win, lose = ["❤️ 🎒 what you need"], ["⚔️ everyone"]
            if key in EDGE and (self.edge_streak >= 1 or self.day >= EDGE_WATCHED):
                lose = lose + ["⚠️ Gamemakers"]
            out.append(self._opt(PLACE_KEYS.index(key), f"{emoji} {label}", hint, None, tag, win, lose, place=key, **extra))
        return out

    def _resolve_place(self, index: int, param: int | None) -> tuple:
        key = PLACE_KEYS[index]
        self.place = key
        if key == "feast":
            return ("draw", "feast")
        self.days[self.day].append(PLACES_INFO[key][0])
        if key == "stream":
            self.supplies += 1
            self._say(self._pick("You drink until your stomach hurts and fill everything you have.",
                                 "Cold water, and a few fish too slow to get away."), "result")
        elif key == "rocks":
            self.health = min(100, self.health + self._hide_heal())
            self.supplies = max(0, self.supplies - 1)
            self._say(self._pick("You wedge yourself into a crack in the rocks and sleep most of the day. It costs you a day's food.",
                                 "A dry hollow under an overhang. You don't move until dark, and you eat more than you'd like."), "result")
        elif key == "ridge":
            if any(w in self.kit for w in lore.HUNTERS):
                hits = max(0, min(3, int(param or 0)))
                if hits:
                    self.supplies += hits * (2 if self.district == 10 else 1)
                    self._say(f"{hits} hit{'s' if hits > 1 else ''}. You eat well tonight.", "result")
                else:
                    self._say("You miss everything. The noise carries.", "result")
                    if not self._roll(70):
                        self._say("Someone heard.", "result")
                        self._hurt(20, "a tribute who heard you hunting")
            else:
                _, ok, _ = self._reveal(self._ridge_chance())
                if ok:
                    snares = self.district == 3 or "rope" in self.kit
                    self.supplies += 2 if self.district == 3 or not snares else 1
                    self._say("Something is caught by morning." if snares else
                              self._pick("Pine nuts, and rainwater pooled in a hollow stump.", "Wild onions and a seep of clean water between the rocks."), "result")
                else:
                    self._say(self._pick("You find nothing but blisters.", "Everything edible up here was eaten days ago."), "result")
        elif key == "horn":
            _, ok, _ = self._reveal(self._chance({"base": HORN_LOOT, "mods": ["grab"]}))
            self._say("You creep toward the Cornucopia and pick through what the bloodbath left behind." if ok else
                      "You creep toward the Cornucopia. Picked clean.", "result")
            if ok:
                self._apply({"gift": True}, {})
        elif key == "open":
            _, ok, _ = self._reveal(self._open_chance())
            if ok:
                self._gift()
            else:
                self._say("Nothing falls from the sky. And now everyone knows where you are.", "result")
        if self.over:
            return ()
        card = self._place_card(key)
        if card:
            return ("draw", card)
        self._say("The rest of the day passes quietly.", "result")
        return ("night",)

    def _place_card(self, key: str) -> str | None:
        for cid in ("nemesis_sighting", "nemesis_clash"):
            c = cards()[cid]
            if cid not in self.used and key in c["place"] and self._eligible(c):
                return cid
        for cid in self.arena.deck:
            c = cards()[cid]
            if cid not in self.used and key in c.get("place", ()) and self._eligible(c):
                return cid
        return None

    # ------------------------------------------------------------------ the end
    def _start_duel(self) -> None:
        # Everyone but you and one rival goes. Your ally first (only one can win), then the nemesis.
        if self.allies:
            self.opponent_id = self.opponent = self.allies[0]
        elif self.nemesis in self.pool:
            self.opponent_id = self.nemesis
            self.opponent = f"{self.nemesis_name} ({self.nemesis[4:]})"
        elif self.pool:
            self.opponent_id = self.pool[-1]
            self.opponent = self._name(self.pool[-1])
        else:
            self.opponent_id = self.opponent = "the last Career"
        rest = [t for t in self.pool if t != self.opponent_id]
        rest_allies = [a for a in self.allies if a != self.opponent_id]
        self.pool = [t for t in self.pool if t == self.opponent_id]
        self.allies = [a for a in self.allies if a == self.opponent_id]
        gone = rest + rest_allies
        if gone:
            self.fallen_today.extend(gone)
            line = f"{len(gone)} cannon{'s' if len(gone) != 1 else ''} in a row."
            if self.nemesis in gone:
                line += f" One of them is {self.nemesis_name}."
            self._say(line, "cannon")
        self.card = DUEL
        self.duel_wins = self.duel_losses = 0
        self.days[self.day].append("⚔️")
        text = f"Two tributes left. You, and {self.opponent}, face to face on the horn. First to win two rounds."
        self._say(text + " " + self._edge_text(), "event")

    def _edge_text(self) -> str:
        good = [f"{e} {label}" for label, e, v, _ in self._edges() if v > 0]
        bad = [f"{e} {label}" for label, e, v, _ in self._edges() if v < 0]
        text = f"Your edge: {', '.join(good)}." if good else "You have no edge. Only nerve."
        if bad:
            text += f" Against you: {', '.join(bad)}."
        return text

    def _edges(self) -> list[tuple[str, str, int, set]]:
        out = []
        weapons = [w for w in self.kit if w in lore.WEAPONS]
        if weapons:
            w = max(weapons, key=lambda k: lore.WEAPONS[k])
            out.append((f"your {lore.item_name(w)}", lore.item_emoji(w), lore.WEAPONS[w], {"press"}))
        hw = health_word(self.health)
        hv = {"Good": 15, "Fair": 0, "Poor": -15, "Very poor": -25}[hw]
        if hv:
            out.append(("good health" if hv > 0 else f"{hw.lower()} health", "❤️", hv, {"press", "hold"}))
        if self.kills >= 2:
            out.append((f"{self.kills} kills behind you", "⚔️", 10, {"press"}))
        if self.district == 2:
            out.append(("Career training", "🛡️", 10, {"press"}))
        if "rope" in self.kit:
            out.append(("the high ground", "🪢", 15, {"hold"}))
        if self.supplies >= 2:
            out.append(("a full stomach", "🎒", 10, {"hold"}))
        if self.opponent_id == self.nemesis:
            v = min(15, 3 * self.nemesis_kills)
            out.append((f"their {self.nemesis_kills} kills", "🩸", -v, {"press", "hold"}))
        return out

    def _duel_chance(self, how: str) -> int:
        return max(5, min(95, DUEL_BASE + sum(v for _, _, v, a in self._edges() if how in a)))

    def _duel_options(self) -> list[dict]:
        out = []
        for i, (how, label, tag) in enumerate((("press", "Press the attack", "bold"), ("hold", "Hold your ground", "safe"))):
            edges = [f"{lbl[0].upper()}{lbl[1:]} {_sign(v)}" for lbl, _, v, a in self._edges() if how in a]
            hint = " · ".join(edges) if edges else "Nothing on your side but luck."
            dmg = DUEL_HIT[how]
            win = ["👑"] if self.duel_wins == 1 else ["round won"]
            lose = [f"❤️−{dmg}"] + (["☠️"] if self.duel_losses == 1 or dmg >= self.health else [])
            out.append(self._opt(i, label, hint, self._duel_chance(how), tag, win, lose))
        return out

    def _resolve_duel(self, index: int) -> str | None:
        how = ("press", "hold")[index]
        _, ok, near = self._reveal(self._duel_chance(how))
        if ok:
            self.duel_wins += 1
            self._say(self._pick("You drive them back across the gold.", "Your blow lands. They stagger.") if how == "press"
                      else self._pick("They come at you and overreach. You make them pay.", "You hold. They tire first."), "result")
        else:
            self.duel_losses += 1
            dmg = DUEL_HIT[how] // (2 if near else 1)
            self._say(("Close. " if near else "") + self._pick("They're faster than you.", "You slip on the wet gold."), "result")
            self._hurt(dmg, f"{self.opponent}, in the final duel")
            if self.over:
                return None
        if self.duel_wins == 2:
            self._say(self._pick("It's over. The trumpets sound.", "They fall. You don't."), "result")
            if self.opponent_id in self.allies:
                self.allies.remove(self.opponent_id)
            self.pool = []
            self.kills += 1
            self.new_kills.append(self.opponent)
            self._win()
        elif self.duel_losses == 2:
            self._die(f"{self.opponent}, in the final duel")
        else:
            rnd = self.duel_wins + self.duel_losses + 1
            return f"Round {rnd} of 3. You {self.duel_wins}, {self.opponent} {self.duel_losses}. {self._edge_text()}"
        return None

    # ------------------------------------------------------------------ effects
    def _apply(self, e: dict, card: dict, near: bool = False) -> str | None:
        cause = self._fmt(e.get("cause", card.get("cause", "the arena")))
        if "supplies" in e:
            self.supplies = max(0, self.supplies + e["supplies"])
        for item in self._list(e.get("remove")):
            if item in self.kit:
                self.kit.remove(item)
        for item in self._list(e.get("add")):
            self._gain(item)
        if e.get("gift"):
            pool = [i for i in ("knife", "spear", "rope", "medkit", "iodine", "axe") if i not in self.kit]
            if pool:
                item = self._dice().choice(pool)
                self._say(f"You found {'an' if item[0] in 'aeiou' else 'a'} {lore.item_name(item)}.", "gift")
                self._gain(item)
        if e.get("kill"):
            self._kill(e["kill"])
        if e.get("partner_dies") and "partner" in self.pool:
            self.pool.remove("partner")
            self.fallen_today.append("partner")
        ally = e.get("ally")
        if ally and ally != "joins_partner" and self.allies:
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
                dmg = round(-e["health"] * DAMAGE)
                self._hurt(dmg // 2 if near else dmg, cause)
            else:
                self.health = min(100, self.health + e["health"])
        if e.get("sponsor") and not self.over:
            self._gift()
        if e.get("die"):
            self._die(self._fmt(e["die"]))
        if e.get("win") or (not self.over and self.remaining == 1):
            self._win()
        return None if self.over else e.get("next")

    def _kill(self, n: int) -> None:
        for _ in range(n):
            if self.target in self.pool:
                v = self.target
                self.target = None
            else:
                victims = [t for t in self.pool if t not in ("partner", self.nemesis)] or self.pool
                if not victims:
                    return
                v = victims[0]
            self.pool.remove(v)
            self.fallen_today.append(v)
            self.new_kills.append(self._name(v))
            self.kills += 1
            self.kills_today += 1
            for d in range(self.day, MAX_DAYS + 1):
                if self.schedule[d] > 0:
                    self.schedule[d] -= 1
                    break
        if "⚔️" not in self.days[self.day]:
            self.days[self.day].append("⚔️")

    def _gift(self) -> None:
        if self.health < 50:
            self.health = min(100, self.health + 30)
            what = "medicine"
        elif self.supplies <= 1:
            self.supplies += 3
            what = "bread and a canteen of water"
        elif not any(w in self.kit for w in lore.WEAPONS) and len(self.kit) < lore.KIT_SLOTS:
            self.kit.append("knife")
            what = "a knife"
        elif "medkit" not in self.kit and len(self.kit) < lore.KIT_SLOTS:
            self.kit.append("medkit")
            what = "a medkit"
        else:
            self.supplies += 2
            what = "a hot meal"
        self.days[self.day].append("🎁")
        self._say(f"A silver parachute drifts down: {what}.", "gift")

    def _hurt(self, n: int, cause: str) -> None:
        self.health -= n
        if self.health <= 0:
            self._die(cause)

    def _die(self, cause: str) -> None:
        if self.over:
            return
        label, chance, health = self.last
        if cause == "starvation":
            self.recap = "You ran out of supplies."
        elif label and chance is not None:
            self.recap = f"You took the {chance}% “{label}” with {health} health."
        elif label:
            self.recap = f"You chose “{label}” with {health} health."
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
                c += max([lore.WEAPONS.get(i, 0) for i in self.kit] + [0])
                c += 10 if self.district == 2 else 0
                c += 5 * len(self.allies)
                c += penalty
            elif m == "escape":
                c += 10 if "rope" in self.kit else 0
                c += 10 if self.district == 5 else 0
                c += penalty
            elif m == "stealth":
                c -= 5 * len(self.allies)
                c += 15 if self.district == 8 else 0
                c += penalty
            elif m == "forage":
                c += 30 if self.district == 11 else 0
                c += 20 if "iodine" in self.kit else 0
            elif m == "swim":
                c += 30 if self.district == 4 else 0
            elif m == "grab":
                c += 10 if self.district == 6 else 0
        return max(5, min(95, c))

    def _dice(self) -> random.Random:
        return _rng(self.salt, self.steps, len(self.log))

    def _roll(self, chance: int) -> bool:
        return self._dice().randint(1, 100) <= chance

    def _reveal(self, chance: int) -> tuple[int, bool, bool]:
        """Roll for an option the player chose, and log the roll so the screen can show it."""
        roll = self._dice().randint(1, 100)
        ok = roll <= chance
        near = not ok and roll <= chance + NEAR
        self.log.append({"day": self.day, "kind": "roll", "text": f"{chance}% · rolled {roll}",
                         "chance": chance, "roll": roll, "ok": ok, "near": near})
        return roll, ok, near

    def _has(self, need: dict | None) -> bool:
        if not need:
            return True
        if "item" in need and need["item"] not in self.kit:
            return False
        if "any" in need and not any(i in self.kit for i in need["any"]):
            return False
        if "perk" in need and self.district != need["perk"]:
            return False
        if need.get("ally") and not self.allies:
            return False
        if "supplies" in need and self.supplies < need["supplies"]:
            return False
        return True

    # ------------------------------------------------------------------ text
    def _snap(self) -> tuple:
        self.new_kills = []
        return (self.health, self.supplies, list(self.kit), list(self.allies))

    def _delta(self, snap: tuple, night: bool = False) -> None:
        if self.over and not self.won:
            return
        health, supplies, kit, allies = snap
        parts = []
        if self.health != health:
            parts.append(f"❤️ {_sign(self.health - health)}")
        if self.supplies != supplies:
            parts.append(f"🎒 {_sign(self.supplies - supplies)}")
        for k in self.kit:
            if k not in kit:
                parts.append(f"+{lore.item_emoji(k)} {lore.item_name(k)}")
        for k in kit:
            if k not in self.kit:
                parts.append(f"−{lore.item_emoji(k)} {lore.item_name(k)}")
        for name in self.new_kills:
            parts.append(f"⚔️ {name}")
        for a in self.allies:
            if a not in allies:
                parts.append(f"+{a}")
        for a in allies:
            if a not in self.allies:
                parts.append(f"−{a}")
        self.new_kills = []
        if parts:
            self._say("  ".join(parts), "delta")

    def _name(self, t: str) -> str:
        if t == "partner":
            return f"{self.partner}, your district partner"
        if t == self.nemesis:
            return f"{self.nemesis_name} ({t[4:]})"
        return t

    def _pick(self, *lines: str) -> str:
        return self._dice().choice(lines)

    def _fmt(self, s: str) -> str:
        if "{" not in s:
            return s
        t = self._name(self.target) if self.target else "a tribute"
        nem = self.nemesis_name
        return s.format(
            ally=self.allies[0] if self.allies else "your ally",
            partner=self.partner,
            target=t,
            Target=t[0].upper() + t[1:],
            nemesis=nem,
            Nemesis=nem[0].upper() + nem[1:],
            remaining=self.remaining,
            host=lore.HOST,
            opponent=self.opponent or "the last tribute",
            district=self.district,
        )

    def _say(self, text: str, kind: str) -> None:
        self.log.append({"day": self.day, "kind": kind, "text": text})

    # ------------------------------------------------------------------ views
    def status(self) -> dict:
        return {
            "day": self.day,
            "health": self.health,
            "health_word": health_word(self.health),
            "supplies": self.supplies,
            "kit": [{"id": k, "name": lore.ITEMS[k][0], "emoji": lore.ITEMS[k][1], "desc": lore.ITEMS[k][2]} for k in self.kit],
            "slots": lore.KIT_SLOTS,
            "kills": self.kills,
            "remaining": self.remaining,
            "allies": list(self.allies),
            "can_use": "medkit" if self.can_use_medkit() else None,
            "duel": [self.duel_wins, self.duel_losses] if self.card == DUEL else None,
        }

    def emoji_row(self) -> str:
        priority = ["👑", "☠️", "⚔️", "🎁"]
        row = []
        for d in range(1, min(self.day, MAX_DAYS) + 1):
            marks = self.days[d]
            if not marks:
                continue
            if d == 1 and not any(p in marks for p in priority[:2]):
                row.append(marks[0])  # the bloodbath shows what you grabbed (or 🌲)
                continue
            row.append(next((p for p in priority if p in marks), marks[0]))  # else where you went
        return "".join(row)

    def score(self) -> int:
        if not self.over:
            return 0
        place = self.placement or START_TRIBUTES
        pts = (START_TRIBUTES + 1 - place) * 40 + self.kills * 60 + self.day * 25
        if self.won:
            pts += 500
        return round(pts * lore.multiplier(self.district))


def new_setup(district: int, partner: str, allies: list[str]) -> dict:
    if district not in lore.DISTRICTS:
        raise ValueError("bad district")
    return {"district": district, "partner": partner, "allies": allies[:2]}


def replay(seed: int, salt: int, setup: dict, choices: list[list[int | None]]) -> Game:
    g = Game(seed, salt, setup)
    for idx, param in choices:
        g.choose(idx, param)
    return g

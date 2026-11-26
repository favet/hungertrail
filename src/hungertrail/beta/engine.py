"""Hunger Trail BETA rules engine.

Same contract as the live engine: pure and deterministic, a run is (seed, salt, setup, choices).
What's different:

- Three stats on screen: health, supplies (food and water together, in days) and a 3-slot kit.
- The bloodbath is a draft: seeded loot around the Cornucopia, closer is better and deadlier.
- Every option states its stakes (chips for what you win and what you lose), computed from the
  same effects the engine applies, so the screen can't lie.
- A failed roll within NEAR points is a near miss: the damage is halved.
- The crowd is hidden. Bold moves please it (marked 🎥) and sponsors pay at once; hiding bores
  it, Caesar says so at night, and if it stays bored the Gamemakers act the next morning.
- The final duel lists your edges (kit, health, kills, crowd) and they set the odds.
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
DUEL_BASE = 30  # final duel odds before your edges
GIFT_AT = 40  # crowd needed for a sponsor gift
RESTLESS = -15  # crowd at or below this at night: the Gamemakers notice
HUNT = "hunt"
DRAFT, CAMP, SWAP, DUEL = "draft", "camp", "swap", "duel"

# Cornucopia rings: base odds, damage on a miss, how it reads.
RINGS = {
    "edge": (85, 15, "At the edge"),
    "mid": (65, 30, "Halfway in"),
    "horn": (45, 45, "At the mouth of the horn"),
}


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
        self.feast_day = r.choice([5, 6])

        # The loot around the horn. Same layout for everyone today: the bloodbath talking point.
        h = _rng(seed, "horn")
        edge = h.sample(["pack", "knife", "rope", "medkit"], 2)
        mid = h.sample([i for i in ("spear", "axe", "sleeping_bag", "iodine", "rope", "medkit") if i not in edge], 2)
        top = h.choice(["bow", "trident"])
        second = h.choice([i for i in ("crate", "bow", "trident", "axe") if i != top and i not in mid])
        self.layout = [(i, "edge") for i in edge] + [(i, "mid") for i in mid] + [(top, "horn"), (second, "horn")]

        others = [f"the District {d} {g}" for d in range(1, 13) for g in ("boy", "girl") if d != district]
        r.shuffle(others)
        self.death_order = others + ["partner"]
        r.shuffle(self.death_order)

        sched = [0] * (MAX_DAYS + 2)
        sched[1] = r.randint(8, 11)
        left = (START_TRIBUTES - 1 - sched[1]) - 2
        for _ in range(left):
            sched[r.randint(2, MAX_DAYS - 1)] += 1
        sched[self.feast_day] += 1
        self.schedule = sched


class Game:
    def __init__(self, seed: int, salt: int, setup: dict):
        self.setup = setup
        self.district: int = setup["district"]
        self.partner: str = setup["partner"]
        self.arena = Arena(seed, self.district)
        self.seed = seed
        self.salt = salt
        self.schedule = list(self.arena.schedule)

        self.day = 1
        self.health = 100
        self.supplies = 2 + (2 if self.district == 9 else 0)
        self.kit: list[str] = []
        self.crowd = 10 + (20 if self.district == 1 else 0)
        self.warned = False
        self.gm_due = False
        self.kills = 0
        self.pool = list(self.arena.death_order)
        self.allies: list[str] = []
        self.ally_names: list[str] = list(setup.get("allies", []))
        self.fallen_today: list[str] = []
        self.announced = 0
        self.used: set[str] = set()
        self.log: list[dict] = []
        self.days: list[list[str]] = [[] for _ in range(MAX_DAYS + 2)]
        self.bold_today = False
        self.over = False
        self.won = False
        self.cause: str | None = None
        self.placement: int | None = None
        self.steps = 0
        self.card = ""
        self.target: str | None = None
        self.opponent: str | None = None
        self.grabs = 0
        self.taken: set[int] = set()
        self.pending_item: str | None = None
        self.resume: tuple = ()
        self.new_kills: list[str] = []
        self.shows = 0  # times you've played to the cameras: the same show pleases less each time

        r = _rng(seed, "allies", self.district)
        for _ in self.ally_names:
            self.pool.remove(r.choice([t for t in self.pool if t != "partner"]))
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

    def options(self) -> list[dict]:
        if self.over:
            return []
        if self.card == DRAFT:
            return self._draft_options()
        if self.card == CAMP:
            return self._camp_options()
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
                crowd = o.get("tag") == "bold" or o["success"].get("effects", {}).get("crowd", 0) > 0
                out.append(self._opt(i, o["label"], o.get("hint", ""), self._chance(o["roll"]), o.get("tag", "safe"), win, lose, crowd))
            else:
                e = o.get("effects", {})
                crowd = o.get("tag") == "bold" or e.get("crowd", 0) > 0
                out.append(self._opt(i, o["label"], o.get("hint", ""), None, o.get("tag", "safe"), self._chips(e, card), [], crowd))
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
        snap = self._snap()
        self._say(valid[index]["label"], "choice")
        if self.card == DRAFT:
            dest = self._resolve_draft(index)
        elif self.card == CAMP:
            self._resolve_camp(index, param)
            dest = ("night",)
        elif self.card == SWAP:
            dest = self._resolve_swap(index)
        elif self.card == DUEL:
            self._resolve_duel(index)
            dest = ()
        else:
            dest = self._resolve_card(index)
        self._delta(snap)
        self._advance(dest)

    # ------------------------------------------------------------------ option helpers
    def _opt(self, index, label, hint, chance, tag, win, lose, crowd, **extra) -> dict:
        deadly = "☠️" in lose
        return {
            "index": index, "label": self._fmt(label), "hint": self._fmt(hint), "chance": chance,
            "tag": tag, "win": win, "lose": lose, "crowd": bool(crowd), "deadly": deadly, **extra,
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
                                 [f"{lore.item_emoji(item)} {lore.item_name(item)}"], lose, ring == "horn"))
        label = "Run for the trees" if not self.grabs else "Run while you can"
        hint = "You'll arrive with nothing but your life." if not self.grabs else "Take what you have and go."
        out.append(self._opt(len(self.arena.layout), label, hint, None, "hide", [], [], False))
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
        roll = self._dice().randint(1, 100)
        name = lore.item_name(item)
        if roll <= chance:
            self._say(self._pick(f"You snatch the {name} and keep moving.", f"The {name} is yours. Nobody touched you."), "result")
            self._take(item)
            if ring == "horn":
                self._crowd(15)
            return ("draft",) if self.grabs < 2 else ("bloodbath",)
        if roll <= chance + NEAR:
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
        if self.ally_names:
            names = " and ".join(self.ally_names)
            self.allies = list(self.ally_names)
            self.reserved_allies = 0
            self._say(f"At dusk, {names} find{'s' if len(self.ally_names) == 1 else ''} you. The alliance holds. For now.", "story")
        self._draw(CAMP)

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
                      [f"{lore.item_emoji(new)} {lore.item_name(new)}"], [], False)
            for i, k in enumerate(self.kit)
        ]
        out.append(self._opt(lore.KIT_SLOTS, f"Leave the {lore.item_name(new)}", lore.ITEMS[new][2], None, "safe", [], [], False))
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
        elif kind == "camp":
            self._draw(CAMP)
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
        if card_id == CAMP:
            self._say("Night falls. What do you do before you sleep?", "prompt")
            return
        card = cards()[card_id]
        self.used.add(card_id)
        self.target = self._pick_target(card) if card.get("target") else None
        self.days[self.day].append(card["emoji"])
        self._say(self._fmt(card["text"]), "event")

    def _pick_target(self, card: dict) -> str | None:
        want = card["target"]
        cands = [t for t in self.pool if t != "partner" and (want == "any" or t.endswith(want))]
        if not cands:
            return None
        return _rng(self.seed, card["id"], self.district).choice(sorted(cands))

    def _resolve_card(self, index: int) -> tuple:
        card = cards()[self.card]
        opt = card["options"][index]
        near = False
        if "roll" in opt:
            chance = self._chance(opt["roll"])
            roll = self._dice().randint(1, 100)
            ok = roll <= chance
            near = not ok and roll <= chance + NEAR
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
        if nxt == DUEL:
            return ("duel",)
        if nxt:
            return ("draw", nxt)
        return ("camp",)

    def _morning(self) -> None:
        self.bold_today = False
        self.fallen_today = []
        self.announced = 0
        self._say(f"Day {self.day}. {self.remaining} tributes remain.", "day")
        if self.remaining <= 2:
            return self._start_duel()
        if self.day >= MAX_DAYS:
            return self._draw("flood")
        if self.day == self.arena.feast_day and "feast" not in self.used:
            return self._draw("feast")
        if self.gm_due:
            self.gm_due = False
            self.warned = False
            self.crowd = max(self.crowd, 0)
            gm = next((g for g in self.arena.gamemakers if g not in self.used), self.arena.gamemakers[0])
            return self._draw(gm)
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
        if card.get("target") and not self._pick_target(card):
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

        if not self.bold_today:
            self.crowd = max(-100, self.crowd - 15)
        self._say(self._caesar(), "caesar")
        if self.day + 1 == self.arena.feast_day and "feast" not in self.used:
            self._say(f"{lore.HOST} announces a feast at the Cornucopia tomorrow at dawn.", "caesar")

        self._scheduled_deaths(self.day)
        n = len(self.fallen_today) - self.announced
        if n:
            self._say(f"{'A cannon fires' if n == 1 else f'{n} cannons fire'} in the distance today.", "cannon")
        if self.fallen_today:
            self._say("The anthem plays. Faces in the sky: " + ", ".join(self._name(t) for t in self.fallen_today) + ".", "sky")
        else:
            self._say("The anthem plays. No faces in the sky tonight.", "sky")
        self.day += 1
        self._morning()

    def _caesar(self) -> str:
        d = f"District {self.district}"
        if self.crowd <= RESTLESS:
            if self.gm_due:
                return f"{lore.HOST}: \"The Gamemakers haven't forgotten {d}. They're just waiting for the right moment.\""
            if self.warned:
                self.gm_due = True
                return f"{lore.HOST}: \"The Gamemakers have run out of patience with our {d} tribute. Tomorrow should be exciting.\""
            self.warned = True
            return self._pick(
                f"{lore.HOST}: \"{d} has gone very quiet. I hear the Gamemakers are getting restless.\"",
                f"{lore.HOST}: \"Is our {d} tribute still in there? The Gamemakers are wondering too.\"",
            )
        self.warned = False
        if self.crowd >= GIFT_AT - 15:
            return self._pick(
                f"{lore.HOST}: \"The Capitol can't look away from {d}. The sponsors are circling.\"",
                f"{lore.HOST}: \"What a tribute! {d} has the whole Capitol talking. Sponsors, are you watching?\"",
            )
        if self.crowd >= 0:
            return self._pick(
                f"{lore.HOST}: \"{d} is still with us. The Capitol is watching, but it isn't excited.\"",
                f"{lore.HOST}: \"A steady day for {d}. Steady doesn't win sponsors, my friends.\"",
            )
        return self._pick(
            f"{lore.HOST}: \"A dull day for {d}. The audience is drifting.\"",
            f"{lore.HOST}: \"{d} spent the day hiding. The Capitol yawned.\"",
        )

    def _scheduled_deaths(self, day: int) -> list[str]:
        n = self.schedule[day]
        self.schedule[day] = 0
        keep = 0 if self.allies or self.reserved_allies else 1
        n = max(0, min(n, len(self.pool) - keep))
        fallen = self.pool[:n]
        self.pool = self.pool[n:]
        self.fallen_today.extend(fallen)
        return fallen

    # ------------------------------------------------------------------ camp
    def _camp_options(self) -> list[dict]:
        heal = 10 * (2 if "sleeping_bag" in self.kit else 1) * (2 if self.district == 7 else 1)
        o = [self._opt(0, "Rest", "Heal up. The crowd finds it dull.", None, "hide", [f"❤️+{heal}"], [], False)]
        o.append(self._opt(1, "Forage", "Look for food and water nearby.", self._forage_chance(), "safe", ["🎒+2"], [], False))
        if any(w in self.kit for w in lore.HUNTERS):
            per = "+2" if self.district == 10 else "+1"
            o.append(self._opt(2, "Hunt", "Three shots. Miss all three and someone may hear.", None, "bold",
                               [f"🎒{per} a hit"], ["❤️−20 if you miss all three"], True, minigame=HUNT))
        if self.district == 3 or "rope" in self.kit:
            gain = 2 if self.district == 3 else 1
            o.append(self._opt(3, "Set snares", "Quiet food by morning.", self._snare_chance(), "safe", [f"🎒+{gain}"], [], False))
        show_hint = ("Sponsors love a show. So do the other tributes." if self.shows == 0 else
                     "They've seen this show before. It pleases them less each time." if self._show_gain() else
                     "The Capitol is sick of this act. It won't help now.")
        o.append(self._opt(4, "Play to the cameras", show_hint, self._chance({"base": 75, "mods": ["stealth"]}),
                           "bold", ["🎥 sponsors"] if self._show_gain() else [], ["❤️−25"] + (["☠️"] if self.health <= 25 else []), self._show_gain() > 0))
        if "medkit" in self.kit and self.health < 100:
            o.append(self._opt(5, "Use the medkit", "One use.", None, "safe", ["❤️+40", "−🩹"], [], False))
        return o

    def _show_gain(self) -> int:
        return max(0, 12 - 4 * self.shows)

    def _forage_chance(self) -> int:
        return self._chance({"base": 50, "mods": ["forage"]})

    def _snare_chance(self) -> int:
        return 90 if self.district == 3 else 70

    def _resolve_camp(self, index: int, param: int | None) -> None:
        if index == 0:
            heal = 10 * (2 if "sleeping_bag" in self.kit else 1) * (2 if self.district == 7 else 1)
            self.health = min(100, self.health + heal)
            self.days[self.day].append("💤")
            self._say(self._pick(
                "You sleep, and wake a little stronger.",
                "You sleep like the dead. Not literally. Not yet.",
                "You curl up under a fallen log and sleep until the birds wake you.",
            ), "result")
        elif index == 1:
            if self._roll(self._forage_chance()):
                self.supplies += 2
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
                self.supplies += hits * (2 if self.district == 10 else 1)
                self._say(f"{hits} hit{'s' if hits > 1 else ''}. You eat well tonight." + (" The Capitol loves a marksman." if hits == 3 else ""), "result")
                if hits == 3:
                    self._crowd(10)
            else:
                self._say("You miss everything. The noise carries.", "result")
                if not self._roll(70):
                    self._say("Someone heard.", "result")
                    self._hurt(20, "a tribute who heard you hunting")
        elif index == 3:
            if self._roll(self._snare_chance()):
                self.supplies += 2 if self.district == 3 else 1
                self._say("Something is caught by morning.", "result")
            else:
                self._say("The snares are empty.", "result")
        elif index == 4:
            self.days[self.day].append("🎥")
            ok = self._roll(self._chance({"base": 75, "mods": ["stealth"]}))
            if ok:
                self._say(self._pick(
                    "You wave at the sky. Somewhere, a sponsor opens their wallet.",
                    "You tell the cameras about home. The Capitol eats it up.",
                    "You sing. Badly. They love it anyway.",
                    "You carve your district number into a tree, slowly, for the cameras.",
                ), "result")
            else:
                self._say("You weren't the only one watching.", "result")
                self._hurt(25, "a tribute who saw you showing off")
            if not self.over:
                self._crowd(self._show_gain())
                self.shows += 1
        elif index == 5:
            self.kit.remove("medkit")
            self.health = min(100, self.health + 40)
            self._say("You clean and bind your wounds. It stings like nothing else.", "result")

    # ------------------------------------------------------------------ the end
    def _start_duel(self) -> None:
        # Everyone but you and one rival goes. An ally, if you still have one: only one can win.
        if self.allies:
            self.opponent = self.allies[0]
            rest = self.allies[1:] + self.pool
            self.allies = self.allies[:1]
        else:
            self.opponent = self._name(self.pool[-1]) if self.pool else "the last Career"
            rest = self.pool[:-1]
            self.pool = self.pool[-1:]
        if rest:
            self.pool = [t for t in self.pool if t not in rest]
            self.allies = [a for a in self.allies if a not in rest]
            self.fallen_today.extend(rest)
            self._say(f"{len(rest)} cannon{'s' if len(rest) != 1 else ''} in a row.", "cannon")
        self.card = DUEL
        self.days[self.day].append("⚔️")
        good = [f"{e} {label}" for label, e, v, _ in self._edges() if v > 0]
        bad = [f"{e} {label}" for label, e, v, _ in self._edges() if v < 0]
        text = f"Two tributes left. You, and {self.opponent}, face to face on the horn."
        text += f" Your edge: {', '.join(good)}." if good else " You have no edge. Only nerve."
        if bad:
            text += f" Against you: {', '.join(bad)}."
        self._say(text, "event")

    def _edges(self) -> list[tuple[str, str, int, set]]:
        out = []
        weapons = [w for w in self.kit if w in lore.WEAPONS]
        if weapons:
            w = max(weapons, key=lambda k: lore.WEAPONS[k])
            out.append((f"your {lore.item_name(w)}", lore.item_emoji(w), lore.WEAPONS[w], {"fight"}))
        hw = health_word(self.health)
        hv = {"Good": 15, "Fair": 0, "Poor": -15, "Very poor": -25}[hw]
        if hv:
            out.append(("good health" if hv > 0 else f"{hw.lower()} health", "❤️", hv, {"fight", "outlast"}))
        if self.kills >= 2:
            out.append((f"{self.kills} kills behind you", "⚔️", 10, {"fight"}))
        if self.district == 2:
            out.append(("Career training", "🛡️", 10, {"fight"}))
        if "rope" in self.kit:
            out.append(("the high ground", "🪢", 15, {"outlast"}))
        if self.supplies >= 2:
            out.append(("a full stomach", "🎒", 10, {"outlast"}))
        if self.crowd >= 20:
            out.append(("the crowd", "🎥", 10, {"fight", "outlast"}))
        elif self.crowd <= RESTLESS:
            out.append(("a bored crowd", "🥱", -10, {"fight", "outlast"}))
        return out

    def _duel_chance(self, how: str) -> int:
        return max(5, min(95, DUEL_BASE + sum(v for _, _, v, a in self._edges() if how in a)))

    def _duel_options(self) -> list[dict]:
        out = []
        for i, (how, label, tag) in enumerate((("fight", "Fight", "bold"), ("outlast", "Wait them out", "safe"))):
            edges = [f"{lbl[0].upper()}{lbl[1:]} {_sign(v)}" for lbl, _, v, a in self._edges() if how in a]
            hint = " · ".join(edges) if edges else "Nothing on your side but luck."
            out.append(self._opt(i, label, hint, self._duel_chance(how), tag, ["👑"], ["☠️"], how == "fight"))
        return out

    def _resolve_duel(self, index: int) -> None:
        how = ("fight", "outlast")[index]
        if self._roll(self._duel_chance(how)):
            self._say(self._pick("It's over. The trumpets sound.", "They fall. You don't.") if how == "fight"
                      else "You wait. They don't. They slip on the wet gold, and the trumpets sound.", "result")
            if self.opponent in self.allies:
                self.allies.remove(self.opponent)
            else:
                self.pool = []
            self.kills += 1
            self.new_kills.append(self.opponent)
            self._win()
        else:
            self._say("So close." if how == "fight" else "They don't slip. You do.", "result")
            self._die(f"{self.opponent}, in the final duel")

    # ------------------------------------------------------------------ effects
    def _apply(self, e: dict, card: dict, near: bool = False) -> str | None:
        cause = self._fmt(e.get("cause", card.get("cause", "the arena")))
        if "supplies" in e:
            self.supplies = max(0, self.supplies + e["supplies"])
        if "crowd" in e:
            self._crowd(e["crowd"])
        for item in self._list(e.get("remove")):
            if item in self.kit:
                self.kit.remove(item)
        for item in self._list(e.get("add")):
            self._gain(item)
        if e.get("gift"):
            pool = [i for i in ("knife", "spear", "rope", "medkit", "iodine", "axe") if i not in self.kit]
            if pool:
                item = self._dice().choice(pool)
                self._say(f"You found {'an' if item[0] in 'aeiou' else 'a'} {lore.item_name(item)}.", "status")
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
                victims = [t for t in self.pool if t != "partner"] or self.pool
                if not victims:
                    return
                v = victims[0]
            self.pool.remove(v)
            self.fallen_today.append(v)
            self.new_kills.append(self._name(v))
            self.kills += 1
            for d in range(self.day, MAX_DAYS + 1):
                if self.schedule[d] > 0:
                    self.schedule[d] -= 1
                    break
            self._crowd(10)
        if "⚔️" not in self.days[self.day]:
            self.days[self.day].append("⚔️")

    def _crowd(self, n: int) -> None:
        self.crowd = max(-100, min(100, self.crowd + n))
        if n > 0 and self.crowd >= GIFT_AT and not self.over:
            self.crowd -= GIFT_AT
            self._gift()

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
        else:
            self.supplies += 2
            what = "a hot meal"
        self.days[self.day].append("🎁")
        self._say(f"The Capitol loved that. A silver parachute drifts down: {what}.", "gift")

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
        return f"{self.partner}, your district partner" if t == "partner" else t

    def _pick(self, *lines: str) -> str:
        return self._dice().choice(lines)

    def _fmt(self, s: str) -> str:
        if "{" not in s:
            return s
        t = self.target or "a tribute"
        return s.format(
            ally=self.allies[0] if self.allies else "your ally",
            partner=self.partner,
            target=t,
            Target=t[0].upper() + t[1:],
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
            row.append(next((p for p in priority if p in marks), marks[0]))
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

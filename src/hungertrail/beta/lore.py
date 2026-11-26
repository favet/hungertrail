"""Beta names and numbers: district perks, kit items, multipliers.

Franchise names still come from the live lore file, so there is one place to rename them.
"""

from ..game.lore import CAPITOL, DEFAULT_NAMES, DISTRICTS, GAMES, HORN, HOST  # noqa: F401

# One small perk per district, from its industry. Each one names the roll or rule it changes.
PERKS = {
    1: ("Pampered", "The Capitol already loves you. +20 on sponsor gifts out in the open."),
    2: ("Trained", "+10 in every fight."),
    3: ("Tinkerer", "You can set snares on the ridge without rope, and they catch more."),
    4: ("Fisher", "+30 in the water."),
    5: ("Quick", "+10 when running or climbing."),
    6: ("Sprinter", "+10 on every grab at the bloodbath."),
    7: ("Woodsman", "Hiding heals twice as much."),
    8: ("Unseen", "+15 when sneaking."),
    9: ("Stocked", "Start with 2 extra days of supplies."),
    10: ("Herder", "Hunting brings back an extra day of food."),
    11: ("Plant lore", "+30 when foraging. You know which berries kill."),
    12: ("Hardened", "Hunger hurts half as much."),
}

# Bot win rates by district came out within a few points of each other (perks balance), so the
# beta has no score multiplier: the district is a play style, not a difficulty setting.
def multiplier(district: int) -> float:
    return 1.0


# name, emoji, what it does for you (shown at the bloodbath and in your kit)
ITEMS = {
    "knife": ("knife", "🔪", "+8 in fights. Cuts you free."),
    "spear": ("spear", "🗡️", "+12 in fights. Lets you hunt and fish."),
    "axe": ("axe", "🪓", "+12 in fights. Chops through anything."),
    "bow": ("bow", "🏹", "+15 in fights. Lets you hunt."),
    "trident": ("trident", "🔱", "+20 in fights. Lets you hunt and fish."),
    "rope": ("rope", "🪢", "+10 when climbing or running. Lets you set snares on the ridge."),
    "sleeping_bag": ("sleeping bag", "🛏️", "Hiding heals 15 more. Keeps out the cold."),
    "medkit": ("medkit", "🩹", "Tap it to use it: heals 40, once."),
    "iodine": ("iodine", "🧪", "+20 when foraging. Makes any water safe."),
}
WEAPONS = {"knife": 8, "spear": 12, "axe": 12, "bow": 15, "trident": 20}
HUNTERS = ("bow", "spear", "trident")
KIT_SLOTS = 3

# Packs at the Cornucopia are supplies, not kit: they don't take a slot.
PACKS = {"pack": ("backpack", "🎒", 3), "crate": ("supply crate", "📦", 5)}


def item_name(i: str) -> str:
    return PACKS[i][0] if i in PACKS else ITEMS[i][0]


def item_emoji(i: str) -> str:
    return PACKS[i][1] if i in PACKS else ITEMS[i][1]

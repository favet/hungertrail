"""Every franchise name lives here, so swapping to original names later is one file."""

CAPITOL = "the Capitol"
GAMES = "the Hunger Games"
HORN = "the Cornucopia"
HOST = "Caesar"

DISTRICTS = {
    1: "Luxury",
    2: "Masonry",
    3: "Technology",
    4: "Fishing",
    5: "Power",
    6: "Transportation",
    7: "Lumber",
    8: "Textiles",
    9: "Grain",
    10: "Livestock",
    11: "Agriculture",
    12: "Mining",
}
CAREERS = {1, 2, 4}


def multiplier(district: int) -> int:
    """Oregon Trail's banker/carpenter/farmer: harder start, bigger score."""
    if district in CAREERS:
        return 1
    if district in (11, 12):
        return 3
    return 2


TRAITS = {
    "fighter": ("Fighter", "Better odds in every fight."),
    "climber": ("Climber", "Better odds when escaping."),
    "plants": ("Plant lore", "Spot poison, find food and medicine."),
    "snares": ("Snares", "Evening snares catch food reliably."),
    "swimmer": ("Swimmer", "Water is a road, not a wall."),
    "charm": ("Charm", "Sponsors love you. Favor gains +50%."),
}

ITEMS = {
    "knife": "a knife",
    "spear": "a spear",
    "axe": "an axe",
    "bow": "a bow",
    "trident": "a trident",
    "rope": "rope",
    "ointment": "burn ointment",
    "sleeping_bag": "a sleeping bag",
    "iodine": "iodine",
}
WEAPONS = {"knife": 8, "spear": 12, "axe": 12, "bow": 15, "trident": 25}

DEFAULT_NAMES = ["Rye", "Wren", "Bram", "Tess", "Cole", "Ivy", "Jory", "Marl", "Hollis", "Fen"]

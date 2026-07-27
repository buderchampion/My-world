import json
from pathlib import Path

DATA_DIR = Path (__file__).resolve().parent.parent

WAGES = DATA_DIR / "NPCS" / "Wages.json"
CITY_PAY_SCALING = DATA_DIR / "Buildings" / "CityPayScaling.json"

BASE_LEVEL_XP = 100
LEVEL_XP_GROWTH = 1.5

SKILL_XP_PER_DAY = 10


def load_json(file): 

    with open(file, "r") as f:
        return json.load(f)

def xp_to_next_level(level):

    return round(BASE_LEVEL_XP * (LEVEL_XP_GROWTH ** (level - 1)))


def get_wage(profession, city_type):

    wages = load_json(WAGES)
    scaling = load_json(CITY_PAY_SCALING)

    base_wage = wages.get(profession, 0)

    multiplier = scaling.get(city_type, {}).get("wage_multiplier", 1.0)

    return round(base_wage * multiplier, 2)


def add_skill_xp(skill, amount):

    skill["xp"] += amount

    while skill["xp"] >= xp_to_next_level(skill["level"]):
        skill["xp"] -= xp_to_next_level(skill["level"])
        skill["level"] += 1


def apply_workday(npc):

    scaling = load_json(CITY_PAY_SCALING)

    city_type = npc.get("city_type")

    xp_multiplier = scaling.get(city_type, {}).get("xp_multiplier", 1.0)

    wage = get_wage(npc["profession"], city_type)

    npc["gold"] = round(npc.get("gold", 0) + wage, 2)

    for skill in npc.get("skills", {}).values():
        add_skill_xp(skill, round(SKILL_XP_PER_DAY * xp_multiplier))

    return npc
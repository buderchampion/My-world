"""
The peoples of the world (Races.json): humans, elves, half-elves, dwarves,
halflings, gnomes, half-orcs, dragonborn, tieflings and orcs.

Kingdoms   each kingdom is of one people (about half are human), founded on
           its homelands where it can (dwarves in the hills and mountains,
           elves in the forests), its towns named in its own tongue.
People     most in a kingdom are of its people; minority_share are of the
           peoples who live among them (a dwarf kingdom's gnomes and humans).
           Names are their people's: a dwarf is "Thorgrim Ironfist", an elf
           "Caelith Moonwhisper".
Children   take their parents' people; an elf and a human have a half-elf, a
           human and an orc a half-orc (the Children table).
Aging      a person's `age` is their stage of life, the same for every people
           (16 is grown, 60 is old), so work, marriage and old age follow it
           everywhere. Their `years` are how long they have really lived: an
           elf's life stage moves on once every age_rate years (7.5), a
           dwarf's every 3.5, a half-orc's a little faster than a human's.
"""

from __future__ import annotations

import json
import math
import random
from functools import lru_cache
from pathlib import Path

RACES_FILE = Path(__file__).with_name("Races.json")
NAMES_DIR = Path(__file__).resolve().parents[1] / "Names"
HUMAN = "Human"


@lru_cache(maxsize=1)
def races():
    with open(RACES_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_") and k != "Children"}


@lru_cache(maxsize=1)
def _children():
    with open(RACES_FILE, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f)["Children"].items() if not k.startswith("_")}


@lru_cache(maxsize=4)
def _names(file):
    with open(NAMES_DIR / file, encoding="utf-8") as f:
        return json.load(f)


def race_of(name):
    return races().get(name) or races()[HUMAN]


def age_rate(race):
    return race_of(race).get("age_rate", 1.0)


# ---------------------------------------------------------------- kingdoms

def choose_kingdom_race():
    """A new kingdom's people, by kingdom_weight (humans about half)."""
    weights = {name: r.get("kingdom_weight", 0) for name, r in races().items() if r.get("kingdom_weight", 0) > 0}
    return random.choices(list(weights), weights=list(weights.values()))[0]


def homelands(race):
    return race_of(race).get("homelands") or []


def choose_person_race(kingdom_race):
    """Someone born or settled in a kingdom of this people: mostly its own, some of its minorities."""
    r = race_of(kingdom_race)
    if r.get("minorities") and random.random() < r.get("minority_share", 0):
        mins = r["minorities"]
        return random.choices(list(mins), weights=list(mins.values()))[0]
    return kingdom_race if kingdom_race in races() else HUMAN


def kingdom_race(conn, kingdom_id):
    row = conn.execute("SELECT race FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    return (row[0] if row and row[0] else HUMAN)


def city_race(conn, city_id):
    row = conn.execute("SELECT k.race FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE c.id = ?", (city_id,)).fetchone()
    return (row[0] if row and row[0] else HUMAN)


def child_race(race_a, race_b):
    """The people of a child of these two."""
    race_a, race_b = race_a or HUMAN, race_b or HUMAN
    if race_a == race_b:
        return race_a
    pair = "+".join(sorted((race_a, race_b)))
    return _children().get(pair) or random.choice((race_a, race_b))


# ---------------------------------------------------------------- names

def _syllables(table, kind):
    return random.choice(table["start"]) + random.choice(table[kind])


def given_name(race, gender):
    """A given name in this people's tongue (half-peoples take either parent people's names)."""
    r = race_of(race)
    if r.get("names_of"):
        return given_name(random.choice(r["names_of"]), gender)
    if r.get("given"):
        return _syllables(r["given"], "male" if gender == "Male" else "female")
    return random.choice(_names("Human_Male.json" if gender == "Male" else "Human_Female.json"))


def family_name(race, taken=()):
    """A family name in this people's tongue, new to this world while any are left."""
    r = race_of(race)
    if r.get("family_of"):
        return family_name(random.choice(r["family_of"]), taken)
    if r.get("family"):
        for _ in range(50):
            name = random.choice(r["family"]["start"]) + random.choice(r["family"]["end"])
            if name not in taken:
                return name
        return name
    pool = _names("FamilyNames.json")
    free = [n for n in pool if n not in taken]
    return random.choice(free or pool)


def place_name(race, taken=()):
    """A town's (or a kingdom's) name in this people's tongue, unless `race` is human (None: the caller's own lists).
    Never one already `taken`, while any are left."""
    r = race_of(race)
    if not r.get("places"):
        return None
    name = None
    for _ in range(100):
        name = random.choice(r["places"]["start"]) + random.choice(r["places"]["end"])
        if name not in taken:
            return name
    return name


# ---------------------------------------------------------------- years

def years_for(race, age):
    """Real years lived for someone at this stage of life (somewhere within that stage-year)."""
    rate = age_rate(race)
    return int(age * rate + random.random() * rate)


def stage_for(race, years):
    return int(years / age_rate(race))


def grow_older(npc):
    """One real year passes. Returns how many years of life stage they moved on (0, 1 or, for the quick-lived, 2)."""
    race = npc.get("race") or HUMAN
    years = npc.get("years")
    if years is None or abs(stage_for(race, years) - npc["age"]) > 1:   # an age set outright: the years it means
        years = years_for(race, npc["age"])
    years += 1
    npc["years"] = years
    stage = max(npc["age"], stage_for(race, years))
    steps = stage - npc["age"]
    npc["age"] = stage
    return steps


def describe(npc):
    """'a dwarf of 140 years' and the like (stage age only for humans)."""
    race = npc.get("race") or HUMAN
    years = npc.get("years") if npc.get("years") is not None else npc.get("age")
    return f"{race.lower()}, {years} years"


def peoples_of(conn, city_ids):
    """[(people, share)] of the living in these towns, most first."""
    if not city_ids:
        return []
    marks = ",".join("?" * len(city_ids))
    rows = conn.execute(f"""SELECT COALESCE(race, 'Human') r, COUNT(*) n FROM npcs WHERE alive = 1 AND city_id IN ({marks})
                            GROUP BY r ORDER BY n DESC""", list(city_ids)).fetchall()
    total = sum(r[1] for r in rows) or 1
    return [(r[0], r[1] / total) for r in rows]
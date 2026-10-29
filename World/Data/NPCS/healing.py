# Health recovered per day just from rest, for any living, injured NPC.
BASE_HEAL_PER_DAY = 3

# Extra health recovered per day if the NPC's city has an active Temple
# (a Temple building with at least one living Healer/Priest working
# there) -- on top of the base rate, not instead of it.
TEMPLE_HEAL_BONUS = 7


def apply_healing(npc: dict, city_has_active_temple: bool) -> bool:
    """
    Recovers health for a single NPC by one day's worth of rest (plus
    a Temple bonus if their city has one staffed). Mutates npc in
    place and returns True if their health actually changed. Does not
    save or commit -- caller's responsibility, matching every other
    *.py system in this project (aging, employment, family, etc. all
    follow the same "mutate the dict, caller persists it" pattern).
    """

    if not npc["alive"]:
        return False

    if npc["health"] >= 100:
        return False

    heal_amount = BASE_HEAL_PER_DAY + (TEMPLE_HEAL_BONUS if city_has_active_temple else 0)

    npc["health"] = min(100, npc["health"] + heal_amount)

    return True


# Anyone hurt worse than this (combat wounds take 20 to 50 health) drinks a
# Potion from their city's stock if there is one: POTION_HEAL extra health,
# at most one a day.
POTION_HEALTH_THRESHOLD = 80
POTION_HEAL = 25


def drink_potion(npc: dict, city: dict) -> bool:
    """
    A wounded NPC drinks one Potion from their city's stock, if it has any.
    Mutates both the NPC and the city; returns True if a potion was drunk.
    The caller saves both, as with apply_healing.
    """

    if not npc["alive"] or npc["health"] >= POTION_HEALTH_THRESHOLD:
        return False

    stock = city.get("resources", {}).get("Potions", 0)

    if stock < 1:
        return False

    city["resources"]["Potions"] = round(stock - 1, 2)
    npc["health"] = min(100, npc["health"] + POTION_HEAL)

    return True


def city_has_active_temple(city: dict) -> bool:
    """True if this city has a Temple building with at least one
    living NPC working there."""

    return any(
        building["type"] == "Temple" and building["workers"]
        for building in city.get("buildings", [])
    )
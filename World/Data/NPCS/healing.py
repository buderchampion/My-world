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


def city_has_active_temple(city: dict) -> bool:
    """True if this city has a Temple building with at least one
    living NPC working there."""

    return any(
        building["type"] == "Temple" and building["workers"]
        for building in city.get("buildings", [])
    )
# Cost per living military-profession NPC (Guard/Soldier/Captian/
# Knight) per day. Gold comes from the kingdom's treasury; Food and
# Weapons come from the kingdom's capital's stockpile (its central
# supply depot, the same role capitals already play for cross-kingdom
# trade). Weapons wear out and get lost far slower than food gets
# eaten or gold gets spent, hence the much smaller daily rate.
UPKEEP_GOLD_PER_SOLDIER_PER_DAY = 2.0
UPKEEP_FOOD_PER_SOLDIER_PER_DAY = 1.0
UPKEEP_WEAPONS_PER_SOLDIER_PER_DAY = 0.1

# If a kingdom can't afford its full upkeep bill (any of the three),
# this fraction of its military deserts (becomes Unemployed) -- an
# army that isn't fed, armed, and paid doesn't stay together.
DESERTION_FRACTION = 0.05

MILITARY_PROFESSIONS = ("Guard", "Soldier", "Captian", "Knight")


def apply_military_upkeep(conn, kingdom, military_npcs, capital_city, deployed_ids=frozenset()):
    """
    Charges one day of upkeep for a kingdom's standing military:
    gold from the treasury, Food and Weapons from the kingdom's
    capital (mutates capital_city["resources"] in place -- the
    caller is responsible for saving it afterward, same as every
    other function in this codebase that touches a city dict).

    All three costs must be affordable together or nothing gets
    spent at all -- no partial payment -- and some fraction of the
    military deserts instead.

    Soldiers in deployed_ids are away with an army: they are still paid,
    but they eat from and are armed by the army's own wagons, so the capital
    is not charged Food or Weapons for them.

    Returns a dict describing what happened, for logging/testing.
    """

    from Data.Database.repository import spend_treasury, save_npc

    if not military_npcs:
        return {"gold_cost": 0.0, "food_cost": 0.0, "weapons_cost": 0.0, "paid": True, "deserters": 0}

    count = len(military_npcs)
    at_home = sum(1 for npc in military_npcs if npc["id"] not in deployed_ids)

    gold_cost = round(count * UPKEEP_GOLD_PER_SOLDIER_PER_DAY, 2)
    food_cost = round(at_home * UPKEEP_FOOD_PER_SOLDIER_PER_DAY, 2)
    weapons_cost = round(at_home * UPKEEP_WEAPONS_PER_SOLDIER_PER_DAY, 2)

    available_food = capital_city.get("resources", {}).get("Food", 0)
    available_weapons = capital_city.get("resources", {}).get("Weapons", 0)

    can_afford_food = available_food >= food_cost
    can_afford_weapons = available_weapons >= weapons_cost

    if can_afford_food and can_afford_weapons:
        paid = spend_treasury(conn, kingdom["id"], gold_cost)
    else:
        paid = False

    deserters = 0

    if paid:

        capital_city["resources"]["Food"] = round(available_food - food_cost, 2)
        capital_city["resources"]["Weapons"] = round(available_weapons - weapons_cost, 2)

    else:

        desertion_count = max(1, round(count * DESERTION_FRACTION))

        for npc in military_npcs[:desertion_count]:
            npc["profession"] = "Unemployed"
            save_npc(conn, npc)
            deserters += 1

    return {
        "gold_cost": gold_cost, "food_cost": food_cost, "weapons_cost": weapons_cost,
        "paid": paid, "deserters": deserters
    }
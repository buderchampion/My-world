# Gold cost per living military-profession NPC (Guard/Soldier/Captian/
# Knight) per day, deducted from their kingdom's treasury -- upkeep
# (food, equipment, wages beyond their own personal wage) for
# maintaining a standing force. This is deliberately the thing that
# actually spends kingdom treasury, since taxes/market fees only ever
# added to it before this existed.
UPKEEP_PER_SOLDIER_PER_DAY = 2.0

# If a kingdom can't afford its full upkeep bill, this fraction of its
# military deserts (becomes Unemployed) -- an unpaid army shrinks
# itself rather than the kingdom silently going into debt.
DESERTION_FRACTION = 0.05

MILITARY_PROFESSIONS = ("Guard", "Soldier", "Captian", "Knight")


def apply_military_upkeep(conn, kingdom, military_npcs):
    """
    Charges one day of upkeep for a kingdom's standing military
    (every living NPC in a military profession, across the whole
    kingdom) against its treasury. If the kingdom can't afford it,
    some fraction of the military deserts instead -- mutates and
    saves any deserting NPCs directly (unlike most of this codebase's
    functions, since desertion is a side effect specific to this
    system, not something the caller would otherwise persist).

    Returns a dict describing what happened, for logging/testing.
    """

    from Data.Database.repository import spend_treasury, save_npc

    if not military_npcs:
        return {"cost": 0.0, "paid": True, "deserters": 0}

    cost = round(len(military_npcs) * UPKEEP_PER_SOLDIER_PER_DAY, 2)

    if cost <= 0:
        return {"cost": 0.0, "paid": True, "deserters": 0}

    paid = spend_treasury(conn, kingdom["id"], cost)

    deserters = 0

    if not paid:

        desertion_count = max(1, round(len(military_npcs) * DESERTION_FRACTION))

        for npc in military_npcs[:desertion_count]:
            npc["profession"] = "Unemployed"
            save_npc(conn, npc)
            deserters += 1

    return {"cost": cost, "paid": paid, "deserters": deserters}
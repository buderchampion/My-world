from Data.Database.repository import get_npc, get_city_buildings, get_city_rival_pairs, create_quest

# Reward tuning -- how gold value flows from each system into a quest.
BOUNTY_GOLD_PER_POWER = 5
SUPPLY_RUN_QUANTITY = 20
SUPPLY_RUN_REWARD_FRACTION = 0.5
DIPLOMACY_BASE_REWARD = 20

# A city can have a lot of Rival pairs once population gets large --
# cap how many actually become quests, keeping only the worst ones.
MAX_DIPLOMACY_QUESTS = 3


def generate_bounty_quest(conn, city, bandit_camp):
    """
    A Bandit Camp with living occupants becomes a Bounty quest -- reward
    scales with the camp's actual combat power (Data.Combat.combat).
    """

    from Data.Combat.combat import get_combat_power

    workers = [get_npc(conn, npc_id) for npc_id in bandit_camp["workers"]]
    workers = [npc for npc in workers if npc["alive"]]

    power = sum(get_combat_power(npc) for npc in workers)

    return create_quest(
        conn,
        city_id=city["id"],
        type="Bounty",
        title=f"Clear out {bandit_camp['name']}",
        giver_id=city.get("ruler"),
        target_building_id=bandit_camp["id"],
        reward_gold=round(power * BOUNTY_GOLD_PER_POWER, 2)
    )


def generate_supply_quest(conn, shortage_city, surplus_city, resource):
    """
    A city short on a resource, paired with a nearby city that has a
    surplus of it, becomes a Supply Run quest -- reward is based on
    that resource's current price (Data.Economy.economy).
    """

    from Data.Economy.economy import get_price

    price = get_price(resource, shortage_city)

    reward = round(price * SUPPLY_RUN_QUANTITY * SUPPLY_RUN_REWARD_FRACTION, 2)

    return create_quest(
        conn,
        city_id=shortage_city["id"],
        type="Supply Run",
        title=f"Deliver {resource} to {shortage_city['name']}",
        giver_id=shortage_city.get("ruler"),
        from_city_id=surplus_city["id"],
        to_city_id=shortage_city["id"],
        resource=resource,
        quantity=SUPPLY_RUN_QUANTITY,
        reward_gold=reward
    )


def generate_diplomacy_quest(conn, city, npc_a, npc_b, severity):
    """
    Two NPCs with a Rival relationship become a Diplomacy quest --
    reward scales with how bad the rivalry actually is (Data.Relationships).
    """

    return create_quest(
        conn,
        city_id=city["id"],
        type="Diplomacy",
        title=f"Mend the rivalry between {npc_a['name']} and {npc_b['name']}",
        giver_id=city.get("ruler"),
        target_npc_a_id=npc_a["id"],
        target_npc_b_id=npc_b["id"],
        reward_gold=round(DIPLOMACY_BASE_REWARD + severity, 2)
    )


def generate_quests_for_city(conn, city, nearby_cities=None):
    """
    Scans this city's current state -- Bandit Camp threats, resource
    shortages (against nearby_cities' surpluses), and NPC rivalries --
    and generates whatever quests make sense right now. Returns the new
    quests. Not called automatically.
    """

    from Data.Economy.economy import classify_supply

    quests = []

    for building in get_city_buildings(conn, city["id"]):

        if building["type"] == "Bandit Camp" and building["workers"]:
            quests.append(generate_bounty_quest(conn, city, building))

    if nearby_cities:

        for resource in city.get("resources", {}):

            if classify_supply(resource, city) != "Shortage":
                continue

            surplus_city = next(
                (c for c in nearby_cities if classify_supply(resource, c) == "Surplus"),
                None
            )

            if surplus_city:
                quests.append(generate_supply_quest(conn, city, surplus_city, resource))

    rival_pairs = get_city_rival_pairs(conn, city["id"])

    for npc_a_id, npc_b_id, severity in rival_pairs[:MAX_DIPLOMACY_QUESTS]:

        npc_a = get_npc(conn, npc_a_id)
        npc_b = get_npc(conn, npc_b_id)

        quests.append(generate_diplomacy_quest(conn, city, npc_a, npc_b, severity))

    return quests

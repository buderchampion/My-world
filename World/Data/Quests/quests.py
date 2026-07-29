import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent

NPC_FOLDER = DATA_DIR / "NPCS" / "NPCData"

BOUNTY_GOLD_PER_POWER = 5
SUPPLY_RUN_QUANTITY = 20
SUPPLY_RUN_REWARD_FRACTION = 0.5
DIPLOMACY_BASE_REWARD = 20

MAX_DIPLOMACY_QUESTS = 3


def load_npc(npc_id):

    path = NPC_FOLDER / f"npc_{npc_id:06}.json"

    with open(path, "r") as f:
        return json.load(f)


def get_next_quest_id():

    from Data.counters import next_id

    return next_id("quest")


def generate_bounty_quest(city, bandit_camp):

    from Data.Combat.combat import get_combat_power

    workers = [load_npc(npc_id) for npc_id in bandit_camp["workers"]]
    workers = [npc for npc in workers if npc["alive"]]

    power = sum(get_combat_power(npc) for npc in workers)

    return {
        "id": get_next_quest_id(),
        "type": "Bounty",
        "title": f"Clear out {bandit_camp['name']}",
        "giver": city.get("ruler"),
        "target_building": bandit_camp["id"],
        "reward_gold": round(power * BOUNTY_GOLD_PER_POWER, 2),
        "status": "Available"
    }


def generate_supply_quest(shortage_city, surplus_city, resource):

    from Data.Economy.economy import get_price

    price = get_price(resource, shortage_city)

    reward = round(price * SUPPLY_RUN_QUANTITY * SUPPLY_RUN_REWARD_FRACTION, 2)

    return {
        "id": get_next_quest_id(),
        "type": "Supply Run",
        "title": f"Deliver {resource} to {shortage_city['name']}",
        "giver": shortage_city.get("ruler"),
        "from_city": surplus_city["id"],
        "to_city": shortage_city["id"],
        "resource": resource,
        "quantity": SUPPLY_RUN_QUANTITY,
        "reward_gold": reward,
        "status": "Available"
    }


def generate_diplomacy_quest(city, npc_a, npc_b):

    relationship = npc_a["relationships"][str(npc_b["id"])]

    severity = abs(relationship["reputation"])

    return {
        "id": get_next_quest_id(),
        "type": "Diplomacy",
        "title": f"Mend the rivalry between {npc_a['name']} and {npc_b['name']}",
        "giver": city.get("ruler"),
        "target_npcs": [npc_a["id"], npc_b["id"]],
        "reward_gold": round(DIPLOMACY_BASE_REWARD + severity, 2),
        "status": "Available"
    }


def generate_quests_for_city(city, buildings_by_id, nearby_cities=None):

    from Data.Economy.economy import classify_supply

    quests = []

    for building_entry in city["buildings"]:

        if building_entry["type"] != "Bandit Camp":
            continue

        full_building = buildings_by_id.get(building_entry["id"])

        if full_building and full_building["workers"]:
            quests.append(generate_bounty_quest(city, full_building))

    if nearby_cities:

        for resource in city.get("resources", {}):

            if classify_supply(resource, city) != "Shortage":
                continue

            surplus_city = next(
                (c for c in nearby_cities if classify_supply(resource, c) == "Surplus"),
                None
            )

            if surplus_city:
                quests.append(generate_supply_quest(city, surplus_city, resource))

    npcs = [load_npc(entry["id"]) for entry in city["npcs"]]

    seen_pairs = set()
    rival_pairs = []

    for npc in npcs:

        for other_id, relationship in npc.get("relationships", {}).items():

            if relationship["type"] != "Rival":
                continue

            pair = tuple(sorted([npc["id"], int(other_id)]))

            if pair in seen_pairs:
                continue

            seen_pairs.add(pair)

            rival_pairs.append((npc, load_npc(int(other_id)), abs(relationship["reputation"])))

    rival_pairs.sort(key=lambda entry: entry[2], reverse=True)

    for npc_a, npc_b, severity in rival_pairs[:MAX_DIPLOMACY_QUESTS]:

        quests.append(generate_diplomacy_quest(city, npc_a, npc_b))

    city["quests"].extend(quests)

    return quests
import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent

NPC_FOLDER = DATA_DIR / "NPCS" / "NPCData"
BUILDING_FOLDER = DATA_DIR / "Buildings" / "BuildingData"

COMBAT_RATES = DATA_DIR / "Combat" / "CombatRates.json"

DEFENDER_PROFESSIONS = ("Guard", "Soldier", "Captian", "Knight")
RAIDER_PROFESSION = "Bandit"

# Chance of injury/death per participant, split by which side they're on.
WINNER_DEATH_CHANCE = 0.03
WINNER_INJURY_CHANCE = 0.10
LOSER_DEATH_CHANCE = 0.15
LOSER_INJURY_CHANCE = 0.35

INJURY_MIN_DAMAGE = 20
INJURY_MAX_DAMAGE = 50

# Combat xp granted to surviving participants who have skills to train.
WINNER_XP = 15
LOSER_XP = 8

# Fraction of a city's resource stockpile a successful raid steals.
RAID_PLUNDER_FRACTION = 0.25


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def load_npc(npc_id):

    path = NPC_FOLDER / f"npc_{npc_id:06}.json"

    with open(path, "r") as f:
        return json.load(f)


def save_npc(npc):

    path = NPC_FOLDER / f"npc_{npc['id']:06}.json"

    with open(path, "w") as f:
        json.dump(npc, f, indent=4)


def load_building(building_id):

    path = BUILDING_FOLDER / f"building_{building_id:06}.json"

    with open(path, "r") as f:
        return json.load(f)


def save_building(building):

    path = BUILDING_FOLDER / f"building_{building['id']:06}.json"

    with open(path, "w") as f:
        json.dump(building, f, indent=4)


def get_combat_power(npc):

    rates = load_json(COMBAT_RATES)

    base = rates.get(npc["profession"], 1)

    skill_bonus = sum(skill["level"] for skill in npc.get("skills", {}).values())

    return base + skill_bonus


def remove_npc_from_buildings(npc):

    for building_id in (npc.get("workplace"), npc.get("home")):

        if building_id is None:
            continue

        building = load_building(building_id)

        for occupancy_key in ("workers", "residents"):

            if npc["id"] in building.get(occupancy_key, []):
                building[occupancy_key].remove(npc["id"])

        save_building(building)


def apply_casualties(participants, won):

    death_chance = WINNER_DEATH_CHANCE if won else LOSER_DEATH_CHANCE
    injury_chance = WINNER_INJURY_CHANCE if won else LOSER_INJURY_CHANCE

    xp = WINNER_XP if won else LOSER_XP

    for npc in participants:

        roll = random.random()

        if roll < death_chance:

            npc["health"] = 0
            npc["alive"] = False
            remove_npc_from_buildings(npc)

        elif roll < death_chance + injury_chance:

            damage = random.randint(INJURY_MIN_DAMAGE, INJURY_MAX_DAMAGE)
            npc["health"] = max(1, npc["health"] - damage)

            for skill in npc.get("skills", {}).values():

                from Data.NPCS.employment import add_skill_xp

                add_skill_xp(skill, xp)

        else:

            for skill in npc.get("skills", {}).values():

                from Data.NPCS.employment import add_skill_xp

                add_skill_xp(skill, xp)

        save_npc(npc)


def resolve_raid(city, bandit_camp):

    attackers = [load_npc(npc_id) for npc_id in bandit_camp["workers"]]
    attackers = [npc for npc in attackers if npc["alive"]]

    defenders = [
        load_npc(entry["id"]) for entry in city["npcs"]
        if entry["profession"] in DEFENDER_PROFESSIONS
    ]
    defenders = [npc for npc in defenders if npc["alive"]]

    attack_power = sum(get_combat_power(npc) for npc in attackers)
    defense_power = sum(get_combat_power(npc) for npc in defenders)

    total_power = attack_power + defense_power

    if total_power == 0:
        return {"outcome": "no_battle", "reason": "no combatants on either side"}

    attackers_win = random.random() < (attack_power / total_power)

    apply_casualties(attackers, won=attackers_win)
    apply_casualties(defenders, won=not attackers_win)

    plunder = {}

    if attackers_win:

        from Data.Economy.economy import get_price

        survivors = [npc for npc in attackers if npc["alive"]]

        if survivors:

            for resource, amount in city.get("resources", {}).items():

                stolen = round(amount * RAID_PLUNDER_FRACTION, 2)

                if stolen <= 0:
                    continue

                city["resources"][resource] = round(amount - stolen, 2)

                gold_value = round(stolen * get_price(resource, city), 2)

                plunder[resource] = stolen

                share = round(gold_value / len(survivors), 2)

                for npc in survivors:
                    npc["gold"] = round(npc.get("gold", 0) + share, 2)
                    save_npc(npc)

    return {
        "outcome": "attackers_win" if attackers_win else "defenders_win",
        "attack_power": attack_power,
        "defense_power": defense_power,
        "plunder": plunder
    }


def resolve_kingdom_war(attacker_kingdom, defender_kingdom, attacker_cities, defender_cities):
 
    def gather_military(cities):

        military = []

        for city in cities:

            for entry in city["npcs"]:

                if entry["profession"] in DEFENDER_PROFESSIONS:

                    npc = load_npc(entry["id"])

                    if npc["alive"]:
                        military.append(npc)

        return military

    attacker_military = gather_military(attacker_cities)
    defender_military = gather_military(defender_cities)

    attack_power = sum(get_combat_power(npc) for npc in attacker_military)
    defense_power = sum(get_combat_power(npc) for npc in defender_military)

    total_power = attack_power + defense_power

    if total_power == 0:
        return {"outcome": "no_battle", "reason": "no combatants on either side"}

    attacker_wins = random.random() < (attack_power / total_power)

    apply_casualties(attacker_military, won=attacker_wins)
    apply_casualties(defender_military, won=not attacker_wins)

    captured_city = None

    if attacker_wins:

        from Data.Economy.economy import get_price

        capturable = [c for c in defender_cities if c["type"] != "Capital"]

        if capturable:

            def city_value(city):
                return sum(
                    amount * get_price(resource, city)
                    for resource, amount in city.get("resources", {}).items()
                )

            capturable.sort(key=city_value, reverse=True)

            captured_city = capturable[0]

            defender_kingdom["cities"] = [
                c for c in defender_kingdom["cities"] if c["id"] != captured_city["id"]
            ]

            attacker_kingdom["cities"].append({
                "id": captured_city["id"],
                "name": captured_city["name"],
                "type": captured_city["type"]
            })

            captured_city["kingdom"] = attacker_kingdom["id"]

    return {
        "outcome": "attacker_wins" if attacker_wins else "defender_wins",
        "attack_power": attack_power,
        "defense_power": defense_power,
        "captured_city": captured_city["name"] if captured_city else None
    }
import json
import random
from pathlib import Path
from Data.Database.repository import get_npc, save_npc, get_city_npcs, set_city_kingdom

DATA_DIR = Path(__file__).resolve().parent.parent

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

# Fraction of the defender's CURRENT treasury balance that transfers
# to the attacker when a city is successfully captured.
WAR_TREASURY_SPOILS_FRACTION = 0.25

PEACE_TREATY_YEARS = 3

def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def get_combat_power(npc):

    rates = load_json(COMBAT_RATES)

    base = rates.get(npc["profession"], 1)

    skill_bonus = sum(skill["level"] for skill in npc.get("skills", {}).values())

    return base + skill_bonus


def apply_casualties(conn, participants, won):

    death_chance = WINNER_DEATH_CHANCE if won else LOSER_DEATH_CHANCE
    injury_chance = WINNER_INJURY_CHANCE if won else LOSER_INJURY_CHANCE

    xp = WINNER_XP if won else LOSER_XP

    deaths = []

    for npc in participants:

        roll = random.random()

        if roll < death_chance:

            npc["health"] = 0
            npc["alive"] = False

            # Slots free up automatically -- workplace/home occupancy is
            # always derived from these fields, so clearing them IS the
            # "remove from building" step. No building rows to touch.
            npc["workplace"] = None
            npc["home"] = None

            deaths.append(npc)

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

        save_npc(conn, npc)

    conn.commit()

    return deaths


def resolve_raid(conn, city, bandit_camp):
    """
    A Bandit Camp's occupants raid this city. Compares raider strength
    against the city's Guard/Soldier/Captian/Knight defenders, applies
    casualties to both sides, and -- if the raiders win -- steals a
    portion of the city's resources, converted to gold at that
    resource's current price and split among surviving raiders.

    Returns a result dict. Not called automatically. You'll want to
    call Data.Database.repository.save_city(conn, city) afterward to
    persist the plundered resource totals.
    """

    attackers = [get_npc(conn, npc_id) for npc_id in bandit_camp["workers"]]
    attackers = [npc for npc in attackers if npc["alive"]]

    defenders = [
        npc for npc in get_city_npcs(conn, city["id"])
        if npc["profession"] in DEFENDER_PROFESSIONS and npc["alive"]
    ]

    attack_power = sum(get_combat_power(npc) for npc in attackers)
    defense_power = sum(get_combat_power(npc) for npc in defenders)

    total_power = attack_power + defense_power

    if total_power == 0:
        return {"outcome": "no_battle", "reason": "no combatants on either side"}

    attackers_win = random.random() < (attack_power / total_power)

    attacker_deaths = apply_casualties(conn, attackers, won=attackers_win)
    defender_deaths = apply_casualties(conn, defenders, won=not attackers_win)

    from Data.Events.events import death_event
    for npc in attacker_deaths + defender_deaths:
        death_event(conn, npc, "Raid", city_id=city["id"])

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
                    save_npc(conn, npc)

            conn.commit()

    result = {
        "outcome": "attackers_win" if attackers_win else "defenders_win",
        "attack_power": attack_power,
        "defense_power": defense_power,
        "plunder": plunder
    }

    from Data.Events.events import raid_event
    raid_event(conn, city, result)
    conn.commit()

    return result


def resolve_kingdom_war(conn, attacker_kingdom, defender_kingdom, attacker_cities, defender_cities):
    """
    Gathers every Guard/Soldier/Captian/Knight across all of each
    kingdom's cities, resolves one battle between the two militaries,
    applies casualties, and -- if the attacker wins -- captures one of
    the defender's non-capital cities (preferring whichever city holds
    the most valuable resource stockpile, so the war has an actual
    target worth fighting for). The captured city's own resources
    travel with it to the new kingdom.

    Returns a result dict. Not called automatically.
    """

    def gather_military(cities):

        military = []

        for city in cities:

            for npc in get_city_npcs(conn, city["id"]):

                if npc["profession"] in DEFENDER_PROFESSIONS and npc["alive"]:
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

    attacker_deaths = apply_casualties(conn, attacker_military, won=attacker_wins)
    defender_deaths = apply_casualties(conn, defender_military, won=not attacker_wins)

    from Data.Events.events import death_event
    for npc in attacker_deaths:
        death_event(conn, npc, "War", kingdom_id=attacker_kingdom["id"])
    for npc in defender_deaths:
        death_event(conn, npc, "War", kingdom_id=defender_kingdom["id"])

    captured_city = None
    spoils = 0

    if attacker_wins:

        from Data.Economy.economy import total_resource_value

        capturable = [c for c in defender_cities if c["type"] != "Capital"]

        if capturable:

            capturable.sort(key=total_resource_value, reverse=True)

            captured_city = capturable[0]

            set_city_kingdom(conn, captured_city["id"], attacker_kingdom["id"])

            defender_kingdom["cities"] = [
                c for c in defender_kingdom["cities"] if c["id"] != captured_city["id"]
            ]

            attacker_kingdom["cities"].append({
                "id": captured_city["id"],
                "name": captured_city["name"],
                "type": captured_city["type"]
            })

            captured_city["kingdom"] = attacker_kingdom["id"]

            from Data.Database.repository import spend_treasury, add_treasury

            defender_row = conn.execute(
                "SELECT treasury FROM kingdoms WHERE id = ?", (defender_kingdom["id"],)
            ).fetchone()
            spoils = round(defender_row["treasury"] * WAR_TREASURY_SPOILS_FRACTION, 2)

            if spoils > 0:
                spend_treasury(conn, defender_kingdom["id"], spoils)
                add_treasury(conn, attacker_kingdom["id"], spoils)

    from Data.Database.repository import set_kingdom_relation

    current_year_row = conn.execute(
        "SELECT clock_year FROM worlds WHERE id = ?", (attacker_kingdom["world_id"],)
    ).fetchone()
    current_year = current_year_row["clock_year"] if current_year_row else 1

    set_kingdom_relation(
        conn, attacker_kingdom["id"], defender_kingdom["id"],
        "Peace Treaty", peace_until_year=current_year + PEACE_TREATY_YEARS
    )

    result = {
        "outcome": "attacker_wins" if attacker_wins else "defender_wins",
        "attack_power": attack_power,
        "defense_power": defense_power,
        "captured_city": captured_city["name"] if captured_city else None,
        "treasury_spoils": spoils
    }

    from Data.Events.events import war_event
    war_event(conn, attacker_kingdom, defender_kingdom, result)
    conn.commit()

    return result

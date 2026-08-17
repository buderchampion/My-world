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

MORALE_IMPACT = {
    "raid_repelled": {"stress": 5, "happiness": -3},
    "raid_succeeded": {"stress": 15, "happiness": -10},
    "war_won":  {"stress": 10, "happiness": -3},
    "war_lost": {"stress": 20, "happiness": -15},
    "civil_war_rebels_won": {"stress": -30, "happiness": 10},
    "civil_war_loyalists_won": {"stress": -20, "happiness": -10}
}

CIVIL_WAR_STRESS_THRESHOLD = 75
CIVIL_WAR_HAPPINESS_THRESHOLD = 25
CIVIL_WAR_WEAPONS_FRACTION_OF_POPULATION = 0.08
CIVIL_WAR_REBEL_PARTICIPATION_RATE = 0.15
CIVIL_WAR_ARMED_REBEL_BONUS =  2
CIVIL_WAR_CHANCE_IF_ELIGIBLE = 0.15
CIVIL_WAR_COOLDOWN_YEARS = 15
CIVIL_WAR_TREASURY_DAMAGE_FRACTION = 0.20

def apply_morale_impact(conn, npcs, impact_key):
    impact = MORALE_IMPACT[impact_key]

    for npc in npcs:

        if not npc["alive"]:
            continue

        npc["needs"]["stress"] = max(0, min(100, npc["needs"]["stress"] + impact["stress"]))
        npc["needs"]["happiness"] = max(0, min(100, npc["needs"]["happiness"] + impact["happiness"]))

        save_npc(conn, npc)

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
    from Data.Relationships.relationships import apply_grief
    for npc in attacker_deaths + defender_deaths:
        death_event(conn, npc, "Raid", city_id=city["id"])
        apply_grief(conn, npc)

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

    city_population = get_city_npcs(conn, city["id"])
    apply_morale_impact(conn, city_population, "raid_succeeded" if attackers_win else "raid_repelled")

    from Data.Relationships.relationships import evolve_relationships_from_battle, form_relationships_from_battle
    evolve_relationships_from_battle(
        conn, attackers, defenders,
        same_side_origin=f"survived the raid on {city['name']} together",
        opposite_side_origin=f"ended up on opposite sides during the raid on {city['name']}"
    )
    form_relationships_from_battle(
        conn, attackers, defenders,
        origin=f"met and bonded during the raid on {city['name']}"
    )    

    from Data.Events.events import raid_event
    raid_event(conn, city, result, participants=[npc["id"] for npc in attackers + defenders])
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
    from Data.Relationships.relationships import apply_grief
    for npc in attacker_deaths:
        death_event(conn, npc, "War", kingdom_id=attacker_kingdom["id"])
        apply_grief(conn, npc)
    for npc in defender_deaths:
        death_event(conn, npc, "War", kingdom_id=defender_kingdom["id"])
        apply_grief(conn, npc)

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

    def gather_population(cities):
        population = []
        for city in cities:
            population.extend(get_city_npcs(conn, city["id"]))
        return population

    apply_morale_impact(conn, gather_population(attacker_cities), "war_won" if attacker_wins else "war_lost")
    apply_morale_impact(conn, gather_population(defender_cities), "war_lost" if attacker_wins else "war_won")

    from Data.Relationships.relationships import evolve_relationships_from_battle, form_relationships_from_battle
    evolve_relationships_from_battle(
        conn, attacker_military, defender_military,
        same_side_origin=f"fought together in the war between {attacker_kingdom['name']} and {defender_kingdom['name']}",
        opposite_side_origin=f"fought on opposite sides of the war between {attacker_kingdom['name']} and {defender_kingdom['name']}"
    )
    form_relationships_from_battle(
        conn, attacker_military, defender_military,
        origin=f"met and bonded fighting in the war between {attacker_kingdom['name']} and {defender_kingdom['name']}"
    )

    from Data.Events.events import war_event
    war_event(conn, attacker_kingdom, defender_kingdom, result,
              participants=[npc["id"] for npc in attacker_military + defender_military]
              )
    conn.commit()

    return result

def check_civil_war_eligibility(conn, kingdom, cities, current_year):
    """
    A kingdom is eligible for civil war this year only if ALL of:
    - it's not still on cooldown from a previous civil war
    - its population's average stress is at or above
      CIVIL_WAR_STRESS_THRESHOLD
    - its population's average happiness is at or below
      CIVIL_WAR_HAPPINESS_THRESHOLD
    - its capital holds enough Weapons to arm a real fraction of the
      population (see CIVIL_WAR_WEAPONS_FRACTION_OF_POPULATION)

    Meeting all four doesn't mean a civil war happens -- see
    CIVIL_WAR_CHANCE_IF_ELIGIBLE, rolled by the caller.

    Returns a dict with "eligible": bool plus the underlying numbers,
    for logging/testing.
    """

    last_civil_war_year = kingdom.get("last_civil_war_year")

    if last_civil_war_year is not None and current_year - last_civil_war_year < CIVIL_WAR_COOLDOWN_YEARS:
        return {"eligible": False, "reason": "cooldown"}

    living = [npc for city in cities for npc in get_city_npcs(conn, city["id"]) if npc["alive"]]

    if not living:
        return {"eligible": False, "reason": "no population"}

    avg_stress = sum(npc["needs"]["stress"] for npc in living) / len(living)
    avg_happiness = sum(npc["needs"]["happiness"] for npc in living) / len(living)

    capital_city = next((c for c in cities if c["id"] == kingdom.get("capital")), None)
    available_weapons = capital_city["resources"].get("Weapons", 0) if capital_city else 0

    weapons_needed = len(living) * CIVIL_WAR_WEAPONS_FRACTION_OF_POPULATION

    eligible = (
        avg_stress >= CIVIL_WAR_STRESS_THRESHOLD
        and avg_happiness <= CIVIL_WAR_HAPPINESS_THRESHOLD
        and available_weapons >= weapons_needed
    )

    return {
        "eligible": eligible,
        "avg_stress": round(avg_stress, 1),
        "avg_happiness": round(avg_happiness, 1),
        "available_weapons": available_weapons,
        "weapons_needed": round(weapons_needed, 1),
    }


def resolve_civil_war(conn, kingdom, cities, current_year):
    """
    Loyalist military (Guard/Soldier/Captian/Knight) vs. armed rebels
    drawn from the rest of the population, armed by whatever Weapons
    are sitting in the kingdom's capital (consumed in the process --
    handing out weapons is a one-way trip). Applies casualties to
    both sides, damages the treasury, releases the population's
    stress (the pressure breaks either way), and -- if the rebels
    win -- clears the capital's ruler, which the existing monthly
    succession check will fill on its own next month.

    Not called automatically -- the caller should already have
    confirmed eligibility via check_civil_war_eligibility and rolled
    CIVIL_WAR_CHANCE_IF_ELIGIBLE first.

    Returns a result dict.
    """

    living = [npc for city in cities for npc in get_city_npcs(conn, city["id"]) if npc["alive"]]

    loyalists = [npc for npc in living if npc["profession"] in DEFENDER_PROFESSIONS]
    civilians = [npc for npc in living if npc["profession"] not in DEFENDER_PROFESSIONS]

    capital_city = next((c for c in cities if c["id"] == kingdom.get("capital")), None)
    available_weapons = capital_city["resources"].get("Weapons", 0) if capital_city else 0

    desired_rebels = round(len(living) * CIVIL_WAR_REBEL_PARTICIPATION_RATE)
    armed_rebel_count = min(int(available_weapons), desired_rebels, len(civilians))

    random.shuffle(civilians)
    rebels = civilians[:armed_rebel_count]

    if capital_city is not None and armed_rebel_count > 0:
        capital_city["resources"]["Weapons"] = round(available_weapons - armed_rebel_count, 2)

    loyalist_power = sum(get_combat_power(npc) for npc in loyalists)
    rebel_power = sum(get_combat_power(npc) + CIVIL_WAR_ARMED_REBEL_BONUS for npc in rebels)

    total_power = loyalist_power + rebel_power

    if total_power == 0:
        return {"outcome": "no_battle", "reason": "no combatants on either side"}

    rebels_win = random.random() < (rebel_power / total_power)

    loyalist_deaths = apply_casualties(conn, loyalists, won=not rebels_win)
    rebel_deaths = apply_casualties(conn, rebels, won=rebels_win)

    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    for npc in loyalist_deaths + rebel_deaths:
        death_event(conn, npc, "Civil War", kingdom_id=kingdom["id"])
        apply_grief(conn, npc)

    apply_morale_impact(conn, living, "civil_war_rebels_won" if rebels_win else "civil_war_loyalists_won")

    from Data.Relationships.relationships import evolve_relationships_from_battle, form_relationships_from_battle
    evolve_relationships_from_battle(
        conn, loyalists, rebels,
        same_side_origin=f"stood together in {kingdom['name']}'s civil war",
        opposite_side_origin=f"found themselves on opposite sides of {kingdom['name']}'s civil war"
    )
    form_relationships_from_battle(
        conn, loyalists, rebels,
        origin=f"met and bonded standing together in {kingdom['name']}'s civil war"
    )

    ruler_overthrown = False

    if rebels_win and capital_city is not None:
        conn.execute("UPDATE cities SET ruler_id = NULL WHERE id = ?", (capital_city["id"],))
        ruler_overthrown = True

    from Data.Database.repository import spend_treasury, set_last_civil_war_year

    treasury_row = conn.execute("SELECT treasury FROM kingdoms WHERE id = ?", (kingdom["id"],)).fetchone()
    treasury_damage = round(treasury_row["treasury"] * CIVIL_WAR_TREASURY_DAMAGE_FRACTION, 2)

    if treasury_damage > 0:
        spend_treasury(conn, kingdom["id"], treasury_damage)

    set_last_civil_war_year(conn, kingdom["id"], current_year)

    result = {
        "outcome": "rebels_win" if rebels_win else "loyalists_win",
        "loyalist_power": loyalist_power,
        "rebel_power": rebel_power,
        "rebel_count": len(rebels),
        "ruler_overthrown": ruler_overthrown,
        "treasury_damage": treasury_damage,
    }

    from Data.Events.events import civil_war_event
    civil_war_event(conn, kingdom, result, participants=[npc["id"] for npc in loyalists + rebels])
    conn.commit()

    return result
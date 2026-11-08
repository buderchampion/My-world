import random

# Campaigns. When a kingdom goes to war (Data.Government.diplomacy chooses
# whom, each year), it no longer fights one instant battle: it raises an army
# and marches on a real town.
#
#   1. The army musters at the capital: two thirds of the kingdom's free
#      soldiers (a third stays home on guard), plus levies -- one young man
#      for every HOUSEHOLDS_PER_LEVY households in the crown's own villages and
#      in each county whose lord is loyal enough to answer the call (a
#      restless lord sends no one).
#   2. It marches by road on the enemy's richest town it can reach, eating
#      from its wagons, its supply caravans following (Data.Government.armies,
#      Data.Government.supply).
#   3. Arriving, it fights there: against the town's garrison and every enemy
#      soldier close enough to rally -- within RALLY_DAYS' march of the town.
#      The aftermath is any war's (Data.Combat.combat.resolve_kingdom_war):
#      victory takes the town -- and, if it is a county seat, the whole county
#      with its lord -- and plunders the enemy's treasury; both sides
#      chronicle it, mourn their dead, and make a peace treaty.
#   4. The wounded drink the army's potions; then the army marches home and
#      disbands, its levies going back to their fields.
#
# Sieges. Capitals and towns with a Castle are walled: behind standing walls
# the defenders fight at WALL_DEFENSE times their strength. An army reaching a
# walled town besieges it instead of attacking: the town is blockaded (no
# trade in or out), and each day the army builds siege works -- slowly by
# labor, four times faster with Tools, which it carries for the purpose. The
# siege ends in an assault when the walls are breached (they no longer help)
# or the town has had no Food for STARVE_DAYS (its starving garrison fights at
# half strength). An army that has besieged for MAX_SIEGE_DAYS, or gone
# GIVE_UP_HUNGRY_DAYS without food, lifts the siege and goes home.

MARCH_SHARE = 2 / 3
HOUSEHOLDS_PER_LEVY = 20
RALLY_DAYS = 5
POTION_HEAL, POTION_BELOW = 25, 80        # a wounded soldier below this health drinks one potion for this much

WALL_DEFENSE = 2.0               # defenders behind standing walls fight at this many times their strength
WALL_STRENGTH = 1000             # siege works needed to breach the walls
LABOR_PER_SOLDIER = 0.25         # siege works a soldier builds a day by labor alone
WORKS_PER_TOOL = 20              # ...and each Tool spent adds this much (four times a soldier's day per 20 soldiers' tool)
TOOLS_PER_SOLDIER_DAY = 0.05     # an army spends this many Tools a soldier a day while besieging
SIEGE_TOOLS_PER_SOLDIER = 1.0    # a campaign army carries this many Tools a soldier
STARVE_DAYS = 10                 # a town with no Food this many days in a row is assaulted, its garrison starving
STARVING_STRENGTH = 0.5          # a starving garrison fights at this share of its strength
MAX_SIEGE_DAYS = 120
GIVE_UP_HUNGRY_DAYS = 7


def capital_of(kingdom):
    """A kingdom's capital city id -- from a game kingdom record ('capital') or a raw row ('capital_city_id')."""
    return kingdom.get("capital_city_id") or kingdom.get("capital")


def walled(conn, city_id):
    """Capitals and towns with a Castle have walls."""
    row = conn.execute("SELECT type FROM cities WHERE id = ?", (city_id,)).fetchone()
    if row and row["type"] == "Capital":
        return True
    return bool(conn.execute("SELECT 1 FROM buildings WHERE city_id = ? AND type = 'Castle' LIMIT 1", (city_id,)).fetchone())


def besieged_city_ids(conn, world_id):
    """Towns under siege right now: blockaded, so no trade goes in or out."""
    return {row[0] for row in conn.execute(
        """SELECT c.target_city_id FROM campaigns c JOIN kingdoms k ON k.id = c.attacker_kingdom_id
           WHERE k.world_id = ? AND c.status = 'Besieging'""", (world_id,))}


def active_campaign(conn, kingdom_id):
    row = conn.execute("SELECT * FROM campaigns WHERE attacker_kingdom_id = ? AND status != 'Over'", (kingdom_id,)).fetchone()
    return dict(row) if row else None


def choose_target(conn, attacker, defender):
    """The defender's richest non-capital town the attacker's army can reach by road, or None."""
    from Data.Government.armies import find_route
    from Data.Database.repository import get_city
    from Data.Economy.economy import total_resource_value
    towns = [get_city(conn, row["id"]) for row in conn.execute(
        "SELECT id FROM cities WHERE kingdom_id = ? AND id != ?", (defender["id"], capital_of(defender) or -1))]
    reachable = [t for t in towns if find_route(conn, capital_of(attacker), t["id"])]
    return max(reachable, key=total_resource_value) if reachable else None


def raise_levies(conn, kingdom_id):
    """Young men from the crown's villages and from every county whose lord answers the call. Returns their ids."""
    from Data.Government.feudal import RESTLESS_BELOW
    from Data.World.villagers import make_individual
    villages = conn.execute(
        """SELECT c.id, c.households FROM cities c LEFT JOIN provinces p ON p.id = c.province_id
           WHERE c.kingdom_id = ? AND c.households >= 1 AND (c.province_id IS NULL OR p.loyalty >= ?)""",
        (kingdom_id, RESTLESS_BELOW)).fetchall()
    levies = []
    for village in villages:
        for _ in range(int(village["households"] // HOUSEHOLDS_PER_LEVY)):
            levies.append(make_individual(conn, village["id"], village["id"], "Soldier",
                                          age=random.randint(16, 40), gender="Male", needs_workplace=False)["id"])
    return levies


def start_campaign(conn, attacker, defender, day):
    """
    The attacker raises an army and marches on the defender's richest
    reachable town. Returns the campaign (None if one is already under way,
    there is no town to reach, or no one to send).
    """

    from Data.Government.armies import raise_army, march_army, get_army
    from Data.Infrastructure.travel import get_traveling_npc_ids
    from Data.Government.military import MILITARY_PROFESSIONS
    from Data.Database.repository import get_city, create_event

    if active_campaign(conn, attacker["id"]):
        return None
    target = choose_target(conn, attacker, defender)
    if target is None:
        return None

    away = get_traveling_npc_ids(conn, attacker["world_id"])
    marks = ",".join("?" * len(MILITARY_PROFESSIONS))
    free = [row[0] for row in conn.execute(
        f"""SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks})""", (attacker["id"], *MILITARY_PROFESSIONS))
        if row[0] not in away]
    marching = int(round(len(free) * MARCH_SHARE))
    levies = raise_levies(conn, attacker["id"])
    if marching + len(levies) == 0:
        return None

    capital = get_city(conn, capital_of(attacker))
    army = raise_army(conn, attacker, capital, size=marching, recruits=levies,
                      name=f"Army of {attacker['name']} against {target['name']}",
                      tools_per_soldier=SIEGE_TOOLS_PER_SOLDIER)
    if army is None:
        return None
    march_army(conn, army["id"], target["id"])

    cursor = conn.execute(
        """INSERT INTO campaigns (attacker_kingdom_id, defender_kingdom_id, army_id, target_city_id, status, started_day)
           VALUES (?, ?, ?, ?, 'Marching', ?)""", (attacker["id"], defender["id"], army["id"], target["id"], day))
    create_event(conn, "Campaign",
                 f"{attacker['name']} marched on {defender['name']}'s town of {target['name']} with "
                 f"{marching} soldiers and {len(levies)} levies.", city_id=target["id"], kingdom_id=attacker["id"])
    conn.commit()
    return dict(conn.execute("SELECT * FROM campaigns WHERE id = ?", (cursor.lastrowid,)).fetchone())


def rally(conn, defender_id, target_city_id):
    """The defender's soldiers who can reach the town in time: its garrison and
    everyone within RALLY_DAYS' march. Soldiers away with an army do not come."""
    from Data.Combat.combat import DEFENDER_PROFESSIONS
    from Data.Government.armies import find_route, get_army_npc_ids
    from Data.Infrastructure.travel import get_travel_days
    from Data.Database.repository import get_npc

    world_id = conn.execute("SELECT world_id FROM kingdoms WHERE id = ?", (defender_id,)).fetchone()[0]
    away = get_army_npc_ids(conn, world_id)
    near = []
    for city in conn.execute("SELECT id FROM cities WHERE kingdom_id = ?", (defender_id,)).fetchall():
        if city["id"] == target_city_id:
            near.append(city["id"])
            continue
        route = find_route(conn, city["id"], target_city_id)
        if route:
            stops = [city["id"]] + route
            if sum(get_travel_days(conn, a, b) for a, b in zip(stops, stops[1:])) <= RALLY_DAYS:
                near.append(city["id"])
    marks_p = ",".join("?" * len(DEFENDER_PROFESSIONS))
    marks_c = ",".join("?" * len(near))
    ids = [row[0] for row in conn.execute(
        f"SELECT id FROM npcs WHERE alive = 1 AND profession IN ({marks_p}) AND city_id IN ({marks_c})",
        (*DEFENDER_PROFESSIONS, *near))] if near else []
    return [get_npc(conn, i) for i in ids if i not in away]


def fight(conn, campaign, defender_bonus=1.0):
    """The army assaults the town: the battle, and its aftermath. defender_bonus
    multiplies the defenders' strength (standing walls, a starving garrison).
    Returns the war's result."""

    from Data.Combat.combat import resolve_kingdom_war
    from Data.Government.armies import get_army, get_army_npc_ids
    from Data.Database.repository import get_npc, get_kingdom, get_kingdom_cities, get_city, save_npc

    attacker = get_kingdom(conn, campaign["attacker_kingdom_id"])
    defender = get_kingdom(conn, campaign["defender_kingdom_id"])
    target = get_city(conn, campaign["target_city_id"])
    if target["kingdom"] != defender["id"]:
        return {"outcome": "no_battle", "reason": "the town had already changed hands"}

    members = [r[0] for r in conn.execute("SELECT npc_id FROM army_members WHERE army_id = ?", (campaign["army_id"],))]
    attackers = [n for n in (get_npc(conn, i) for i in members) if n and n["alive"]]
    defenders = rally(conn, defender["id"], target["id"])

    # armor: the army's from its wagons; the defenders' from the towns they came from
    from Data.Combat.combat import armor_share, city_armor
    army = get_army(conn, campaign["army_id"])
    attacker_armor = armor_share(army["armor"] if army else 0, len(attackers))
    defender_armor = armor_share(city_armor(conn, {d["city_id"] for d in defenders}), len(defenders))

    result = resolve_kingdom_war(conn, attacker, defender,
                                 get_kingdom_cities(conn, attacker["id"]), get_kingdom_cities(conn, defender["id"]),
                                 attacker_military=attackers, defender_military=defenders, target_city=target,
                                 attacker_armor=attacker_armor, defender_armor=defender_armor,
                                 defender_bonus=defender_bonus)

    # the fallen's armor is lost with them
    if army and result.get("attacker_deaths"):
        lost = min(army["armor"], result["attacker_deaths"] * attacker_armor)
        conn.execute("UPDATE armies SET armor = armor - ? WHERE id = ?", (round(lost, 2), army["id"]))

    # the wounded drink the army's potions
    army = get_army(conn, campaign["army_id"])
    potions = army["potions"] if army else 0
    for npc_id in members:
        npc = get_npc(conn, npc_id)
        if potions >= 1 and npc and npc["alive"] and npc["health"] < POTION_BELOW:
            npc["health"] = min(100, npc["health"] + POTION_HEAL)
            save_npc(conn, npc)
            potions -= 1
    if army:
        conn.execute("UPDATE armies SET potions = ? WHERE id = ?", (round(potions, 2), army["id"]))

    result["attackers"], result["defenders"] = len(attackers), len(defenders)
    return result


def advance_campaigns(conn, world_id, day):
    """
    Daily, after the armies move: an army that has reached its target fights
    and turns for home; one that is home again disbands. Returns what happened.
    """

    from Data.Government.armies import get_army, march_army, disband_army
    from Data.Database.repository import create_event

    happened = []
    for campaign in conn.execute(
        """SELECT c.* FROM campaigns c JOIN kingdoms k ON k.id = c.attacker_kingdom_id
           WHERE k.world_id = ? AND c.status != 'Over'""", (world_id,)).fetchall():
        campaign = dict(campaign)
        army = get_army(conn, campaign["army_id"])
        alive = conn.execute(
            "SELECT COUNT(*) FROM army_members m JOIN npcs n ON n.id = m.npc_id WHERE m.army_id = ? AND n.alive = 1",
            (campaign["army_id"],)).fetchone()[0] if army else 0

        if not army or army["status"] == "Disbanded" or alive == 0:
            if army and army["status"] != "Disbanded":
                disband_army(conn, army["id"])
            conn.execute("UPDATE campaigns SET status = 'Over', outcome = COALESCE(outcome, 'lost in the field') WHERE id = ?",
                         (campaign["id"],))
            happened.append((campaign["id"], "over"))
            continue

        if campaign["status"] == "Marching" and army["status"] == "Encamped" and army["location_city_id"] == campaign["target_city_id"]:
            if walled(conn, campaign["target_city_id"]):
                conn.execute("UPDATE campaigns SET status = 'Besieging', siege_day = ?, siege_progress = 0, starving_days = 0 WHERE id = ?",
                             (day, campaign["id"]))
                town = conn.execute("SELECT name FROM cities WHERE id = ?", (campaign["target_city_id"],)).fetchone()["name"]
                create_event(conn, "Siege", f"{army['name']} laid siege to the walled town of {town}.",
                             city_id=campaign["target_city_id"], kingdom_id=campaign["attacker_kingdom_id"])
                happened.append((campaign["id"], "siege begun"))
                continue
            result = fight(conn, campaign)
            conn.execute("UPDATE campaigns SET status = 'Returning', battle_day = ?, outcome = ? WHERE id = ?",
                         (day, result["outcome"], campaign["id"]))
            march_army(conn, army["id"], army["home_city_id"])
            happened.append((campaign["id"], result["outcome"]))

        elif campaign["status"] == "Besieging":
            happened.append((campaign["id"], siege_day_passes(conn, campaign, army, alive, day)))

        elif campaign["status"] == "Returning" and army["status"] == "Encamped" and army["location_city_id"] == army["home_city_id"]:
            disband_army(conn, army["id"])
            conn.execute("UPDATE campaigns SET status = 'Over' WHERE id = ?", (campaign["id"],))
            create_event(conn, "Campaign", f"{army['name']} came home and was disbanded.", kingdom_id=campaign["attacker_kingdom_id"])
            happened.append((campaign["id"], "home"))

    conn.commit()
    return happened


def siege_day_passes(conn, campaign, army, soldiers, day):
    """
    One day of a siege: the army builds siege works (spending Tools), the
    blockaded town eats into its stores, and the siege may end -- in an
    assault on breached walls or a starving garrison, or by being lifted.
    Returns what happened.
    """

    from Data.Government.armies import march_army
    from Data.Database.repository import create_event

    town_id = campaign["target_city_id"]
    town = conn.execute("SELECT name, kingdom_id FROM cities WHERE id = ?", (town_id,)).fetchone()

    def end_siege(outcome):
        conn.execute("UPDATE campaigns SET status = 'Returning', battle_day = ?, outcome = ? WHERE id = ?",
                     (day, outcome, campaign["id"]))
        march_army(conn, army["id"], army["home_city_id"])
        return outcome

    if town["kingdom_id"] != campaign["defender_kingdom_id"]:
        return end_siege("no_battle")                      # the town changed hands some other way

    # siege works: labor, and Tools
    tools_used = min(army["tools"], TOOLS_PER_SOLDIER_DAY * soldiers)
    progress = campaign["siege_progress"] + LABOR_PER_SOLDIER * soldiers + WORKS_PER_TOOL * tools_used
    conn.execute("UPDATE armies SET tools = tools - ? WHERE id = ?", (round(tools_used, 2), army["id"]))

    # the blockaded town's stores
    food = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM city_resources WHERE city_id = ? AND resource_name = 'Food'",
                        (town_id,)).fetchone()[0]
    starving = campaign["starving_days"] + 1 if food < 1 else 0
    conn.execute("UPDATE campaigns SET siege_progress = ?, starving_days = ? WHERE id = ?",
                 (round(progress, 2), starving, campaign["id"]))

    if progress >= WALL_STRENGTH:
        create_event(conn, "Siege", f"The walls of {town['name']} were breached; the besiegers stormed the town.",
                     city_id=town_id, kingdom_id=campaign["attacker_kingdom_id"])
        return end_siege(fight(conn, dict(campaign, siege_progress=progress), defender_bonus=1.0)["outcome"])

    if starving >= STARVE_DAYS:
        create_event(conn, "Siege", f"{town['name']}, starved by the siege, was stormed; its hungry garrison could barely fight.",
                     city_id=town_id, kingdom_id=campaign["attacker_kingdom_id"])
        return end_siege(fight(conn, campaign, defender_bonus=WALL_DEFENSE * STARVING_STRENGTH)["outcome"])

    if day - campaign["siege_day"] >= MAX_SIEGE_DAYS or army["days_without_food"] >= GIVE_UP_HUNGRY_DAYS:
        create_event(conn, "Siege", f"The siege of {town['name']} was lifted; the besiegers turned for home.",
                     city_id=town_id, kingdom_id=campaign["attacker_kingdom_id"])
        return end_siege("siege lifted")

    return "besieging"
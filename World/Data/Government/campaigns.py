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

MARCH_SHARE = 2 / 3
HOUSEHOLDS_PER_LEVY = 20
RALLY_DAYS = 5
POTION_HEAL, POTION_BELOW = 25, 80        # a wounded soldier below this health drinks one potion for this much


def capital_of(kingdom):
    """A kingdom's capital city id -- from a game kingdom record ('capital') or a raw row ('capital_city_id')."""
    return kingdom.get("capital_city_id") or kingdom.get("capital")


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
                      name=f"Army of {attacker['name']} against {target['name']}")
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


def fight(conn, campaign):
    """The army has reached the town: the battle, and its aftermath. Returns the war's result."""

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

    result = resolve_kingdom_war(conn, attacker, defender,
                                 get_kingdom_cities(conn, attacker["id"]), get_kingdom_cities(conn, defender["id"]),
                                 attacker_military=attackers, defender_military=defenders, target_city=target)

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
            result = fight(conn, campaign)
            conn.execute("UPDATE campaigns SET status = 'Returning', battle_day = ?, outcome = ? WHERE id = ?",
                         (day, result["outcome"], campaign["id"]))
            march_army(conn, army["id"], army["home_city_id"])
            happened.append((campaign["id"], result["outcome"]))

        elif campaign["status"] == "Returning" and army["status"] == "Encamped" and army["location_city_id"] == army["home_city_id"]:
            disband_army(conn, army["id"])
            conn.execute("UPDATE campaigns SET status = 'Over' WHERE id = ?", (campaign["id"],))
            create_event(conn, "Campaign", f"{army['name']} came home and was disbanded.", kingdom_id=campaign["attacker_kingdom_id"])
            happened.append((campaign["id"], "home"))

    conn.commit()
    return happened
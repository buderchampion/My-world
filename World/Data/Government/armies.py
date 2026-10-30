import heapq
import json

# An army is ONE record -- its roster, the supplies in its wagons, and where
# it is on the road network -- not hundreds of individual travelers. Nothing
# sends armies to war yet; that is Step 4's campaigns. This is the machinery
# they will use.

# Each soldier eats this much Food a day from the army's wagons -- the same
# ration a soldier at home draws from the capital as upkeep.
ARMY_FOOD_PER_SOLDIER_PER_DAY = 1.0

# A day's ration restores this much hunger. Marching costs about 32 hunger a
# day (the "Traveling" needs effect, four times a day), so a fed army stays
# fed; an army without food gets hungrier by the day, and the ordinary
# starvation rules (Data.NPCS.starvation) take it from there.
ARMY_MEAL_HUNGER = 40

# What an army takes from its home city when raised, per soldier, as far as
# the city's stock allows.
DEFAULT_FOOD_DAYS = 30
DEFAULT_WEAPONS_PER_SOLDIER = 1.0
DEFAULT_POTIONS_PER_SOLDIER = 0.1


def find_route(conn, from_city_id, to_city_id):
    """
    The quickest road route between two cities, as the list of cities to
    pass through after the start (ending with the destination); [] if they
    are the same city, None if no road connects them at all.
    """

    if from_city_id == to_city_id:
        return []

    neighbors = {}
    for road in conn.execute("SELECT city_a_id, city_b_id, travel_days FROM roads"):
        neighbors.setdefault(road["city_a_id"], []).append((road["city_b_id"], road["travel_days"]))
        neighbors.setdefault(road["city_b_id"], []).append((road["city_a_id"], road["travel_days"]))

    best = {from_city_id: 0}
    came_from = {}
    queue = [(0, from_city_id)]

    while queue:
        days, city = heapq.heappop(queue)
        if city == to_city_id:
            break
        if days > best.get(city, float("inf")):
            continue
        for other, road_days in neighbors.get(city, []):
            total = days + road_days
            if total < best.get(other, float("inf")):
                best[other] = total
                came_from[other] = city
                heapq.heappush(queue, (total, other))

    if to_city_id not in best:
        return None

    route = [to_city_id]
    while route[-1] in came_from and came_from[route[-1]] != from_city_id:
        route.append(came_from[route[-1]])

    return list(reversed(route))


def get_army(conn, army_id):
    row = conn.execute("SELECT * FROM armies WHERE id = ?", (army_id,)).fetchone()
    if row is None:
        return None
    army = dict(row)
    army["route"] = json.loads(army["route"] or "[]")
    army["members"] = [r["npc_id"] for r in conn.execute("SELECT npc_id FROM army_members WHERE army_id = ?", (army_id,))]
    return army


def get_army_npc_ids(conn, world_id):
    """Everyone serving in an army in this world. They are away from home:
    travel.get_traveling_npc_ids includes them, so they skip their normal
    schedule and are never sent on holiday or to emigrate."""

    rows = conn.execute(
        """
        SELECT m.npc_id FROM army_members m
        JOIN armies a ON a.id = m.army_id
        JOIN kingdoms k ON k.id = a.kingdom_id
        WHERE k.world_id = ? AND a.status != 'Disbanded'
        """,
        (world_id,)
    ).fetchall()

    return {row["npc_id"] for row in rows}


def raise_army(conn, kingdom, home_city, size=None, name=None,
               food_days=DEFAULT_FOOD_DAYS,
               weapons_per_soldier=DEFAULT_WEAPONS_PER_SOLDIER,
               potions_per_soldier=DEFAULT_POTIONS_PER_SOLDIER):
    """
    Musters an army at home_city (a city dict of this kingdom) from the
    kingdom's living soldiers who are not already away, up to `size` (all of
    them if None), and loads its wagons from home_city's stock -- as much as
    the city has, up to the amounts asked for. Mutates and saves home_city,
    so pass the city's live dict. Returns the new army, or None if the
    kingdom has no soldier free to serve.
    """

    from Data.Database.repository import save_city
    from Data.Government.military import MILITARY_PROFESSIONS
    from Data.Infrastructure.travel import get_traveling_npc_ids

    away = get_traveling_npc_ids(conn, kingdom["world_id"])
    placeholders = ",".join("?" * len(MILITARY_PROFESSIONS))

    candidates = [
        row["id"] for row in conn.execute(
            f"""
            SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({placeholders})
            ORDER BY n.id
            """,
            (kingdom["id"], *MILITARY_PROFESSIONS)
        )
        if row["id"] not in away
    ]

    soldiers = candidates if size is None else candidates[:size]

    if not soldiers:
        return None

    n = len(soldiers)
    stock = home_city.setdefault("resources", {})

    def take(resource, wanted):
        amount = round(min(stock.get(resource, 0), wanted), 2)
        stock[resource] = round(stock.get(resource, 0) - amount, 2)
        return amount

    food = take("Food", n * food_days * ARMY_FOOD_PER_SOLDIER_PER_DAY)
    weapons = take("Weapons", n * weapons_per_soldier)
    potions = take("Potions", n * potions_per_soldier)

    save_city(conn, home_city)

    count = conn.execute("SELECT COUNT(*) FROM armies WHERE kingdom_id = ?", (kingdom["id"],)).fetchone()[0]
    name = name or f"Host of {kingdom['name']} ({count + 1})"

    cursor = conn.execute(
        """
        INSERT INTO armies (kingdom_id, name, status, home_city_id, location_city_id, food, weapons, potions)
        VALUES (?, ?, 'Mustered', ?, ?, ?, ?, ?)
        """,
        (kingdom["id"], name, home_city["id"], home_city["id"], food, weapons, potions)
    )
    army_id = cursor.lastrowid

    conn.executemany("INSERT INTO army_members (army_id, npc_id) VALUES (?, ?)", [(army_id, s) for s in soldiers])
    conn.commit()

    return get_army(conn, army_id)


def march_army(conn, army_id, destination_city_id):
    """
    Sets an army marching toward a city along the quickest road route. An
    army already there simply encamps. Returns False (and changes nothing)
    if the army is disbanded or no road leads there.
    """

    from Data.Infrastructure.travel import get_travel_days

    army = get_army(conn, army_id)

    if army is None or army["status"] == "Disbanded":
        return False

    start = army["location_city_id"]
    route = find_route(conn, start, destination_city_id)

    if route is None:
        return False

    if not route:
        conn.execute(
            "UPDATE armies SET status = 'Encamped', next_city_id = NULL, leg_days_remaining = 0, route = '[]', destination_city_id = ? WHERE id = ?",
            (destination_city_id, army_id)
        )
    else:
        next_city = route[0]
        conn.execute(
            """
            UPDATE armies SET status = 'Marching', next_city_id = ?, leg_days_remaining = ?, route = ?, destination_city_id = ?
            WHERE id = ?
            """,
            (next_city, get_travel_days(conn, start, next_city), json.dumps(route[1:]), destination_city_id, army_id)
        )

    conn.commit()
    return True


def advance_armies(conn, world_id):
    """
    Daily, for every army still in the field: its soldiers eat from the
    wagons (a fed army's hunger is restored; an unfed one goes without), and
    a marching army covers one more day of road, entering each city on its
    route as it reaches it and encamping at its destination.

    Hunger is changed in the database directly, so call this after the day's
    per-NPC loop has saved its own copies of everyone (as on_day does), or
    those older copies would overwrite it. Returns what happened, for
    logging and testing.
    """

    from Data.Infrastructure.travel import get_travel_days

    report = {"fed": [], "unfed": [], "arrived": []}

    armies = conn.execute(
        """
        SELECT a.id FROM armies a JOIN kingdoms k ON k.id = a.kingdom_id
        WHERE k.world_id = ? AND a.status != 'Disbanded'
        """,
        (world_id,)
    ).fetchall()

    for row in armies:

        army = get_army(conn, row["id"])
        living = [
            r["id"] for r in conn.execute(
                f"SELECT id FROM npcs WHERE alive = 1 AND id IN ({','.join('?' * len(army['members'])) or 'NULL'})",
                army["members"]
            )
        ]

        # --- supper from the wagons
        need = len(living) * ARMY_FOOD_PER_SOLDIER_PER_DAY

        if living and army["food"] >= need:
            army["food"] = round(army["food"] - need, 2)
            army["days_without_food"] = 0
            conn.execute(
                f"UPDATE npcs SET needs_hunger = MIN(100, needs_hunger + ?) WHERE id IN ({','.join('?' * len(living))})",
                (ARMY_MEAL_HUNGER, *living)
            )
            report["fed"].append(army["id"])
        elif living:
            army["food"] = 0.0
            army["days_without_food"] += 1
            report["unfed"].append(army["id"])

        # --- a day's march
        if army["status"] == "Marching":

            army["leg_days_remaining"] -= 1

            if army["leg_days_remaining"] <= 0:
                army["location_city_id"] = army["next_city_id"]

                if army["route"]:
                    army["next_city_id"] = army["route"].pop(0)
                    army["leg_days_remaining"] = get_travel_days(conn, army["location_city_id"], army["next_city_id"])
                else:
                    army["status"] = "Encamped"
                    army["next_city_id"] = None
                    army["leg_days_remaining"] = 0
                    report["arrived"].append(army["id"])

        conn.execute(
            """
            UPDATE armies SET status = ?, location_city_id = ?, next_city_id = ?, leg_days_remaining = ?, route = ?,
                              food = ?, days_without_food = ?
            WHERE id = ?
            """,
            (army["status"], army["location_city_id"], army["next_city_id"], army["leg_days_remaining"],
             json.dumps(army["route"]), army["food"], army["days_without_food"], army["id"])
        )

    conn.commit()
    return report


def disband_army(conn, army_id):
    """
    Disbands an army standing (not marching) in a city of its own kingdom:
    what is left in its wagons goes into that city's stock and its soldiers
    go home. Returns False, changing nothing, for an army that is marching,
    abroad, or already disbanded.

    Adds to the city's stock in the database directly, so -- like
    advance_armies -- call it outside any loop that will later save an
    older copy of that city.
    """

    army = get_army(conn, army_id)

    if army is None or army["status"] in ("Disbanded", "Marching"):
        return False

    city = conn.execute("SELECT kingdom_id FROM cities WHERE id = ?", (army["location_city_id"],)).fetchone()

    if city is None or city["kingdom_id"] != army["kingdom_id"]:
        return False

    for resource, amount in (("Food", army["food"]), ("Weapons", army["weapons"]), ("Potions", army["potions"])):
        if amount > 0:
            conn.execute(
                """
                INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, ?, ?)
                ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount
                """,
                (army["location_city_id"], resource, amount)
            )

    conn.execute("DELETE FROM army_members WHERE army_id = ?", (army_id,))
    conn.execute(
        "UPDATE armies SET status = 'Disbanded', food = 0, weapons = 0, potions = 0, next_city_id = NULL, route = '[]' WHERE id = ?",
        (army_id,)
    )
    conn.commit()
    return True
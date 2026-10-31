import random

# Supply lines: an army in the field is fed from home. When its wagons run
# low, its kingdom's capital sends a supply caravan with Food, Weapons and
# Potions, which follows the army along the roads -- re-planning at every
# city, since an army may have moved on -- and loads its wagons when it
# catches up. Raiding a supply caravan is how a supply line is cut.

# An army in the field asks for supplies when its Food falls below this many
# days for its soldiers, and is sent enough to bring it back up to
# RESUPPLY_TO_DAYS (with Weapons and Potions topped up to the amounts an army
# is raised with). One caravan per army at a time.
RESUPPLY_BELOW_DAYS = 10
RESUPPLY_TO_DAYS = 30

# A distant army asks earlier: when its Food falls below the caravan's
# travel time from the capital plus this margin, if that is more than
# RESUPPLY_BELOW_DAYS -- otherwise it would starve before help arrived.
RESUPPLY_SAFETY_DAYS = 5

# The capital never sends away its last few days of its own Food.
HOME_FOOD_RESERVE_DAYS = 3

# The same daily chance of being raided as a trade caravan
# (Data.Infrastructure.caravans.CARAVAN_RAID_CHANCE_PER_DAY).
from Data.Infrastructure.caravans import CARAVAN_RAID_CHANCE_PER_DAY as SUPPLY_RAID_CHANCE_PER_DAY

ACTIVE = ("En Route", "Returning")


def get_supply_caravan(conn, caravan_id):
    row = conn.execute("SELECT * FROM supply_caravans WHERE id = ?", (caravan_id,)).fetchone()
    return dict(row) if row else None


def _living_members(conn, army):
    if not army["members"]:
        return []
    return [
        row["id"] for row in conn.execute(
            f"SELECT id FROM npcs WHERE alive = 1 AND id IN ({','.join('?' * len(army['members']))})",
            army["members"]
        )
    ]


def resupply_threshold_days(conn, capital_id, army):
    """How many days of Food left should make this army send home for more:
    RESUPPLY_BELOW_DAYS, or the caravan's journey from the capital plus
    RESUPPLY_SAFETY_DAYS if the army is far enough away for that to be more."""

    from Data.Government.armies import find_route
    from Data.Infrastructure.travel import get_travel_days

    target = army["next_city_id"] if army["status"] == "Marching" else army["location_city_id"]
    route = find_route(conn, capital_id, target) or []
    stops = [capital_id] + route
    journey = sum(get_travel_days(conn, a, b) for a, b in zip(stops, stops[1:]))

    return max(RESUPPLY_BELOW_DAYS, journey + RESUPPLY_SAFETY_DAYS)


def dispatch_supplies(conn, army):
    """
    If this army is in the field, has no supply caravan already on the way,
    and is below RESUPPLY_BELOW_DAYS of Food, its kingdom's capital sends one
    -- as much as the capital can spare of what the army is short. Returns
    the new caravan, or None if none was sent.
    """

    from Data.Database.repository import get_city, save_city, get_kingdom
    from Data.Government.armies import ARMY_FOOD_PER_SOLDIER_PER_DAY, DEFAULT_WEAPONS_PER_SOLDIER, DEFAULT_POTIONS_PER_SOLDIER

    if army["status"] == "Disbanded":
        return None

    in_flight = conn.execute(
        f"SELECT COUNT(*) FROM supply_caravans WHERE army_id = ? AND status IN ({','.join('?' * len(ACTIVE))})",
        (army["id"], *ACTIVE)
    ).fetchone()[0]

    if in_flight:
        return None

    soldiers = len(_living_members(conn, army))

    if soldiers == 0:
        return None

    daily = soldiers * ARMY_FOOD_PER_SOLDIER_PER_DAY
    capital = get_city(conn, get_kingdom(conn, army["kingdom_id"])["capital"])

    if army["food"] >= daily * resupply_threshold_days(conn, capital["id"], army):
        return None

    stock = capital["resources"]
    reserve = capital["population"] * HOME_FOOD_RESERVE_DAYS

    def spare(resource, wanted, keep=0.0):
        amount = round(max(0.0, min(wanted, stock.get(resource, 0) - keep)), 2)
        stock[resource] = round(stock.get(resource, 0) - amount, 2)
        return amount

    food = spare("Food", daily * RESUPPLY_TO_DAYS - army["food"], keep=reserve)
    weapons = spare("Weapons", soldiers * DEFAULT_WEAPONS_PER_SOLDIER - army["weapons"])
    potions = spare("Potions", soldiers * DEFAULT_POTIONS_PER_SOLDIER - army["potions"])

    if food <= 0 and weapons <= 0 and potions <= 0:
        return None

    save_city(conn, capital)

    cursor = conn.execute(
        """
        INSERT INTO supply_caravans (army_id, origin_city_id, status, location_city_id, food, weapons, potions)
        VALUES (?, ?, 'En Route', ?, ?, ?, ?)
        """,
        (army["id"], capital["id"], capital["id"], food, weapons, potions)
    )
    conn.commit()

    return get_supply_caravan(conn, cursor.lastrowid)


def _deposit(conn, city_id, caravan):
    for resource in ("Food", "Weapons", "Potions"):
        amount = caravan[resource.lower()]
        if amount > 0:
            conn.execute(
                """
                INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, ?, ?)
                ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount
                """,
                (city_id, resource, amount)
            )


def _save(conn, caravan):
    conn.execute(
        """
        UPDATE supply_caravans SET status = ?, location_city_id = ?, next_city_id = ?, leg_days_remaining = ?,
                                   food = ?, weapons = ?, potions = ?
        WHERE id = ?
        """,
        (caravan["status"], caravan["location_city_id"], caravan["next_city_id"], caravan["leg_days_remaining"],
         caravan["food"], caravan["weapons"], caravan["potions"], caravan["id"])
    )


def _step(conn, caravan, report):
    """One day for one caravan: deliver if it is with its army, otherwise
    choose its next road if between legs, then travel a day."""

    from Data.Government.armies import get_army, find_route
    from Data.Infrastructure.travel import get_travel_days

    for _ in range(2):   # a caravan may arrive and deliver on the same day

        army = get_army(conn, caravan["army_id"])

        if caravan["status"] == "En Route" and (army is None or army["status"] == "Disbanded" or not _living_members(conn, army)):
            caravan["status"] = "Returning"

        if caravan["status"] == "Returning":
            target = caravan["origin_city_id"]
        elif army["status"] == "Marching":
            target = army["next_city_id"]      # meet it where it is going
        else:
            target = army["location_city_id"]

        if caravan["leg_days_remaining"] == 0:

            if caravan["location_city_id"] == target:

                if caravan["status"] == "Returning":
                    _deposit(conn, caravan["origin_city_id"], caravan)
                    caravan["status"] = "Returned"
                    report["returned"].append(caravan["id"])
                    return

                if army["status"] != "Marching":
                    conn.execute(
                        "UPDATE armies SET food = food + ?, weapons = weapons + ?, potions = potions + ? WHERE id = ?",
                        (caravan["food"], caravan["weapons"], caravan["potions"], army["id"])
                    )
                    caravan["status"] = "Delivered"
                    report["delivered"].append(caravan["id"])
                    return

                return      # waiting where the marching army is headed

            route = find_route(conn, caravan["location_city_id"], target)

            if route is None:
                caravan["status"] = "Returning"
                continue

            caravan["next_city_id"] = route[0]
            caravan["leg_days_remaining"] = get_travel_days(conn, caravan["location_city_id"], route[0])

        if caravan.get("_moved"):
            return

        caravan["leg_days_remaining"] -= 1
        caravan["_moved"] = True

        if caravan["leg_days_remaining"] > 0:
            return

        caravan["location_city_id"] = caravan["next_city_id"]
        caravan["next_city_id"] = None


def advance_supply_lines(conn, world_id):
    """
    Daily: every army in the field that is running low sends home for
    supplies, and every supply caravan on the road may be raided, travels a
    day, and delivers to its army -- or, if the army is gone, turns back and
    returns its cargo home.

    Changes city stocks and armies' wagons in the database directly, so --
    like advance_armies -- call it after the day's city loops have saved
    their own copies. Returns what happened, for logging and testing.
    """

    from Data.Government.armies import get_army
    from Data.Database.repository import create_event

    report = {"dispatched": [], "delivered": [], "raided": [], "returned": []}

    for row in conn.execute(
        """
        SELECT a.id FROM armies a JOIN kingdoms k ON k.id = a.kingdom_id
        WHERE k.world_id = ? AND a.status != 'Disbanded'
        """,
        (world_id,)
    ).fetchall():
        caravan = dispatch_supplies(conn, get_army(conn, row["id"]))
        if caravan:
            report["dispatched"].append(caravan["id"])

    caravans = conn.execute(
        f"""
        SELECT s.* FROM supply_caravans s
        JOIN armies a ON a.id = s.army_id JOIN kingdoms k ON k.id = a.kingdom_id
        WHERE k.world_id = ? AND s.status IN ({','.join('?' * len(ACTIVE))})
        """,
        (world_id, *ACTIVE)
    ).fetchall()

    for row in caravans:

        caravan = dict(row)

        if random.random() < SUPPLY_RAID_CHANCE_PER_DAY:
            army = get_army(conn, caravan["army_id"])
            lost = [f"{caravan[k]:.0f} {name}" for k, name in (("food", "Food"), ("weapons", "Weapons"), ("potions", "Potions"))
                    if caravan[k] >= 0.5]
            lost_text = (", ".join(lost[:-1]) + " and " + lost[-1]) if len(lost) > 1 else (lost[0] if lost else "nothing")
            create_event(
                conn, "Supply Raided",
                f"A supply caravan bound for {army['name'] if army else 'an army'} was raided on the road, losing {lost_text}.",
                city_id=caravan["location_city_id"], kingdom_id=army["kingdom_id"] if army else None
            )
            caravan["status"] = "Raided"
            report["raided"].append(caravan["id"])
        else:
            _step(conn, caravan, report)

        _save(conn, caravan)

    conn.commit()
    return report
import random

# Bandits strike near their camps (Bandit Camp buildings with bandits in them):
# each day, a caravan within BANDIT_REACH of such a camp is raided with
# BANDIT_CHANCE for each camp near enough. Far from any camp -- the deep wilds,
# where the beasts are (Data.Monsters.travelers) -- bandits do not reach.
BANDIT_REACH = 300
BANDIT_CHANCE = 0.005
FALLBACK_TRAVEL_DAYS = 5


def bandit_camps(conn, world_id):
    """Where bandits are: the map positions of every Bandit Camp with living bandits in it."""
    return [(r[0], r[1]) for r in conn.execute(
        """SELECT c.map_x, c.map_y FROM buildings b JOIN cities c ON c.id = b.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND b.type = 'Bandit Camp'
             AND EXISTS (SELECT 1 FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1)""", (world_id,))]


def bandit_chance(camps, point):
    """Today's chance that bandits strike a traveller at `point`: BANDIT_CHANCE for each camp within reach."""
    if point is None:
        return 0.0
    x, y = point
    near = sum(1 for cx, cy in camps if (cx - x) ** 2 + (cy - y) ** 2 <= BANDIT_REACH ** 2)
    return 1 - (1 - BANDIT_CHANCE) ** near


def get_travel_days(conn, city_a_id, city_b_id):
    a_id, b_id = sorted((city_a_id, city_b_id))
    row = conn.execute(
        "SELECT travel_days FROM roads WHERE city_a_id = ? AND city_b_id = ?", (a_id, b_id)
    ).fetchone()
    return row["travel_days"] if row else FALLBACK_TRAVEL_DAYS


def dispatch_caravan(conn, origin_city_id, destination_city_id, resource, amount,
                      buyer_kingdom_id, seller_kingdom_id):
    from Data.Database.repository import create_caravan
    from Data.Government.campaigns import besieging_army
    if besieging_army(conn, destination_city_id):
        # no caravan sets out for a besieged town: the goods stay where they were
        conn.execute("""INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, ?, ?)
                        ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount""",
                     (origin_city_id, resource, amount))
        return None
    travel_days = get_travel_days(conn, origin_city_id, destination_city_id)
    return create_caravan(
        conn, origin_city_id, destination_city_id, resource, amount,
        buyer_kingdom_id, seller_kingdom_id, travel_days
    )


# Caravans and sieges (Data.Government.campaigns): a caravan is not sent to a
# besieged town, and one that arrives at a town besieged since it set out is
# seized by the besieging army -- what an army carries goes into its wagons,
# anything else is sold as loot, its worth to the besieging kingdom.
ARMY_SUPPLIES = {"Food": "food", "Weapons": "weapons", "Potions": "potions", "Armor": "armor", "Tools": "tools"}


def seize_at_siege(conn, caravan):
    """If the caravan's destination is besieged, the besiegers take its goods. Returns True if they did."""
    from Data.Government.campaigns import besieging_army
    from Data.Database.repository import set_caravan_status, get_city, create_event
    from Data.Economy.economy import get_price
    siege = besieging_army(conn, caravan["destination_city_id"])
    if siege is None:
        return False
    resource, amount = caravan["resource"], caravan["amount"]
    town = get_city(conn, caravan["destination_city_id"])
    if resource in ARMY_SUPPLIES:
        column = ARMY_SUPPLIES[resource]
        conn.execute(f"UPDATE armies SET {column} = {column} + ? WHERE id = ?", (amount, siege["id"]))
        taken = f"{amount:.0f} {resource} into its wagons"
    else:
        worth = round(amount * get_price(resource, town), 2)
        conn.execute("UPDATE kingdoms SET treasury = treasury + ? WHERE id = ?", (worth, siege["attacker_kingdom_id"]))
        taken = f"{amount:.0f} {resource}, sold as loot for {worth:.0f} gold"
    set_caravan_status(conn, caravan["id"], "Seized")
    create_event(conn, "Seized", f"A caravan bound for besieged {town['name']} was seized by {siege['name']}: {taken}.",
                 city_id=town["id"], kingdom_id=siege["attacker_kingdom_id"])
    return True


def advance_caravans(conn, world_id):
    from Data.Database.repository import (
        get_active_caravans, get_city, save_city, set_caravan_status, decrement_caravan_days
    )
    from Data.Events.events import caravan_raided_event

    delivered = []
    raided = []
    seized = []

    from Data.Monsters.travelers import _positions, _along
    camps = bandit_camps(conn, world_id)
    towns, road_days = _positions(conn, world_id)

    for caravan in get_active_caravans(conn, world_id):

        here = _along(towns, road_days, caravan["origin_city_id"], caravan["destination_city_id"], caravan["days_remaining"])
        if random.random() < bandit_chance(camps, here):
            set_caravan_status(conn, caravan["id"], "Raided")
            origin = get_city(conn, caravan["origin_city_id"])
            destination = get_city(conn, caravan["destination_city_id"])
            caravan_raided_event(conn, caravan, origin["name"], destination["name"])
            raided.append(caravan)
            continue

        remaining = caravan["days_remaining"] - 1

        if remaining <= 0 and seize_at_siege(conn, caravan):
            seized.append(caravan)              # arrived at a besieged town: the besiegers took it
            continue

        if remaining <= 0:
            destination = get_city(conn, caravan["destination_city_id"])
            destination["resources"][caravan["resource"]] = round(
                destination["resources"].get(caravan["resource"], 0) + caravan["amount"], 2
            )
            save_city(conn, destination)
            set_caravan_status(conn, caravan["id"], "Delivered")
            delivered.append(caravan)
        else:
            decrement_caravan_days(conn, caravan["id"], remaining)

    return {"delivered": delivered, "raided": raided, "seized": seized}
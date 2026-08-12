import random

CARAVAN_RAID_CHANCE_PER_DAY = 0.03
FALLBACK_TRAVEL_DAYS = 5


def get_travel_days(conn, city_a_id, city_b_id):
    a_id, b_id = sorted((city_a_id, city_b_id))
    row = conn.execute(
        "SELECT travel_days FROM roads WHERE city_a_id = ? AND city_b_id = ?", (a_id, b_id)
    ).fetchone()
    return row["travel_days"] if row else FALLBACK_TRAVEL_DAYS


def dispatch_caravan(conn, origin_city_id, destination_city_id, resource, amount,
                      buyer_kingdom_id, seller_kingdom_id):
    from Data.Database.repository import create_caravan
    travel_days = get_travel_days(conn, origin_city_id, destination_city_id)
    return create_caravan(
        conn, origin_city_id, destination_city_id, resource, amount,
        buyer_kingdom_id, seller_kingdom_id, travel_days
    )


def advance_caravans(conn, world_id):
    from Data.Database.repository import (
        get_active_caravans, get_city, save_city, set_caravan_status, decrement_caravan_days
    )
    from Data.Events.events import caravan_raided_event

    delivered = []
    raided = []

    for caravan in get_active_caravans(conn, world_id):

        if random.random() < CARAVAN_RAID_CHANCE_PER_DAY:
            set_caravan_status(conn, caravan["id"], "Raided")
            origin = get_city(conn, caravan["origin_city_id"])
            destination = get_city(conn, caravan["destination_city_id"])
            caravan_raided_event(conn, caravan, origin["name"], destination["name"])
            raided.append(caravan)
            continue

        remaining = caravan["days_remaining"] - 1

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

    return {"delivered": delivered, "raided": raided}
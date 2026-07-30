import random
from Data.Kingdoms.kingdom_generator import generate_kingdom
from Data.Database.database import init_database, get_connection
from Data.Database.repository import create_world, get_world_kingdoms


def generate_world():

    init_database()

    conn = get_connection()

    world = create_world(conn, "Generated World")

    number_of_kingdoms = random.randint(3, 6)

    for _ in range(number_of_kingdoms):
        generate_kingdom(conn, world["id"])

    world["kingdoms"] = [
        {"id": k["id"], "name": k["name"]} for k in get_world_kingdoms(conn, world["id"])
    ]

    conn.commit()

    conn.close()

    return world

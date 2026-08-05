import random
from Data.Kingdoms.kingdom_generator import generate_kingdom, KINGDOM_COLORS
from Data.Database.database import init_database, get_connection
from Data.Database.repository import create_world, get_world_kingdoms
from Data.Infrastructure.roads import generate_roads


def generate_world():

    init_database()

    conn = get_connection()

    world = create_world(conn, "Generated World")

    number_of_kingdoms = random.randint(3, 6)

    colors = random.sample(KINGDOM_COLORS, number_of_kingdoms)

    for color in colors:
        generate_kingdom(conn, world["id"], color)

    world["kingdoms"] = [
        {"id": k["id"], "name": k["name"]} for k in get_world_kingdoms(conn, world["id"])
    ]

    generate_roads(conn, world["id"])

    conn.commit()

    conn.close()

    return world

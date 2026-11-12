import random
from Data.Kingdoms.kingdom_generator import generate_kingdom, KINGDOM_COLORS
from Data.Database.database import init_database, get_connection
from Data.Database.repository import create_world, get_world_kingdoms
from Data.Infrastructure.roads import generate_roads


def generate_world(seed=None):
    """Generates a world. The same seed always generates the same world
    (Data.Infrastructure.seeds); without one, a fresh seed is chosen and recorded."""

    from Data.Infrastructure.seeds import new_seed, apply_seed
    seed = apply_seed(new_seed() if seed is None else seed)

    init_database()

    conn = get_connection()

    world = create_world(conn, "Generated World")
    conn.execute("UPDATE worlds SET seed = ? WHERE id = ?", (seed, world["id"]))
    world["seed"] = seed

    # The land comes first, so kingdoms can be placed where it can feed them.
    from Data.World.regions import ensure_regions
    ensure_regions(conn, world["id"])

    number_of_kingdoms = random.randint(3, 6)

    colors = random.sample(KINGDOM_COLORS, number_of_kingdoms)

    # about a quarter of the kingdoms are merchant republics (Data.Government.republic)
    from Data.Government.republic import republic_count
    republics = set(random.sample(range(number_of_kingdoms), republic_count(number_of_kingdoms)))

    for index, color in enumerate(colors):
        generate_kingdom(conn, world["id"], color, "Republic" if index in republics else "Crown")

    world["kingdoms"] = [
        {"id": k["id"], "name": k["name"]} for k in get_world_kingdoms(conn, world["id"])
    ]

    generate_roads(conn, world["id"])

    # Put every settlement into the region it sits in (the regions themselves
    # were made above) -- see Data.World.regions.
    ensure_regions(conn, world["id"])

    # Each kingdom's towns become counties, and its villages fall under the
    # nearest seat or the crown (Data.Government.provinces). Their lords are
    # installed with the first rulers, in the first month.
    from Data.Government.provinces import ensure_provinces
    ensure_provinces(conn, world["id"])

    # Cities open with stocked storehouses, not empty ones -- see
    # Data.Economy.economy.stock_starting_supplies.
    from Data.Economy.economy import stock_starting_supplies
    stock_starting_supplies(conn, world["id"])

    # Village households start with enough to eat until their first harvest.
    from Data.World.harvest import ensure_household_stores
    ensure_household_stores(conn, world["id"])

    # Adults start with a month's food money (Data.Economy.food_market).
    from Data.Economy.food_market import give_starting_savings
    give_starting_savings(conn, world["id"])

    conn.commit()

    conn.close()

    return world
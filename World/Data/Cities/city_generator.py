from pathlib import Path
import json
import random
from Data.NPCS.npc_generator import generate_npc
from Data.Buildings.building_generator import generate_buildings
from Data.Database.repository import create_city, get_city

DATA_DIR = Path(__file__).resolve().parent.parent

PREFIXES = DATA_DIR / "Names" / "CityPrefixes.json"
SUFFIXES = DATA_DIR / "Names" / "CitySuffixes.json"
POPULATION = DATA_DIR / "Population.json"


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def generate_city_name(conn=None, kingdom_id=None):
    """A town's name: in its kingdom's people's tongue (Data.NPCS.races), and -- given the
    kingdom -- one no other town in the world already bears, while any are left."""

    taken, race = set(), "Human"
    if conn is not None and kingdom_id is not None:
        from Data.NPCS.races import kingdom_race
        race = kingdom_race(conn, kingdom_id)
        taken = {r[0] for r in conn.execute(
            """SELECT c.name FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = (SELECT world_id FROM kingdoms WHERE id = ?)""", (kingdom_id,))}

    from Data.NPCS.races import place_name
    name = place_name(race, taken)
    if name:
        return name

    prefixes = load_json(PREFIXES)
    suffixes = load_json(SUFFIXES)

    for _ in range(100):
        name = random.choice(prefixes) + random.choice(suffixes)
        if name not in taken:
            break
    return name


def generate_city(conn, kingdom_id, city_type, map_x=0, map_y=0, biases=None, population=None):

    # population = how many individual NPCs to create. Villages are given
    # theirs (their named villagers -- see Data.World.countryside); everything
    # else rolls it from Population.json.
    if population is None:
        population_data = load_json(POPULATION)
        population = random.randint(
            population_data[city_type]["min"],
            population_data[city_type]["max"]
        )

    city = create_city(conn, kingdom_id, generate_city_name(conn, kingdom_id), city_type, population, map_x, map_y)

    # Buildings are rolled first so employment can be checked against
    # their real capacity as NPCs are generated.
    generate_buildings(conn, city["id"], city_type, population, biases)

    for _ in range(population):
        generate_npc(conn, city["id"], city_type)

    # Re-fetch: pulls in every building/npc/resource generated above.
    return get_city(conn, city["id"])
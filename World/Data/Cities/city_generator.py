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


def generate_city_name():

    prefixes = load_json(PREFIXES)
    suffixes = load_json(SUFFIXES)

    return random.choice(prefixes) + random.choice(suffixes)


def generate_city(conn, kingdom_id, city_type, map_x=0, map_y=0):

    population_data = load_json(POPULATION)

    population = random.randint(
        population_data[city_type]["min"],
        population_data[city_type]["max"]
    )

    city = create_city(conn, kingdom_id, generate_city_name(), city_type, population, map_x, map_y)

    # Buildings are rolled first so employment can be checked against
    # their real capacity as NPCs are generated.
    generate_buildings(conn, city["id"], city_type, population)

    for _ in range(population):
        generate_npc(conn, city["id"], city_type)

    # Re-fetch: pulls in every building/npc/resource generated above.
    return get_city(conn, city["id"])

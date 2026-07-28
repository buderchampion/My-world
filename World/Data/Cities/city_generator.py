from pathlib import Path
import json
import random
from Data.NPCS.npc_generator import generate_npc
from Data.Buildings.building_generator import generate_buildings, save_buildings
from Data.counters import next_id as _next_id

DATA_DIR = Path(__file__).resolve().parent.parent

CITY_FOLDER = DATA_DIR / "Cities" / "CityData"

TEMPLATE = DATA_DIR / "Templates" / "City_Template.json"

PREFIXES = DATA_DIR / "Names" / "CityPrefixes.json"

SUFFIXES = DATA_DIR / "Names" / "CitySuffixes.json"

POPULATION = DATA_DIR / "Population.json"

CITY_FOLDER.mkdir(exist_ok=True)

def load_json(file):

    with open(file, "r")as f:
        return json.load(f)

def get_next_city_id():

    return _next_id("city", CITY_FOLDER, "city_*.json")

def generate_city_name():

    prefixes = load_json(PREFIXES)
    suffixes = load_json(SUFFIXES)

    return random.choice(prefixes) + random.choice(suffixes)

def save_city(city):

    filename = CITY_FOLDER / f"city_{city['id']:06}.json"

    with open(filename, "w") as f:
        json.dump(city, f, indent=4)

def generate_city(kingdom_id, city_type):

    city = load_json(TEMPLATE)

    city["id"] = get_next_city_id()

    city["name"] = generate_city_name()

    city["kingdom"] = kingdom_id

    population_data = load_json(POPULATION)

    city["population"] = random.randint(
        population_data[city_type]["min"],
        population_data[city_type]["max"]
    )

    buildings = generate_buildings(city["id"], city_type, city["population"])

    for _ in range(city["population"]):

        npc = generate_npc(city_type, buildings)

        city["npcs"].append(
            {
                "id": npc["id"],
                "name": npc["name"],
                "profession": npc["profession"],
                "class": npc["class"],
                "workplace": npc["workplace"]
            }
        )

    save_buildings(buildings)

    city["buildings"] = [
        {
            "id": building["id"],
            "name": building["name"],
            "type": building["type"],
            "capacity": building["capacity"],
            "workers": len(building["workers"]),
            "residents": len(building["residents"])
        }

        for building in buildings
    ]

    city["type"] = city_type

    save_city(city)

    return city
from pathlib import Path
import json
import random
from Data.Cities.city_generator import generate_city
from Data.counters import next_id as _next_id


DATA_DIR = Path(__file__).resolve().parent.parent

KINGDOM_FOLDER = DATA_DIR / "Kingdoms" / "KingdomsData"

TEMPLATE = DATA_DIR / "Templates" / "Kingdom_Template.json"

PREFIXES = DATA_DIR / "Names" / "KingdomPrefixes.json"

SUFFIXES = DATA_DIR / "Names" / "KingdomSuffixes.json"

KINGDOM_FOLDER.mkdir(exist_ok=True)

def load_json(file):

    with open(file, "r") as f:
        return json.load(f)

def get_next_kingdom_id():
    return _next_id("kingdom", KINGDOM_FOLDER, "kingdom_*.json")

def generate_kingdom_name():

    prefixes = load_json(PREFIXES)
    suffixes = load_json(SUFFIXES)

    return random.choice(prefixes) + random.choice(suffixes)

def save_kingdom(kingdom):

    filename = KINGDOM_FOLDER / f"kingdom_{kingdom['id']:06}.json"

    with open(filename, "w") as f:
        json.dump(kingdom, f, indent=4)

def generate_kingdom():

    kingdom = load_json(TEMPLATE)

    kingdom["id"] = get_next_kingdom_id()

    kingdom["name"] = generate_kingdom_name()

    capital = generate_city(kingdom["id"], "Capital")

    kingdom["capital"] = capital["name"]

    kingdom["cities"].append(
        {
            "id": capital["id"],
            "name": capital["name"],
            "type": capital["type"]
            }
        )

    for _ in range(random.randint(2,5)):

        town = generate_city(
            kingdom["id"],
            "Town"
        )
        kingdom["cities"].append(
            {
                "id": town["id"],
                "name": town["name"],
                "type": town["type"]
            }
        )

    for _ in range(random.randint(5,10)):

        village = generate_city(
            kingdom["id"],
            "Village"
        )
        kingdom["cities"].append(
            {
                "id": village["id"],
                "name": village["name"],
                "type": village["type"]
            }
        )

    kingdom["population"] = 0

    save_kingdom(kingdom)

    return kingdom
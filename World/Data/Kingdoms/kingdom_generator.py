from pathlib import Path
import json
import random
from Data.Cities.city_generator import generate_city
from Data.Database.repository import create_kingdom, get_kingdom, save_kingdom

DATA_DIR = Path(__file__).resolve().parent.parent

PREFIXES = DATA_DIR / "Names" / "KingdomPrefixes.json"
SUFFIXES = DATA_DIR / "Names" / "KingdomSuffixes.json"


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def generate_kingdom_name():

    prefixes = load_json(PREFIXES)
    suffixes = load_json(SUFFIXES)

    return random.choice(prefixes) + random.choice(suffixes)


def generate_kingdom(conn, world_id):

    kingdom = create_kingdom(conn, world_id, generate_kingdom_name())

    capital = generate_city(conn, kingdom["id"], "Capital")

    kingdom["capital"] = capital["id"]

    for _ in range(random.randint(2, 5)):
        generate_city(conn, kingdom["id"], "Town")

    for _ in range(random.randint(5, 10)):
        generate_city(conn, kingdom["id"], "Village")

    save_kingdom(conn, kingdom)

    # Re-fetch: pulls in every city generated above.
    return get_kingdom(conn, kingdom["id"])

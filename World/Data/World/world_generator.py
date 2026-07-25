import json
import random
import os
from pathlib import Path
from Data.Kingdoms.kingdom_generator import generate_kingdom
from Data.counters import next_id as _next_id


DATA_DIR = Path(__file__).resolve().parent.parent

WORLD_FOLDER = DATA_DIR / "World" / "WorldData"

TEMPLATE = DATA_DIR / "Templates" / "World_Template.json"

WORLD_FOLDER.mkdir(exist_ok=True)

def load_json(file):

    with open(file, "r") as f:
        return json.load(f)

def get_next_world_id():

    return _next_id("world", WORLD_FOLDER, "world_*.json")

def generate_world():

    world = load_json(TEMPLATE)

    world["id"] = get_next_world_id()

    world["name"] = "Generated World"

    number_of_kingdoms = random.randint(3,6)

    for _ in range(number_of_kingdoms):

        kingdom = generate_kingdom()

        world["kingdoms"].append(
            {
                "id": kingdom["id"],
                "name": kingdom["name"]
            }
        )

    save_world(world)

    return world

def save_world(world):

    filename = WORLD_FOLDER / f"world_{world['id']:06}.json"

    with open(filename, "w") as f:
        json.dump(world, f, indent=4)
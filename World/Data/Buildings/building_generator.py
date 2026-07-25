from pathlib import Path
import json
import random
import math
from Data.counters import next_id as _next_id

DATA_DIR = Path(__file__).resolve().parent.parent

BUILDING_FOLDER = DATA_DIR / "Buildings" / "BuildingData"
TEMPLATE = DATA_DIR / "Templates" / "Building_Template.json"
BUILDING_TYPES = DATA_DIR / "Buildings" / "BuildingTypes.json"
CITY_BUILDING_KITS = DATA_DIR / "Buildings" / "CityBuildingKits.json"

BUILDING_FOLDER.mkdir(exist_ok=True)


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def get_next_building_id():

    return _next_id("building", BUILDING_FOLDER, "building_*.json")

def save_building(building):

    filename = BUILDING_FOLDER / f"building_{building['id']:06}.json"

    with open(filename, "w") as f:
        json.dump(building, f, indent=4)

def save_buildings(buildings):

    for building in buildings:
        save_building(building)

def create_building(cit_id, building_type, building_types_data):

    building = load_json(TEMPLATE)

    building["id"] = get_next_building_id()

    building["name"] = f"{building_type} #{building['id']}"

    building["type"] = building_type

    building["category"] = building_types_data[building_type]["category"]

    building["capacity"] = building_types_data[building_type]["capacity"]

    building["condition"] = 100

    building["owner"] = None

    building["workers"] = []

    building["residents"] = []

    save_building(building)

    return building

def generate_buildings(city_id, city_type, population):

    building_types_data = load_json(BUILDING_TYPES)
    kits = load_json(CITY_BUILDING_KITS)

    buildings = []

    kit = kits.get(city_type, {})

    for building_type, bounds in kit.items():

        min_count, max_count = bounds

        count = random.randint(min_count, max_count)

        for _ in range(count):
            buildings.append(
                create_building(city_id, building_type, building_types_data)
            )

    house_capacity = building_types_data["House"]["capacity"]

    house_count = max(1, math.ceil(population / house_capacity))

    for _ in range(house_count):
        buildings.append(
            create_building(city_id, "House", building_types_data)
        )

    return buildings
import json
import random
import os
from pathlib import Path
from Data.counters import next_id as _next_id

DATA_DIR = Path(__file__).resolve().parent.parent

TEMPLATE = DATA_DIR / "Templates" / "NPC_Template.json"
CLASS_FILE = DATA_DIR / "NPCS" / "Classes.json"
SKILL_FILE = DATA_DIR / "NPCS" / "Skills.json"
PROFESSIONS = DATA_DIR / "NPCS" / "Professions.json"
PROFESSIONS_CLASSES = DATA_DIR / "NPCS" / "ProfessionClasses.json"
PROFESSIONS_WEIGHTS = DATA_DIR  / "Cities" / "ProfessionWeights.json"
PROFESSIONS_BUILDINGS = DATA_DIR / "Buildings" / "ProfessionBuildings.json"

NPC_FOLDER = DATA_DIR / "NPCS" / "NPCData"

MALE_NAMES = DATA_DIR / "Names" / "Human_Male.json"
FEMALE_NAMES = DATA_DIR / "Names" / "Human_Female.json"

NPC_FOLDER.mkdir(exist_ok=True)


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def get_next_id():

    return _next_id("npc", NPC_FOLDER, "npc_*.json")

def find_open_building(buildings, building_type):

    for building in buildings:

        if building["type"] == building_type and len(building["workers"]) < building["capacity"]:
            return building

    return None

def is_profession_available(profession, profession_buildings, buildings):

    building_type = profession_buildings.get(profession)

    if building_type is None:
        return True

    if buildings is None:
        return True

    return find_open_building(buildings, building_type) is not None

def generate_profession(city_type=None, buildings=None):

    professions = load_json(PROFESSIONS)
    profession_buildings = load_json(PROFESSIONS_BUILDINGS)

    weights = None

    if city_type:
        weights_data = load_json(PROFESSIONS_WEIGHTS)
        weights = weights_data.get(city_type)

    category_order = list(professions.keys())

    if weights:
        category_order = [c for c in weights if c in professions]

    remaining_categories = list(category_order)

    while remaining_categories:

        if weights:
            category_weights = [weights.get(c, 1) for c in remaining_categories]
            category = random.choices(remaining_categories, weights=category_weights, k=1)[0]
        else:
            category = random.choice(remaining_categories)

        available = [
            p for p in professions[category]
            if is_profession_available(p, profession_buildings, buildings)
        ]

        if available:
            return random.choice(available)
        
        remaining_categories.remove(category)

    return None

def generate_class(profession):

    profession_classes = load_json(PROFESSIONS_CLASSES)

    entry = None
    for key, value in profession_classes.items():
        if key.lower() == profession.lower():
            entry = value
            break

    if not entry or not entry.get("classes"):
        return "None"

    return random.choice(
        entry["classes"]
    )
def find_open_building(buildings, building_type, occupancy_key="workers"):

    for building in buildings:

        if building["type"] == building_type and len(building[occupancy_key]) < building["capacity"]:
            return building

    return None

def generate_npc(city_type=None, buildings=None):

    npc = load_json(TEMPLATE)

    classes = load_json(CLASS_FILE)
    skills = load_json(SKILL_FILE)
    profession_buildings = load_json(PROFESSIONS_BUILDINGS)

    npc["id"] = get_next_id()
    npc["gender"] = random.choice(["Male", "Female"])

    if npc["gender"] == "Male":
        names = load_json(MALE_NAMES)
    else:
        names = load_json(FEMALE_NAMES)

    npc["name"] = random.choice(names)

    profession = generate_profession(city_type, buildings)

    workplace = None
    if profession is None:
        npc["profession"] = "Unemployed"
    else:
        npc["profession"] = profession

        building_type = profession_buildings.get(profession)

        if building_type and buildings is not None:

            building = find_open_building(buildings, building_type)

            if building:
                building["workers"].append(npc["id"])
                workplace = building["id"]

    npc["workplace"] = workplace

    home = None

    if buildings is not None:

        house = find_open_building(buildings, "House", occupancy_key="residents")

        if house:
            house["residents"].append(npc["id"])
            home = house["id"]

    npc["home"] = home
    npc["city_type"] = city_type
    npc["age"] = random.randint(18, 70)
    npc["class"] = generate_class(npc["profession"])
    npc["skills"] = {}

    for skill in classes.get(npc["class"], {}).get("skills", []):

        npc["skills"][skill] = skills[skill].copy()
    save_npc(npc)

    return npc


def save_npc(npc):

    filename = NPC_FOLDER / f"npc_{npc['id']:06}.json"

    with open(filename, "w") as f:
        json.dump(npc, f, indent=4)

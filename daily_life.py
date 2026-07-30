import json
import random
from pathlib import Path
from Data.Database.repository import create_npc, save_npc, find_open_building, count_occupants

DATA_DIR = Path(__file__).resolve().parent.parent

CLASS_FILE = DATA_DIR / "NPCS" / "Classes.json"
SKILL_FILE = DATA_DIR / "NPCS" / "Skills.json"
PROFESSIONS = DATA_DIR / "NPCS" / "Professions.json"
PROFESSION_CLASSES = DATA_DIR / "NPCS" / "ProfessionClasses.json"
PROFESSION_WEIGHTS = DATA_DIR / "NPCS" / "ProfessionWeights.json"
PROFESSION_BUILDINGS = DATA_DIR / "Buildings" / "ProfessionBuildings.json"

MALE_NAMES = DATA_DIR / "Names" / "Human_Male.json"
FEMALE_NAMES = DATA_DIR / "Names" / "Human_Female.json"


def load_json(file):

    with open(file, "r") as f:
        return json.load(f)


def get_building_type(profession, profession_buildings):

    # Case-insensitive match -- a profession spelled differently between
    # Professions.json and ProfessionBuildings.json (e.g. "Innkeeper" vs
    # "innkeeper") must NOT silently be treated as "no building required".
    for key, value in profession_buildings.items():
        if key.lower() == profession.lower():
            return value

    return None


def is_profession_available(conn, city_id, profession, profession_buildings):

    building_type = get_building_type(profession, profession_buildings)

    # No building requirement (e.g. Bandit) -- always available
    if building_type is None:
        return True

    if city_id is None:
        return True

    return find_open_building(conn, city_id, building_type, "workplace_id") is not None


def generate_profession(conn, city_id, city_type=None):

    professions = load_json(PROFESSIONS)
    profession_buildings = load_json(PROFESSION_BUILDINGS)

    weights = None

    if city_type:
        weights_data = load_json(PROFESSION_WEIGHTS)
        weights = weights_data.get(city_type)

    category_order = list(professions.keys())

    if weights:
        category_order = [c for c in weights if c in professions]

    # Try categories weighted by city type, but skip a category entirely
    # once every profession in it is full, and reroll a different one.
    remaining_categories = list(category_order)

    while remaining_categories:

        if weights:
            category_weights = [weights.get(c, 1) for c in remaining_categories]
            category = random.choices(remaining_categories, weights=category_weights, k=1)[0]
        else:
            category = random.choice(remaining_categories)

        available = [
            p for p in professions[category]
            if is_profession_available(conn, city_id, p, profession_buildings)
        ]

        if available:
            return random.choice(available)

        remaining_categories.remove(category)

    # Every profession in every category is full -- no work available
    return None


def generate_class(profession):

    profession_classes = load_json(PROFESSION_CLASSES)

    # Match keys case-insensitively (e.g. "Innkeeper" profession vs "innkeeper" key)
    entry = None
    for key, value in profession_classes.items():
        if key.lower() == profession.lower():
            entry = value
            break

    if not entry or not entry.get("classes"):
        return "None"

    return random.choice(entry["classes"])


def generate_npc(conn, city_id, city_type=None):

    classes = load_json(CLASS_FILE)
    skills = load_json(SKILL_FILE)
    profession_buildings = load_json(PROFESSION_BUILDINGS)

    gender = random.choice(["Male", "Female"])

    names = load_json(MALE_NAMES if gender == "Male" else FEMALE_NAMES)
    name = random.choice(names)

    age = random.randint(18, 70)

    profession = generate_profession(conn, city_id, city_type)

    if profession is None:
        profession = "Unemployed"

    npc_class = generate_class(profession)

    npc = create_npc(conn, city_id, name, gender, age, npc_class, profession)

    building_type = get_building_type(profession, profession_buildings)

    if building_type:

        building = find_open_building(conn, city_id, building_type, "workplace_id")

        if building:
            npc["workplace"] = building["id"]

    house = find_open_building(conn, city_id, "House", "home_id")

    if house:
        npc["home"] = house["id"]

    for skill_name in classes.get(npc_class, {}).get("skills", []):
        npc["skills"][skill_name] = skills[skill_name].copy()

    from Data.DailyLife.daily_life import build_schedule

    build_schedule(npc)

    npc["location"] = npc["home"]

    save_npc(conn, npc)

    return npc

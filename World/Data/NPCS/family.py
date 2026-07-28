import json
import random
from pathlib import Path
from Data.NPCS.npc_generator import TEMPLATE, MALE_NAMES, FEMALE_NAMES, load_json

DATA_DIR = Path(__file__).resolve().parent.parent

NPC_FOLDER = DATA_DIR / "NPCS" / "NPCData"
BUILDING_FOLDER = DATA_DIR / "Buildings" / "BuildingData"

MIN_MARRY_AGE = 18
MARRIAGE_CHANCE = 0.6

FERTILE_AGE_MIN = 18
FERTILE_AGE_MAX = 45
CHILD_CHANCE = 0.5
MAX_CHILDREN = 3


def load_npc(npc_id):

    path = NPC_FOLDER / f"npc_{npc_id:06}.json"

    with open(path, "r") as f:
        return json.load(f)

def save_npc(npc):

    path = NPC_FOLDER / f"npc_{npc['id']:06}.json"

    with open(path, "w") as f:
        json.dump(npc, f, indent=4)


def load_building(building_id):

    path = BUILDING_FOLDER / f"building_{building_id:06}.json"

    with open(path, "r") as f:
        return json.load(f)

def save_building(building):

    path = BUILDING_FOLDER / f"building_{building['id']:06}.json"

    with open(path, "w") as f:
        json.dump(building, f, indent=4)


def get_next_npc_id():

    from Data.counters import next_id

    return next_id("npc", NPC_FOLDER, "npc_*.json")


def marry(npc_a, npc_b, buildings_by_id):

    npc_a["spouse"] = npc_b["id"]
    npc_b["spouse"] = npc_a["id"]

    old_home = buildings_by_id.get(npc_b["home"])
    new_home = buildings_by_id.get(npc_a["home"])

    if old_home and npc_b["id"] in old_home["residents"]:
        old_home["residents"].remove(npc_b["id"])

    if new_home:
        new_home["residents"].append(npc_b["id"])
        npc_b["hpme"] = new_home["id"]


def create_child(parent_a, parent_b, city_type, buildings_by_id):

    child = load_json(TEMPLATE)

    child["id"] = get_next_npc_id()
    child["gender"] = random.choice(["Male", "Female"])

    names = load_json(MALE_NAMES if child["gender"] == "Male" else FEMALE_NAMES)
    child["name"] = random.choice(names)

    child["age"] = 0
    child["city_type"] = city_type
    child["profession"] = "Child"
    child["class"] = "None"
    child["workplace"] = None
    child["home"] = parent_a["home"]
    child["parents"] = [parent_a["id"], parent_b["id"]]

    home = buildings_by_id.get(parent_a["home"])

    if home:
        home["residents"].append(child["id"])

    save_npc(child)

    parent_a["children"].append(child["id"])
    parent_b["children"].append(child["id"])

    return child


def form_families(city):
    npcs = [load_npc(entry["id"]) for entry in city["npcs"]]

    buildings_by_id = {
        entry["id"]: load_building(entry["id"]) for entry in city["buildings"]
    }

    eligible = [
        npc for npc in npcs
        if npc["age"] >= MIN_MARRY_AGE and npc["spouse"] is None
    ]

    random.shuffle(eligible)

    new_children = []

    while len(eligible) >= 2:

        npc_a = eligible.pop()
        npc_b = eligible.pop()

        if random.random() > MARRIAGE_CHANCE:
            continue

        marry(npc_a, npc_b, buildings_by_id)

        can_have_children = (
            FERTILE_AGE_MIN <= npc_a["age"] <= FERTILE_AGE_MAX
            or FERTILE_AGE_MIN <= npc_b["age"] <= FERTILE_AGE_MAX
        )

        num_children = 0

        while can_have_children and num_children < MAX_CHILDREN and random.random() <CHILD_CHANCE:

            child = create_child(npc_a, npc_b, city["type"], buildings_by_id)

            new_children.append(child)

            num_children += 1

    for npc in npcs:
        save_npc(npc)

    for building in buildings_by_id.values():
        save_building(building) 

    for child in new_children:
        city["npcs"].append({
            "id": child["id"],
            "name": child ["name"],
            "profession": child["profession"],
            "class": child["class"],
            "workplace": child["workplace"],
            "home": child["home"]
        })

    return city
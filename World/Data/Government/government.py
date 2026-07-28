import json
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent

NPC_FOLDER = DATA_DIR / "NPCS" / "NPCData"

RULER_CANDIDATE_PROFESSIONS = ["Knight", "Captian", "Merchant", "Scholar", "Priest"]

NON_RULER_PROFESSIONS = ("Unemployed", "Child")


def load_npc(npc_id):

    path = NPC_FOLDER / f"npc_{npc_id:06}.json"

    with open(path, "r") as f:
        return json.load(f)


def save_npc(npc):

    path = NPC_FOLDER / f"npc_{npc['id']:06}.json"

    with open(path, "w") as f:
        json.dump(npc, f, indent=4)


def assign_city_ruler(city):

    npcs = [load_npc(entry["id"]) for entry in city["npcs"]]

    candidates = [
        npc for npc in npcs
        if npc["profession"] in RULER_CANDIDATE_PROFESSIONS and npc["title"] is None
    ]

    if not candidates:
        candidates = [
            npc for npc in npcs
            if npc["profession"] not in NON_RULER_PROFESSIONS and npc["title"] is None
        ]

    if not candidates:
        city["ruler"] = None
        return None

    ruler = random.choice(candidates)

    if city["type"] == "Capital":
        ruler["title"] = "King" if ruler["gender"] == "Male" else "Queen"
    else:
        ruler["title"] = "Mayor"

    save_npc(ruler)

    city["ruler"] = ruler["id"]

    return ruler


def assign_kingdom_ruler(kingdom, capital_city):
    
    kingdom["ruler"] = capital_city.get("ruler")

    return kingdom["ruler"]
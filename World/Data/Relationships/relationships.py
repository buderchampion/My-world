import json
import random
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).resolve().parent.parent

NPC_FOLDER = DATA_DIR / "NPCS" / "NPCData"

FRIEND_CHANCE = 0.4
RIVAL_CHANCE = 0.15

def load_npc(npc_id):

    path = NPC_FOLDER / f"npc_{npc_id:06}.json"

    with open(path, "r") as f:
        return json.load(f)


def save_npc(npc):

    path = NPC_FOLDER / f"npc_{npc['id']:06}.json"

    with open(path, "w") as f:
        json.dump(npc, f, indent=4)


def roll_relationship():

    roll = random.random()

    if roll < FRIEND_CHANCE:
        return "Friend", random.randint(40, 90)

    if roll < FRIEND_CHANCE + RIVAL_CHANCE:
        return "Rival", random.randint(-80, -30)

    return "Acquaintance", random.randint(-10, 10)


def link(npc_a, npc_b):

    rel_type, reputation = roll_relationship()

    npc_a["relationships"][str(npc_b["id"])] = {"type": rel_type, "reputation": reputation}
    npc_b["relationships"][str(npc_a["id"])] = {"type": rel_type, "reputation": reputation}


def form_relationships(city):

    npcs = [load_npc(entry["id"]) for entry in city["npcs"]]

    by_workplace = defaultdict(list)

    for npc in npcs:

        if npc["workplace"] is not None:
            by_workplace[npc["workplace"]].append(npc)

    for coworkers in by_workplace.values():

        for i in range(len(coworkers)):
            for j in range(i + 1, len(coworkers)):
                link(coworkers[i], coworkers[j])

    for npc in npcs:
        save_npc(npc)

    return city
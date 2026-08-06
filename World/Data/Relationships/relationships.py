import random
from collections import defaultdict
from Data.Database.repository import get_city_npcs, set_relationship

FRIEND_CHANCE = 0.4
RIVAL_CHANCE = 0.15
# anything left over becomes a neutral "Acquaintance"


def roll_relationship():

    roll = random.random()

    if roll < FRIEND_CHANCE:
        return "Friend", random.randint(40, 90)

    if roll < FRIEND_CHANCE + RIVAL_CHANCE:
        return "Rival", random.randint(-80, -30)

    return "Acquaintance", random.randint(-10, 10)


def link(conn, npc_a, npc_b):

    # Already linked -- leave it alone. Without this check, calling
    # form_relationships more than once (e.g. every month, once wired
    # into the clock) would re-roll and could flip an existing
    # friendship into a rivalry for no reason.
    if str(npc_b["id"]) in npc_a.get("relationships", {}):
        return

    rel_type, reputation = roll_relationship()

    set_relationship(conn, npc_a["id"], npc_b["id"], rel_type, reputation)


def form_relationships(conn, city):
    """
    Loads every NPC in this city and forms a relationship between every
    pair of coworkers (same workplace). Not called automatically.
    """

    npcs = get_city_npcs(conn, city["id"])

    by_workplace = defaultdict(list)

    for npc in npcs:

        if npc["workplace"] is not None:
            by_workplace[npc["workplace"]].append(npc)

    for coworkers in by_workplace.values():

        for i in range(len(coworkers)):
            for j in range(i + 1, len(coworkers)):
                link(conn, coworkers[i], coworkers[j])

    conn.commit()

    return city

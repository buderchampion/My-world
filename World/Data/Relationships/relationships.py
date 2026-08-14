import random
from collections import defaultdict
from Data.Database.repository import get_city_npcs, set_relationship

FRIEND_CHANCE = 0.4
RIVAL_CHANCE = 0.15
# anything left over becomes a neutral "Acquaintance"

# How much a spouse's death shifts the survivor's stress/happiness --
# deliberately much larger than an ordinary combat morale impact (see
# Data.Combat.combat.MORALE_IMPACT), since losing someone you married
# should outweigh a nearby raid or a battle you weren't even in.
GRIEF_STRESS_IMPACT = 25
GRIEF_HAPPINESS_IMPACT = -30


def apply_grief(conn, deceased_npc):
    """
    If the NPC who just died had a living spouse, the survivor takes
    a real, lasting stress/happiness hit, and is freed to eventually
    remarry (nothing previously cleared spouse_id on death, which
    silently left every widow/widower permanently "married" to a
    dead person and unable to remarry -- form_families requires
    spouse is None).

    Safe to call for every death, not just combat ones -- does
    nothing if the deceased had no spouse, or if the spouse is
    already dead too. Returns True if grief was actually applied.
    """

    spouse_id = deceased_npc.get("spouse")

    if not spouse_id:
        return False

    from Data.Database.repository import get_npc, save_npc

    spouse = get_npc(conn, spouse_id)

    if not spouse["alive"]:
        return False

    spouse["needs"]["stress"] = max(0, min(100, spouse["needs"]["stress"] + GRIEF_STRESS_IMPACT))
    spouse["needs"]["happiness"] = max(0, min(100, spouse["needs"]["happiness"] + GRIEF_HAPPINESS_IMPACT))
    spouse["spouse"] = None

    save_npc(conn, spouse)

    return True


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
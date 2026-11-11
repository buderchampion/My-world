import random
from pathlib import Path
from Data.NPCS.npc_generator import MALE_NAMES, FEMALE_NAMES, load_json
from Data.Database.repository import get_city_npcs, save_npc, create_npc, set_npc_parents


DATA_DIR = Path(__file__).resolve().parent.parent

MIN_MARRY_AGE = 18
MARRIAGE_CHANCE = 0.6

SPOUSE_REPUTATION_RANGE = (70, 95)
FERTILE_AGE_MIN = 18
FERTILE_AGE_MAX = 45
CHILD_CHANCE = 0.5
MAX_CHILDREN = 3


def residents(conn, house_id):
    """How many living people call this house home."""
    if not house_id:
        return 0
    return conn.execute("SELECT COUNT(*) FROM npcs WHERE home_id = ? AND alive = 1", (house_id,)).fetchone()[0]


def room_in(conn, house_id, needed):
    """Does this house have room for `needed` more?"""
    if not house_id:
        return False
    capacity = conn.execute("SELECT capacity FROM buildings WHERE id = ?", (house_id,)).fetchone()
    return bool(capacity) and residents(conn, house_id) + needed <= capacity[0]


def marry(npc_a, npc_b, conn=None):

    npc_a["spouse"] = npc_b["id"]
    npc_b["spouse"] = npc_a["id"]

    # The couple lives together, where there is room: npc_a's home, else
    # npc_b's, else any house in town with room for both. Only if the whole
    # town is full do they crowd into npc_a's home (public works then counts
    # the crowding as a need for Houses -- Data.Buildings.upgrades).
    # (No building lists to update -- home_id IS the residency.)
    if conn is None or room_in(conn, npc_a["home"], 1):
        npc_b["home"] = npc_a["home"]
    elif room_in(conn, npc_b["home"], 1):
        npc_a["home"] = npc_b["home"]
    else:
        house = conn.execute(
            """SELECT b.id FROM buildings b WHERE b.city_id = ? AND b.type = 'House'
               AND b.capacity - (SELECT COUNT(*) FROM npcs n WHERE n.home_id = b.id AND n.alive = 1) >= 2
               ORDER BY b.id LIMIT 1""", (npc_a["city_id"],)).fetchone()
        npc_a["home"] = npc_b["home"] = house[0] if house else npc_a["home"]


def create_child(conn, parent_a, parent_b, city_type):

    gender = random.choice(["Male", "Female"])

    names = load_json(MALE_NAMES if gender == "Male" else FEMALE_NAMES)
    name = random.choice(names)

    from Data.NPCS.personality import inherited_traits
    child = create_npc(conn, parent_a["city_id"], name, gender, 0, "None", "Child",
                       traits=inherited_traits(parent_a, parent_b))      # takes after both parents

    set_npc_parents(conn, child["id"], parent_a["id"], parent_b["id"])

    child["home"] = parent_a["home"]

    from Data.DailyLife.daily_life import build_schedule

    build_schedule(child)

    child["location"] = child["home"]

    save_npc(conn, child)

    return child


def form_families(conn, city):
    """
    Loads every NPC in this city, pairs up eligible adults into
    marriages (moving them into one household), and gives some married
    couples children. Saves every changed NPC back to the database, and
    adds any newborn children to city["npcs"].

    Not called automatically -- run whenever you want a city's
    population to settle into families.
    """

    npcs = get_city_npcs(conn, city["id"])

    eligible = [
        npc for npc in npcs
        if npc["alive"] and npc["age"] >= MIN_MARRY_AGE and npc["spouse"] is None     # the dead do not marry
    ]

    random.shuffle(eligible)

    new_children = []
    new_marriages = []

    while len(eligible) >= 2:

        npc_a = eligible.pop()
        npc_b = eligible.pop()

        if random.random() > MARRIAGE_CHANCE:
            continue

        marry(npc_a, npc_b, conn)
        save_npc(conn, npc_a)          # housed at once, so the next couple counts them
        save_npc(conn, npc_b)
        new_marriages.append((npc_a, npc_b))

        can_have_children = (
            FERTILE_AGE_MIN <= npc_a["age"] <= FERTILE_AGE_MAX
            or FERTILE_AGE_MIN <= npc_b["age"] <= FERTILE_AGE_MAX
        )

        num_children = 0

        while can_have_children and num_children < MAX_CHILDREN and random.random() < CHILD_CHANCE:

            child = create_child(conn, npc_a, npc_b, city["type"])

            new_children.append(child)

            num_children += 1

    for npc in npcs:
        save_npc(conn, npc)

    conn.commit()

    from Data.Events.events import marriage_event
    from Data.Database.repository import set_relationship

    for npc_a, npc_b in new_marriages:
        marriage_event(conn, npc_a, npc_b)
        set_relationship(
            conn, npc_a["id"], npc_b["id"], "Spouse",
            random.randint(*SPOUSE_REPUTATION_RANGE), origin="married"
        )

    conn.commit()

    for child in new_children:
        city["npcs"].append({
            "id": child["id"],
            "name": child["name"],
            "profession": child["profession"],
            "class": child["class"],
            "workplace": child["workplace"],
            "home": child["home"]
        })

    return city
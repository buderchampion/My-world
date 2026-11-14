import random
from pathlib import Path
from Data.NPCS.npc_generator import MALE_NAMES, FEMALE_NAMES, load_json
from Data.Database.repository import get_city_npcs, save_npc, create_npc, set_npc_parents


DATA_DIR = Path(__file__).resolve().parent.parent

MIN_MARRY_AGE = 18
# Courtship (form_families): close friends court, grow closer, and marry.
COURTSHIP_START = 70        # a friendship this warm can become a courtship
COURTSHIP_CHANCE = 0.25     # each month, for an unmarried adult not yet courting
COURTSHIP_GROWTH = 4        # reputation a courting pair gains each month
COURTSHIP_MONTHS = 6        # the least time before they may marry
WEDDING_CHANCE = 0.35       # each month after that
MAX_AGE_GAP = 15
DAYS_PER_MONTH = 30
BIRTH_CHANCE = 0.02         # each month, for a married couple of whom one is of age to have children
FOUNDING_AGE = 25           # at generation, about FOUNDING_SHARE of adults this old start married
FOUNDING_SHARE = 0.5
FOUNDING_AGE_GAP = 10
FOUNDING_CHILD_SHARE = 0.5  # children at generation: half as many as everyone else, so about a third of all
GROWN_AGE = 16

SPOUSE_REPUTATION_RANGE = (70, 95)
FERTILE_AGE_MIN = 18
FERTILE_AGE_MAX = 45
MAX_CHILDREN = 6           # a couple's children in a lifetime


def residents(conn, house_id):
    """How many living people call this house home."""
    if not house_id:
        return 0
    return conn.execute("SELECT COUNT(*) FROM npcs WHERE home_id = ? AND alive = 1", (house_id,)).fetchone()[0]


def room_in(conn, house_id, needed):
    """Does this house have room for `needed` more?"""
    if not house_id:
        return False
    row = conn.execute("SELECT capacity, type FROM buildings WHERE id = ?", (house_id,)).fetchone()
    if not row or row["type"] != "House":
        return False                          # the slums are no home with room (Data.World.slums)
    return residents(conn, house_id) + needed <= row["capacity"]


def marry(npc_a, npc_b, conn=None):

    npc_a["spouse"] = npc_b["id"]
    npc_b["spouse"] = npc_a["id"]

    if conn is not None:                       # the couple takes the older family's name (Data.NPCS.family_names)
        from Data.NPCS.family_names import marry_names
        marry_names(conn, npc_a, npc_b)

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
        if house:
            npc_a["home"] = npc_b["home"] = house[0]
        else:                                  # crowd into whichever of their homes is a House, never the slums
            is_house = lambda h: bool(h) and conn.execute("SELECT type FROM buildings WHERE id = ?", (h,)).fetchone()["type"] == "House"
            shared = npc_a["home"] if is_house(npc_a["home"]) else npc_b["home"] if is_house(npc_b["home"]) else npc_a["home"]
            npc_a["home"] = npc_b["home"] = shared


def create_child(conn, parent_a, parent_b, city_type):

    gender = random.choice(["Male", "Female"])

    names = load_json(MALE_NAMES if gender == "Male" else FEMALE_NAMES)
    name = random.choice(names)

    from Data.NPCS.personality import inherited_traits
    from Data.NPCS.races import child_race
    race = child_race(parent_a.get("race"), parent_b.get("race"))      # an elf and a human have a half-elf
    child = create_npc(conn, parent_a["city_id"], name, gender, 0, "None", "Child",
                       traits=inherited_traits(parent_a, parent_b), race=race)      # takes after both parents

    set_npc_parents(conn, child["id"], parent_a["id"], parent_b["id"])

    from Data.NPCS.family_names import name_child      # the name their family uses
    name_child(conn, child, parent_a, parent_b)

    child["home"] = parent_a["home"]

    from Data.DailyLife.daily_life import build_schedule

    build_schedule(child)

    child["location"] = child["home"]

    save_npc(conn, child)

    return child


def form_families(conn, city):
    """
    Monthly, for one town: courtships begin between close friends, grow,
    and after COURTSHIP_MONTHS may end in marriage; married couples have
    children now and then. (Before, strangers were paired at random and
    married at once, with all their children born on the wedding day.)
    """

    from Data.Government.edicts import today
    world_id = conn.execute("SELECT k.world_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE c.id = ?", (city["id"],)).fetchone()[0]
    day = today(conn, world_id)
    npcs = get_city_npcs(conn, city["id"])
    by_id = {n["id"]: n for n in npcs}
    single = {n["id"] for n in npcs if n["alive"] and n["age"] >= MIN_MARRY_AGE and n["spouse"] is None}     # the dead do not marry

    new_marriages = []
    new_children = []

    # courtships that can no longer go on end (a death, another marriage, a move away)
    courting = {}
    for row in conn.execute("SELECT * FROM courtships").fetchall():
        a, b = row["npc_a_id"], row["npc_b_id"]
        if a in by_id or b in by_id:
            if a in single and b in single:
                courting[a] = courting[b] = dict(row)
            else:
                conn.execute("DELETE FROM courtships WHERE npc_a_id = ? AND npc_b_id = ?", (a, b))

    # going on: they grow closer, and after long enough may marry
    for court in {(c["npc_a_id"], c["npc_b_id"]): c for c in courting.values()}.values():
        a, b = by_id[court["npc_a_id"]], by_id[court["npc_b_id"]]
        conn.execute("""UPDATE relationships SET reputation = MIN(100, reputation + ?) WHERE npc_a_id = ? AND npc_b_id = ?""",
                     (COURTSHIP_GROWTH, min(a["id"], b["id"]), max(a["id"], b["id"])))
        if day - court["since_day"] >= COURTSHIP_MONTHS * DAYS_PER_MONTH and random.random() < WEDDING_CHANCE:
            conn.execute("DELETE FROM courtships WHERE npc_a_id = ? AND npc_b_id = ?", (court["npc_a_id"], court["npc_b_id"]))
            marry(a, b, conn)
            save_npc(conn, a)          # housed at once, so the next couple counts them
            save_npc(conn, b)
            new_marriages.append((a, b))
            single -= {a["id"], b["id"]}

    # beginning: an unmarried adult who is not courting may court their closest unmarried friend in town
    free = [i for i in single if i not in courting]
    random.shuffle(free)
    taken = set(courting)
    for npc_id in free:
        if npc_id in taken or random.random() >= COURTSHIP_CHANCE:
            continue
        me = by_id[npc_id]
        friend = conn.execute(
            """SELECT CASE WHEN npc_a_id = ? THEN npc_b_id ELSE npc_a_id END other, reputation FROM relationships
               WHERE (npc_a_id = ? OR npc_b_id = ?) AND reputation >= ? ORDER BY reputation DESC""",
            (npc_id, npc_id, npc_id, COURTSHIP_START)).fetchall()
        for row in friend:
            other = by_id.get(row["other"])
            if other and other["id"] in single and other["id"] not in taken and abs(other["age"] - me["age"]) <= MAX_AGE_GAP:
                pair = (min(npc_id, other["id"]), max(npc_id, other["id"]))
                conn.execute("INSERT OR IGNORE INTO courtships (npc_a_id, npc_b_id, since_day) VALUES (?, ?, ?)", (*pair, day))
                taken.update(pair)
                break

    # children: now and then, for married couples
    for npc in npcs:
        partner = by_id.get(npc["spouse"]) if npc["spouse"] else None
        if not partner or npc["id"] > partner["id"] or not npc["alive"] or not partner["alive"]:
            continue                                   # each couple once, both living, both in this town
        fertile = FERTILE_AGE_MIN <= npc["age"] <= FERTILE_AGE_MAX or FERTILE_AGE_MIN <= partner["age"] <= FERTILE_AGE_MAX
        if not fertile or random.random() >= BIRTH_CHANCE:
            continue
        had = conn.execute("SELECT COUNT(*) FROM npcs WHERE (parent_a_id = ? AND parent_b_id = ?) OR (parent_a_id = ? AND parent_b_id = ?)",
                           (npc["id"], partner["id"], partner["id"], npc["id"])).fetchone()[0]
        if had < MAX_CHILDREN:
            new_children.append(create_child(conn, npc, partner, city["type"]))

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



def _swap_into(conn, mover_id, home_id, keep):
    """Moves `mover_id` into `home_id` by swapping homes with one single resident not in `keep`
    (a generated world's houses are exactly full). Returns True if they moved."""
    mover_home = conn.execute("SELECT home_id FROM npcs WHERE id = ?", (mover_id,)).fetchone()[0]
    if mover_home == home_id:
        return True
    marks = ",".join(str(i) for i in keep) or "0"
    resident = conn.execute(
        f"""SELECT id FROM npcs WHERE home_id = ? AND alive = 1 AND spouse_id IS NULL AND parent_a_id IS NULL
            AND id NOT IN ({marks}) ORDER BY id LIMIT 1""", (home_id,)).fetchone()
    if resident is None:
        return False
    conn.execute("UPDATE npcs SET home_id = ? WHERE id = ?", (mover_home, resident[0]))
    conn.execute("UPDATE npcs SET home_id = ? WHERE id = ?", (home_id, mover_id))
    return True


def found_families(conn, world_id):
    """
    At generation: about FOUNDING_SHARE of each town's adults over FOUNDING_AGE
    start married to someone close in age, living together, and the town's
    children are given to couples old enough to be their parents, living
    with them. Returns (couples, children placed).
    """
    from Data.Database.repository import set_relationship
    couples = placed = 0
    for city in conn.execute(
            "SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?", (world_id,)).fetchall():
        adults = [dict(r) for r in conn.execute(
            "SELECT id, age FROM npcs WHERE city_id = ? AND alive = 1 AND age >= ? AND spouse_id IS NULL ORDER BY age, id",
            (city["id"], FOUNDING_AGE))]
        # neighbours in age make the likeliest couples; about half of these adults end up married
        pairs = [(adults[i], adults[i + 1]) for i in range(0, len(adults) - 1, 2)
                 if adults[i + 1]["age"] - adults[i]["age"] <= FOUNDING_AGE_GAP]
        random.shuffle(pairs)
        town_couples = []
        for a, b in pairs[:round(len(adults) * FOUNDING_SHARE / 2)]:
            conn.execute("UPDATE npcs SET spouse_id = ? WHERE id = ?", (b["id"], a["id"]))
            conn.execute("UPDATE npcs SET spouse_id = ? WHERE id = ?", (a["id"], b["id"]))
            home = conn.execute("SELECT home_id FROM npcs WHERE id = ?", (a["id"],)).fetchone()[0]
            if home:
                _swap_into(conn, b["id"], home, keep={a["id"], b["id"]})
            set_relationship(conn, a["id"], b["id"], "Spouse", random.randint(*SPOUSE_REPUTATION_RANGE), origin="married")
            town_couples.append((a, b))
        couples += len(town_couples)

        # children: on top of the town's people, about a third of all (half as many as everyone else),
        # each born to a couple old enough to be their parents, living with them
        from Data.Database.repository import get_npc
        city_type = conn.execute("SELECT type FROM cities WHERE id = ?", (city["id"],)).fetchone()[0]
        people = conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1", (city["id"],)).fetchone()[0]
        count = {}
        for _ in range(round(people * FOUNDING_CHILD_SHARE)):
            age = random.randint(0, GROWN_AGE - 1)
            fits = [c for c in town_couples if c[0]["age"] - age >= FERTILE_AGE_MIN and c[1]["age"] - age >= FERTILE_AGE_MIN
                    and min(c[0]["age"], c[1]["age"]) - age <= FERTILE_AGE_MAX and count.get(c[0]["id"], 0) < MAX_CHILDREN]
            if not fits:
                continue
            a, b = random.choice(fits)
            count[a["id"]] = count.get(a["id"], 0) + 1
            child = create_child(conn, get_npc(conn, a["id"]), get_npc(conn, b["id"]), city_type)
            conn.execute("UPDATE npcs SET age = ? WHERE id = ?", (age, child["id"]))
            placed += 1
    # every town and village now counts its people, children included
    conn.execute("""UPDATE cities SET population = (SELECT COUNT(*) FROM npcs n WHERE n.city_id = cities.id AND n.alive = 1)
                    WHERE kingdom_id IN (SELECT id FROM kingdoms WHERE world_id = ?)""", (world_id,))
    conn.commit()
    return couples, placed
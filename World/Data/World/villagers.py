import random

# Entering the story. A village's peasants are counted, not simulated
# (Data.World.countryside) -- until something happens to one of them. Then
# they become an ordinary individual NPC, and they stay one:
#   * famine flight: a hungry village's households leave for a town and
#     arrive as families -- a married couple and their children -- among the
#     town's poor;
#   * banditry: desperate young men from a hungry village join a Bandit Camp;
#   * levies: when an army is raised with levies, young men are drawn from
#     the villages to serve as Soldiers, and go home as Farmers afterwards.
# A household is FAMILY_SIZE people, so each person who leaves takes that
# share of a household with them (households can become fractional, which
# keeps harvests and eating exact).

FAMILY_SIZE = 5

# A village hungry this many days in a row starts losing households to the
# towns, this share of them each month (at least one).
FLIGHT_AFTER_HUNGRY_DAYS = 10
FLIGHT_SHARE_PER_MONTH = 0.05

# A village hungry this many days in a row may lose young men to banditry:
# this chance each month, taking 1 to BANDITS_AT_MOST of them.
BANDITRY_AFTER_HUNGRY_DAYS = 20
BANDITRY_CHANCE_PER_MONTH = 0.5
BANDITS_AT_MOST = 3

# Who can be levied or turn bandit.
FIGHTING_AGE = (16, 40)


def make_individual(conn, village_id, city_id=None, profession="Farmer", age=None, gender=None, needs_workplace=True):
    """
    Turns one of a village's peasants into an individual NPC: the village
    loses a fifth of a household, and the new NPC lives in city_id (the
    village itself if None), working as `profession` if that city has an
    open place for it (otherwise Laborer -- unless needs_workplace is
    False, as for a levy, who serves in an army rather than at a building),
    and housed if it has an open house. Returns the NPC.
    """

    from Data.Database.repository import create_npc, save_npc, find_open_building
    from Data.NPCS.npc_generator import (
        load_json, generate_class, get_building_type, MALE_NAMES, FEMALE_NAMES, CLASS_FILE, SKILL_FILE, PROFESSION_BUILDINGS,
    )
    from Data.DailyLife.daily_life import build_schedule

    city_id = city_id or village_id
    gender = gender or random.choice(["Male", "Female"])
    age = age if age is not None else random.randint(18, 50)
    name = random.choice(load_json(MALE_NAMES if gender == "Male" else FEMALE_NAMES))

    npc_class = "None" if profession == "Child" else generate_class(profession)
    npc = create_npc(conn, city_id, name, gender, age, npc_class, profession)

    building_type = get_building_type(profession, load_json(PROFESSION_BUILDINGS))
    if building_type:
        workplace = find_open_building(conn, city_id, building_type, "workplace_id")
        if workplace:
            npc["workplace"] = workplace["id"]
        elif profession != "Child" and needs_workplace:
            npc["profession"] = "Laborer"      # no place for that trade here

    house = find_open_building(conn, city_id, "House", "home_id")
    if house:
        npc["home"] = house["id"]

    classes, skills = load_json(CLASS_FILE), load_json(SKILL_FILE)
    for skill_name in classes.get(npc["class"], {}).get("skills", []):
        npc["skills"][skill_name] = skills[skill_name].copy()

    build_schedule(npc)
    npc["location"] = npc["home"]
    save_npc(conn, npc)

    conn.execute("UPDATE cities SET households = MAX(0, households - ?) WHERE id = ?", (1 / FAMILY_SIZE, village_id))
    conn.execute("UPDATE cities SET population = population + 1 WHERE id = ?", (city_id,))

    return npc


def _move_family(conn, village, town_id):
    """One household leaves the village for the town: a married couple and
    up to three children, all sharing one house. Returns them (None if the
    town has no free house)."""

    from Data.Database.repository import find_open_building, save_npc, set_relationship, set_npc_parents
    from Data.NPCS.family import marry, SPOUSE_REPUTATION_RANGE

    if not find_open_building(conn, town_id, "House", "home_id"):
        return None

    husband = make_individual(conn, village["id"], town_id, "Laborer", age=random.randint(20, 45), gender="Male")
    wife = make_individual(conn, village["id"], town_id, "Laborer", age=max(18, husband["age"] + random.randint(-6, 3)), gender="Female")
    wife["home"] = husband["home"]
    marry(husband, wife)
    save_npc(conn, husband)
    save_npc(conn, wife)
    set_relationship(conn, husband["id"], wife["id"], "Spouse", random.randint(*SPOUSE_REPUTATION_RANGE), origin="married")

    family = [husband, wife]
    for _ in range(FAMILY_SIZE - 2):
        child = make_individual(conn, village["id"], town_id, "Child", age=random.randint(0, 15))
        child["home"] = husband["home"]
        save_npc(conn, child)
        set_npc_parents(conn, child["id"], husband["id"], wife["id"])
        family.append(child)

    return family


def famine_flight(conn, village, towns):
    """
    A village hungry long enough loses FLIGHT_SHARE_PER_MONTH of its
    households (at least one) to the best-fed town of its kingdom that has
    room, each arriving as a family. towns: [(town id, Food on hand)].
    Returns how many households left.
    """

    if (village["hungry_days"] or 0) < FLIGHT_AFTER_HUNGRY_DAYS or village["households"] < 1:
        return 0

    leaving = max(1, int(village["households"] * FLIGHT_SHARE_PER_MONTH))
    left = 0

    for town_id, _ in sorted(towns, key=lambda t: -t[1]):
        while left < leaving and _move_family(conn, village, town_id):
            left += 1
        if left >= leaving:
            break

    return left


def turn_bandit(conn, village, camps):
    """
    A village hungry long enough may lose a few young men to banditry: they
    join a Bandit Camp in the village itself if it has one, otherwise the
    nearest one in the kingdom. camps: [(camp city id, distance)]. Returns
    how many turned bandit.
    """

    if (village["hungry_days"] or 0) < BANDITRY_AFTER_HUNGRY_DAYS or village["households"] < 1 or not camps:
        return 0
    if random.random() >= BANDITRY_CHANCE_PER_MONTH:
        return 0

    camp_city = min(camps, key=lambda c: c[1])[0]
    count = random.randint(1, BANDITS_AT_MOST)

    for _ in range(count):
        make_individual(conn, village["id"], camp_city, "Bandit", age=random.randint(*FIGHTING_AGE), gender="Male")

    return count


def village_hardship(conn, world_id):
    """
    Monthly: every hungry village may lose households to the towns and
    young men to banditry. Each kind of loss is logged. Returns totals.
    """

    from Data.Database.repository import create_event

    report = {"households_fled": 0, "bandits": 0, "famine_dead": 0}

    # This month's famine deaths in each village (Data.World.harvest), logged and cleared.
    for row in conn.execute(
        """SELECT c.id, c.name, c.kingdom_id, c.famine_dead FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND c.famine_dead >= 1""", (world_id,)
    ).fetchall():
        dead = round(row["famine_dead"])
        report["famine_dead"] += dead
        create_event(conn, "Famine", f"Famine claimed about {dead} lives in {row['name']} this month.",
                     city_id=row["id"], kingdom_id=row["kingdom_id"])
        conn.execute("UPDATE cities SET famine_dead = 0 WHERE id = ?", (row["id"],))

    villages = [dict(v) for v in conn.execute(
        """
        SELECT c.* FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
        WHERE k.world_id = ? AND c.households >= 1 AND c.hungry_days >= ?
        """, (world_id, min(FLIGHT_AFTER_HUNGRY_DAYS, BANDITRY_AFTER_HUNGRY_DAYS))
    )]

    for village in villages:

        towns = [(row["id"], row["food"]) for row in conn.execute(
            """
            SELECT c.id, COALESCE((SELECT amount FROM city_resources WHERE city_id = c.id AND resource_name = 'Food'), 0) food
            FROM cities c WHERE c.kingdom_id = ? AND c.type != 'Village'
            """, (village["kingdom_id"],)
        )]

        fled = famine_flight(conn, village, towns)
        if fled:
            report["households_fled"] += fled
            create_event(conn, "Famine Flight",
                         f"Hunger drove {fled} household(s) from {village['name']} to seek bread in the towns.",
                         city_id=village["id"], kingdom_id=village["kingdom_id"])

        camps = [(row["city_id"], (row["map_x"] - village["map_x"]) ** 2 + (row["map_y"] - village["map_y"]) ** 2)
                 for row in conn.execute(
                     """SELECT b.city_id, c.map_x, c.map_y FROM buildings b JOIN cities c ON c.id = b.city_id
                        WHERE b.type = 'Bandit Camp' AND c.kingdom_id = ?""", (village["kingdom_id"],))]
        current = conn.execute("SELECT households, hungry_days FROM cities WHERE id = ?", (village["id"],)).fetchone()
        village.update(households=current["households"], hungry_days=current["hungry_days"])

        turned = turn_bandit(conn, village, camps)
        if turned:
            report["bandits"] += turned
            create_event(conn, "Turned Bandit",
                         f"Hunger drove {turned} young man/men of {village['name']} to banditry.",
                         city_id=village["id"], kingdom_id=village["kingdom_id"])

    conn.commit()
    return report


def levy_villagers(conn, kingdom_id, count):
    """
    Draws up to `count` young men from the kingdom's villages as Soldiers,
    from each village in proportion to its households (never more than a
    village has). Returns the new NPCs; they live on in their own villages.
    """

    villages = [dict(v) for v in conn.execute(
        "SELECT id, households FROM cities WHERE kingdom_id = ? AND households >= 1", (kingdom_id,)
    )]
    total = sum(v["households"] for v in villages)
    levied = []

    for village in sorted(villages, key=lambda v: -v["households"]):
        share = min(int(village["households"]), round(count * village["households"] / total) if total else 0)
        for _ in range(min(share, count - len(levied))):
            levied.append(make_individual(conn, village["id"], village["id"], "Soldier",
                                          age=random.randint(*FIGHTING_AGE), gender="Male", needs_workplace=False))

    # rounding can leave a few to find: take them from the biggest villages
    for village in sorted(villages, key=lambda v: -v["households"]):
        while len(levied) < count and conn.execute("SELECT households FROM cities WHERE id = ?", (village["id"],)).fetchone()[0] >= 1:
            levied.append(make_individual(conn, village["id"], village["id"], "Soldier",
                                          age=random.randint(*FIGHTING_AGE), gender="Male", needs_workplace=False))

    conn.commit()
    return levied


def send_levies_home(conn, npc_ids):
    """A disbanded army's levies go back to the land: each becomes a Farmer
    in their own village if it has an open farm, otherwise Laborer."""

    from Data.Database.repository import get_npc, save_npc, find_open_building

    for npc_id in npc_ids:
        npc = get_npc(conn, npc_id)
        if npc is None or not npc["alive"]:
            continue
        farm = find_open_building(conn, npc["city_id"], "Farm", "workplace_id")
        npc["profession"] = "Farmer" if farm else "Laborer"
        npc["workplace"] = farm["id"] if farm else None
        save_npc(conn, npc)
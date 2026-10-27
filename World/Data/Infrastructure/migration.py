import random

# A permanent move is a household decision, not a solo one -- this is
# the same "genuinely desperate" bar Civil War eligibility uses
# (Data.Combat.combat.CIVIL_WAR_STRESS_THRESHOLD), applied per-person.
MIGRATION_MIN_STRESS = 75

# The destination kingdom must be meaningfully happier than home --
# bigger gap than Tourism requires (see travel.py), since this is a
# much bigger, permanent decision, not a vacation.
MIGRATION_MIN_HAPPINESS_ADVANTAGE = 20

# Gold required to even consider moving, and how much MORE the whole
# household needs (pooled together) for each additional family member
# beyond the first -- relocating a family costs more than relocating
# one person.
MIGRATION_MIN_GOLD_SOLO = 150
MIGRATION_GOLD_PER_ADDITIONAL_MEMBER = 75

# A reigning king, queen or mayor never abandons the realm, and neither does
# a household that includes one. Without this, a desperate ruler (or a
# ruler's desperate spouse, who brings the whole household along) could
# emigrate and leave a throne occupied by someone living in another kingdom,
# which is never noticed as empty and never passed on.
RULING_TITLES = ("King", "Queen", "Mayor")

# Chance, per month, that an eligible NPC actually decides to go.
# Deliberately much rarer than Tourism -- this is the biggest
# decision an NPC can make, not a whim.
MIGRATION_CHANCE_PER_MONTH = 0.02


def get_family_members(conn, npc):
    """
    Returns the NPC's own household: themself, their living spouse
    (if any), and any living children who still share their home.
    Grown children who've moved into their own household elsewhere
    aren't part of this -- migration is a decision made by whoever
    actually lives together right now.
    """

    from Data.Database.repository import get_npc

    members = [npc]

    spouse_id = npc.get("spouse")
    spouse = None

    if spouse_id:
        spouse = get_npc(conn, spouse_id)
        if spouse["alive"]:
            members.append(spouse)
        else:
            spouse = None

    parent_ids = [npc["id"]] + ([spouse["id"]] if spouse else [])
    placeholders = ",".join("?" * len(parent_ids))

    rows = conn.execute(
        f"""
        SELECT id FROM npcs
        WHERE alive = 1 AND home_id = ?
          AND (parent_a_id IN ({placeholders}) OR parent_b_id IN ({placeholders}))
        """,
        (npc["home"], *parent_ids, *parent_ids)
    ).fetchall()

    existing_ids = {m["id"] for m in members}

    for row in rows:
        if row["id"] not in existing_ids:
            members.append(get_npc(conn, row["id"]))
            existing_ids.add(row["id"])

    return members


def find_open_house_in_kingdom(conn, kingdom_id):
    """
    Searches every city in this kingdom for a House with room left.
    Returns (city_id, house_dict) for the first one found, or
    (None, None) if the whole kingdom has no space anywhere.
    """

    from Data.Database.repository import get_kingdom_cities, find_open_building

    for city in get_kingdom_cities(conn, kingdom_id):

        house = find_open_building(conn, city["id"], "House", "home_id")

        if house:
            return city["id"], house

    return None, None


def relocate_npc(conn, npc_id, new_city_id):
    """
    Permanently moves an NPC to a new city. Searches for actual
    available housing at the moment of arrival (rather than trusting
    whatever was open back when the trip was dispatched, since that
    could easily be gone by the time a weeks-long journey finishes) --
    tries the specific destination city first, then falls back to
    anywhere else in the same kingdom if that particular city filled
    up in the meantime. In the rare case nothing is open anywhere,
    the move still happens (new city, fresh employment attempt) but
    they keep their old home_id rather than being left with none.

    Also makes a fresh attempt at employment in their existing
    profession (if there's an open building for it in the new city)
    -- falls back to Unemployed if there isn't, since a job that
    doesn't exist anywhere in the new city obviously can't carry
    over. Children (profession "Child") and the already-Unemployed
    skip the employment step entirely.
    """

    from Data.Database.repository import get_npc, save_npc, find_open_building, get_city
    from Data.NPCS.npc_generator import get_building_type, load_json, PROFESSION_BUILDINGS

    npc = get_npc(conn, npc_id)

    new_home = find_open_building(conn, new_city_id, "House", "home_id")

    if not new_home:
        destination_kingdom_id = get_city(conn, new_city_id)["kingdom"]
        _, new_home = find_open_house_in_kingdom(conn, destination_kingdom_id)

    npc["city_id"] = new_city_id

    if new_home:
        npc["home"] = new_home["id"]
        npc["location"] = new_home["id"]

    if npc["profession"] not in ("Unemployed", "Child"):

        profession_buildings = load_json(PROFESSION_BUILDINGS)
        building_type = get_building_type(npc["profession"], profession_buildings)

        new_workplace = (
            find_open_building(conn, new_city_id, building_type, "workplace_id")
            if building_type else None
        )

        if new_workplace:
            npc["workplace"] = new_workplace["id"]
        else:
            npc["profession"] = "Unemployed"
            npc["workplace"] = None

    save_npc(conn, npc)

    return npc


def consider_migration(conn, world_id):
    """
    Monthly: NPCs who are genuinely desperate (high stress) -- and
    whose whole household is too, if they have one, since this is a
    family decision, not a solo one -- have a small chance of
    deciding to permanently relocate to a meaningfully happier
    kingdom, provided the household (pooled together) can afford it,
    and there's actually a place to live once they arrive.

    This is meant as the real alternative to Civil War: rather than a
    population's despair only ever building toward revolt, families
    who can afford to leave, do -- so a kingdom that ever DOES cross
    Civil War's thresholds has already lost whoever had the means to
    leave, not just accumulated grievance with nowhere for it to go.

    Dispatches each family member as their own trip (purpose
    "Migration") to the same destination, so they travel and arrive
    together; the actual relocation happens when each trip arrives
    (see advance_npc_travels in travel.py).

    Returns how many households actually started migrating, for
    testing/logging.
    """

    from Data.Database.repository import get_world_kingdoms, get_kingdom_cities, get_city_npcs
    from Data.Infrastructure.travel import get_traveling_npc_ids, dispatch_npc_travel

    kingdoms = get_world_kingdoms(conn, world_id)

    kingdom_happiness = {}

    for kingdom in kingdoms:
        living = [
            npc for city in get_kingdom_cities(conn, kingdom["id"])
            for npc in get_city_npcs(conn, city["id"]) if npc["alive"]
        ]
        if living:
            kingdom_happiness[kingdom["id"]] = sum(n["needs"]["happiness"] for n in living) / len(living)

    traveling_ids = get_traveling_npc_ids(conn, world_id)

    migrated_households = 0
    already_handled_ids = set()

    for kingdom in kingdoms:

        home_happiness = kingdom_happiness.get(kingdom["id"])
        if home_happiness is None:
            continue

        candidates = [
            (other_id, happiness) for other_id, happiness in kingdom_happiness.items()
            if other_id != kingdom["id"] and happiness >= home_happiness + MIGRATION_MIN_HAPPINESS_ADVANTAGE
        ]

        if not candidates:
            continue

        candidate_ids = [c[0] for c in candidates]
        candidate_weights = [c[1] for c in candidates]

        for city in get_kingdom_cities(conn, kingdom["id"]):
            for npc in get_city_npcs(conn, city["id"]):

                if not npc["alive"] or npc["id"] in traveling_ids or npc["id"] in already_handled_ids:
                    continue

                if npc["needs"]["stress"] < MIGRATION_MIN_STRESS:
                    continue

                if random.random() >= MIGRATION_CHANCE_PER_MONTH:
                    continue

                family = get_family_members(conn, npc)

                already_handled_ids.update(m["id"] for m in family)
                
                if any(m.get("title") in RULING_TITLES for m in family):
                    continue

                family_avg_stress = sum(m["needs"]["stress"] for m in family) / len(family)
                if family_avg_stress < MIGRATION_MIN_STRESS:
                    continue

                family_gold = sum(m["gold"] for m in family)
                required_gold = MIGRATION_MIN_GOLD_SOLO + (len(family) - 1) * MIGRATION_GOLD_PER_ADDITIONAL_MEMBER

                if family_gold < required_gold:
                    continue

                destination_kingdom_id = random.choices(candidate_ids, weights=candidate_weights, k=1)[0]

                dest_city_id, dest_house = find_open_house_in_kingdom(conn, destination_kingdom_id)

                if dest_city_id is None:
                    continue

                from Data.Database.repository import save_npc

                for member in family:
                    share = round(required_gold * (member["gold"] / family_gold), 2)
                    member["gold"] = round(member["gold"] - share, 2)
                    save_npc(conn, member)
                    dispatch_npc_travel(conn, member, dest_city_id, purpose="Migration")

                migrated_households += 1

    return migrated_households
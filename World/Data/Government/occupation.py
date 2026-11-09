import random

# Occupation. A town taken by a campaign (Data.Government.campaigns) is held
# by a garrison: a quarter of the surviving army (soldiers before levies)
# stays behind and moves in. For a year the town is occupied:
#
#   * its own people resent it -- each month they lose happiness and gain stress;
#   * each month they may rise, if they can muster at least half the
#     garrison's strength: a tenth of the townsfolk plus the town's own
#     soldiers, armed from its stores. The odds follow strength.
#       - freed: the town -- with its county, if it is a county seat -- returns
#         to its old crown, and the garrison is killed or driven out;
#       - crushed: the rebels die, and the resentment goes on;
#   * after OCCUPATION_DAYS with no successful rising, the occupation ends:
#     the town belongs to its conqueror, and the garrison settles there.
#
# Risings and integration are logged; a town won back is chronicled by both.

GARRISON_SHARE = 0.25
OCCUPATION_DAYS = 360
RESENTMENT_HAPPINESS, RESENTMENT_STRESS = 5, 5     # each month, for the town's own people
REBEL_SHARE = 0.10                                 # of the townsfolk who are not soldiers
RISING_CHANCE = 0.15                               # each month, if they are strong enough
MIN_REBEL_STRENGTH = 0.5                           # ...that is, at least this share of the garrison's strength


def occupied(conn, city_id):
    row = conn.execute("SELECT * FROM occupations WHERE city_id = ? AND status = 'Occupied'", (city_id,)).fetchone()
    return dict(row) if row else None


def garrison_of(conn, occupation_id):
    """The garrison's living soldiers (NPC dicts)."""
    from Data.Database.repository import get_npc
    ids = [r[0] for r in conn.execute("SELECT npc_id FROM occupation_garrison WHERE occupation_id = ?", (occupation_id,))]
    return [n for n in (get_npc(conn, i) for i in ids) if n and n["alive"]]


def begin_occupation(conn, city_id, occupier_id, former_id, army_id, day):
    """
    A campaign has taken the town: a quarter of the army's survivors stay
    (soldiers before levies), move in, and leave the army. Returns the
    occupation, or None if no one is left to hold it.
    """

    from Data.Infrastructure.migration import relocate_npc
    from Data.Database.repository import get_npc, save_npc, create_event

    survivors = [dict(r) for r in conn.execute(
        """SELECT m.npc_id, m.levied FROM army_members m JOIN npcs n ON n.id = m.npc_id
           WHERE m.army_id = ? AND n.alive = 1 ORDER BY m.levied, m.npc_id""", (army_id,))]
    count = max(1, round(len(survivors) * GARRISON_SHARE)) if survivors else 0
    garrison = [s["npc_id"] for s in survivors[:count]]
    if not garrison:
        return None

    cursor = conn.execute(
        "INSERT INTO occupations (city_id, occupier_kingdom_id, former_kingdom_id, since_day, status) VALUES (?, ?, ?, ?, 'Occupied')",
        (city_id, occupier_id, former_id, day))
    occupation_id = cursor.lastrowid

    for npc_id in garrison:
        profession = get_npc(conn, npc_id)["profession"]
        relocate_npc(conn, npc_id, city_id)
        npc = get_npc(conn, npc_id)
        if npc["profession"] != profession:          # a garrison soldier stays a soldier, barracks or none
            npc["profession"] = profession
            npc["workplace"] = None
            save_npc(conn, npc)
        conn.execute("DELETE FROM army_members WHERE npc_id = ?", (npc_id,))
        conn.execute("INSERT INTO occupation_garrison (occupation_id, npc_id) VALUES (?, ?)", (occupation_id, npc_id))

    town = conn.execute("SELECT name FROM cities WHERE id = ?", (city_id,)).fetchone()["name"]
    occupier = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (occupier_id,)).fetchone()["name"]
    create_event(conn, "Occupation", f"{occupier} left {len(garrison)} soldiers to hold {town}.", city_id=city_id, kingdom_id=occupier_id)
    conn.commit()
    return dict(conn.execute("SELECT * FROM occupations WHERE id = ?", (occupation_id,)).fetchone())


def rise(conn, occupation):
    """The townsfolk rise against the garrison. Returns True if the town is freed."""

    from Data.Combat.combat import get_combat_power, apply_casualties, side_power, DEFENDER_PROFESSIONS, CIVIL_WAR_ARMED_REBEL_BONUS
    from Data.Combat.combat import transfer_conquest
    from Data.Events.events import death_event
    from Data.Database.repository import get_npc, create_event, get_kingdom, get_city
    from Data.History.chronicle import write_entry, current_date

    town = get_city(conn, occupation["city_id"])
    garrison = garrison_of(conn, occupation["id"])
    garrison_ids = {g["id"] for g in garrison}
    natives = [n for n in (get_npc(conn, r[0]) for r in conn.execute(
        "SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND profession != 'Child'", (town["id"],))) if n["id"] not in garrison_ids]
    fighters = [n for n in natives if n["profession"] in DEFENDER_PROFESSIONS]
    civilians = [n for n in natives if n["profession"] not in DEFENDER_PROFESSIONS]
    random.shuffle(civilians)
    rebels = fighters + civilians[:round(len(civilians) * REBEL_SHARE)]
    weapons = int(town["resources"].get("Weapons", 0))
    rebel_power = sum(get_combat_power(n) for n in rebels) + CIVIL_WAR_ARMED_REBEL_BONUS * min(weapons, len(rebels))
    garrison_power = side_power(garrison)

    freed = rebel_power > 0 and (garrison_power <= 0 or random.random() < rebel_power / (rebel_power + garrison_power))
    for npc in apply_casualties(conn, rebels, won=freed):
        death_event(conn, npc, "Uprising", kingdom_id=occupation["occupier_kingdom_id"])
    for npc in apply_casualties(conn, garrison, won=not freed):
        death_event(conn, npc, "Uprising", kingdom_id=occupation["occupier_kingdom_id"])

    occupier = get_kingdom(conn, occupation["occupier_kingdom_id"])
    former = get_kingdom(conn, occupation["former_kingdom_id"])

    if not freed:
        create_event(conn, "Uprising", f"The people of {town['name']} rose against the garrison of {occupier['name']}, and were crushed.",
                     city_id=town["id"], kingdom_id=occupier["id"])
        conn.commit()
        return False

    # freed: back to the old crown, county and all
    transfer_conquest(conn, town, former, occupier)
    for soldier in garrison_of(conn, occupation["id"]):           # survivors are driven home
        from Data.Infrastructure.migration import relocate_npc
        if occupier["capital"]:
            relocate_npc(conn, soldier["id"], occupier["capital"])
    conn.execute("UPDATE occupations SET status = 'Freed' WHERE id = ?", (occupation["id"],))
    create_event(conn, "Uprising", f"The people of {town['name']} rose, drove out the garrison of {occupier['name']}, "
                                   f"and returned to {former['name']}.", city_id=town["id"], kingdom_id=former["id"])
    year, season = current_date(conn, former["world_id"])
    write_entry(conn, former["id"], "Uprising", "Uprising (freed)", year, season,
                {"kingdom": former["name"], "other": occupier["name"], "city": town["name"]}, seed_parts=(occupation["id"],))
    write_entry(conn, occupier["id"], "Uprising", "Uprising (lost)", year, season,
                {"kingdom": occupier["name"], "other": former["name"], "city": town["name"]}, seed_parts=(occupation["id"],))
    conn.commit()
    return True


def occupations_monthly(conn, world_id, day):
    """
    Monthly, for every occupied town: resentment, perhaps a rising, and --
    after a year -- integration. Returns what happened.
    """

    from Data.Database.repository import create_event
    from Data.Combat.combat import side_power

    happened = []
    for occupation in conn.execute(
        """SELECT o.* FROM occupations o JOIN kingdoms k ON k.id = o.occupier_kingdom_id
           WHERE k.world_id = ? AND o.status = 'Occupied'""", (world_id,)).fetchall():
        occupation = dict(occupation)
        town = conn.execute("SELECT name, kingdom_id FROM cities WHERE id = ?", (occupation["city_id"],)).fetchone()

        if town["kingdom_id"] != occupation["occupier_kingdom_id"]:          # lost some other way
            conn.execute("UPDATE occupations SET status = 'Ended' WHERE id = ?", (occupation["id"],))
            continue

        if day - occupation["since_day"] >= OCCUPATION_DAYS:
            conn.execute("UPDATE occupations SET status = 'Integrated' WHERE id = ?", (occupation["id"],))
            create_event(conn, "Occupation", f"After a year under its garrison, {town['name']} is counted part of the realm.",
                         city_id=occupation["city_id"], kingdom_id=occupation["occupier_kingdom_id"])
            happened.append((occupation["id"], "integrated"))
            continue

        garrison_ids = {g["id"] for g in garrison_of(conn, occupation["id"])}
        marks = ",".join("?" * len(garrison_ids)) or "NULL"
        conn.execute(
            f"""UPDATE npcs SET needs_happiness = MAX(0, needs_happiness - ?), needs_stress = MIN(100, needs_stress + ?)
                WHERE city_id = ? AND alive = 1 AND id NOT IN ({marks})""",
            (RESENTMENT_HAPPINESS, RESENTMENT_STRESS, occupation["city_id"], *garrison_ids))

        fallen = conn.execute("SELECT status FROM kingdoms WHERE id = ?", (occupation["former_kingdom_id"],)).fetchone()[0] == "Fallen"
        if not fallen and random.random() < RISING_CHANCE:          # a fallen kingdom has no crown to rise back to
            from Data.Database.repository import get_npc
            from Data.Combat.combat import get_combat_power, DEFENDER_PROFESSIONS
            garrison_power = side_power(garrison_of(conn, occupation["id"]))
            natives = [get_npc(conn, r[0]) for r in conn.execute(
                "SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND profession != 'Child'", (occupation["city_id"],))]
            natives = [n for n in natives if n["id"] not in garrison_ids]
            soldiers = sum(get_combat_power(n) for n in natives if n["profession"] in DEFENDER_PROFESSIONS)
            civilians = [n for n in natives if n["profession"] not in DEFENDER_PROFESSIONS]
            estimate = soldiers + sum(get_combat_power(n) for n in civilians) * REBEL_SHARE
            if estimate >= MIN_REBEL_STRENGTH * garrison_power:
                happened.append((occupation["id"], "freed" if rise(conn, occupation) else "crushed"))

    conn.commit()
    return happened
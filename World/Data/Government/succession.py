# Succession. Crowns and counties stay in the family as long as they can.
# When a ruler dies, the title passes down this line, the first living and
# eligible person in it taking the title:
#
#   1. their children, eldest first
#   2. their siblings, eldest first
#   3. their spouse
#   4. the reigning King or Queen's siblings (for the crown itself, the same as 2)
#   5. their spouse's siblings
#   6. other close kin, eldest to youngest: grandchildren, nieces and nephews,
#      parents, aunts and uncles, cousins
#   7. failing all of these, the people: someone new is chosen, and a new
#      house begins (Data.Government.government.assign_city_ruler).
#
# A child under ADULT_AGE still inherits; their surviving parent -- or failing
# that, the first adult in the line, or with no family left the realm's eldest
# Count or Countess -- rules as regent until they come of age.
# A ruler deposed by revolt has no heirs: the throne goes to the people.

ADULT_AGE = 16

MONARCH_TITLES = ("King", "Queen", "Doge")   # no one heads two realms
LORD_TITLES = ("Count", "Countess")


# ------------------------------------------------------------------ family

def _ids(conn, sql, params):
    return [row[0] for row in conn.execute(sql, params)]


def children_of(conn, npc_id):
    """All children, living or dead, eldest first."""
    return _ids(conn, "SELECT id FROM npcs WHERE parent_a_id = ? OR parent_b_id = ? ORDER BY age DESC, id", (npc_id, npc_id))


def parents_of(conn, npc_id):
    row = conn.execute("SELECT parent_a_id, parent_b_id FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    return [p for p in (row["parent_a_id"], row["parent_b_id"]) if p is not None] if row else []


def siblings_of(conn, npc_id):
    """Everyone sharing a parent with this person, living or dead, eldest first."""
    parents = parents_of(conn, npc_id)
    if not parents:
        return []
    marks = ",".join("?" * len(parents))
    return _ids(conn, f"""SELECT id FROM npcs WHERE id != ? AND (parent_a_id IN ({marks}) OR parent_b_id IN ({marks}))
                          ORDER BY age DESC, id""", (npc_id, *parents, *parents))


def spouse_of(conn, npc_id):
    row = conn.execute("SELECT spouse_id FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    return row["spouse_id"] if row and row["spouse_id"] else None


def other_kin(conn, npc_id):
    """Grandchildren, nieces and nephews, parents, aunts and uncles, cousins."""
    kin = set()
    for child in children_of(conn, npc_id):
        kin.update(children_of(conn, child))
    for sibling in siblings_of(conn, npc_id):
        kin.update(children_of(conn, sibling))
    parents = parents_of(conn, npc_id)
    kin.update(parents)
    for parent in parents:
        for aunt_or_uncle in siblings_of(conn, parent):
            kin.add(aunt_or_uncle)
            kin.update(children_of(conn, aunt_or_uncle))
    kin.discard(npc_id)
    return list(kin)


def line_of_succession(conn, deceased_id, monarch_id=None):
    """Everyone in the line, in order (living or dead -- callers filter)."""

    line = []
    line += children_of(conn, deceased_id)
    line += siblings_of(conn, deceased_id)
    spouse = spouse_of(conn, deceased_id)
    if spouse:
        line.append(spouse)
    if monarch_id and monarch_id != deceased_id:
        line += siblings_of(conn, monarch_id)
    if spouse:
        line += siblings_of(conn, spouse)

    kin = other_kin(conn, deceased_id)
    if kin:
        marks = ",".join("?" * len(kin))
        line += _ids(conn, f"SELECT id FROM npcs WHERE id IN ({marks}) ORDER BY age DESC, id", tuple(kin))

    seen, ordered = set(), []
    for npc_id in line:
        if npc_id not in seen and npc_id != deceased_id:
            seen.add(npc_id)
            ordered.append(npc_id)
    return ordered


def relation_word(conn, heir_id, deceased_id):
    """How the heir is related to the person they inherit from, in words."""
    if heir_id in children_of(conn, deceased_id):
        return "parent"                      # the deceased was the heir's parent
    if heir_id in siblings_of(conn, deceased_id):
        return "sibling"
    if heir_id == spouse_of(conn, deceased_id):
        return "spouse"
    if heir_id in parents_of(conn, deceased_id):
        return "child"
    return "kin"


# ------------------------------------------------------------------ choosing

def _eligible(npc, for_crown):
    """Alive, and not already holding a title they cannot hold with this one:
    no one is monarch of two realms, and no lord holds two counties (an heir
    to the crown may be a lord -- they give up their county)."""
    if not npc or not npc["alive"]:
        return False
    if npc["title"] in MONARCH_TITLES:
        return False
    if not for_crown and npc["title"] in LORD_TITLES:
        return False
    return True


def choose_heir(conn, deceased_id, for_crown, monarch_id=None):
    """
    (heir, regent) for a title the deceased held: the first eligible person
    in the line of succession, and -- if they are under ADULT_AGE -- a regent
    to rule for them. (None, None) when the line has no one left.
    """

    from Data.Database.repository import get_npc

    for npc_id in line_of_succession(conn, deceased_id, monarch_id):
        npc = get_npc(conn, npc_id)
        if _eligible(npc, for_crown):
            regent = choose_regent(conn, npc, deceased_id, monarch_id) if npc["age"] < ADULT_AGE else None
            return npc, regent

    return None, None


def choose_regent(conn, ward, deceased_id=None, monarch_id=None):
    """Who rules for a child: their surviving parent if an adult, otherwise
    the first adult in the line of succession (never a reigning monarch);
    with no family left, the realm's eldest Count or Countess."""

    from Data.Database.repository import get_npc

    candidates = [p for p in parents_of(conn, ward["id"]) if p != deceased_id]
    if deceased_id:
        candidates += line_of_succession(conn, deceased_id, monarch_id)
    candidates += line_of_succession(conn, ward["id"], monarch_id)

    for npc_id in candidates:
        if npc_id == ward["id"]:
            continue
        npc = get_npc(conn, npc_id)
        if npc and npc["alive"] and npc["age"] >= ADULT_AGE and npc["title"] not in MONARCH_TITLES:
            return npc

    # No family left at all: the realm's eldest great lord governs, as a
    # council of nobles would. Only with no lords either does the child rule alone.
    elder = conn.execute(
        """SELECT n.id FROM provinces p JOIN npcs n ON n.id = p.lord_id
           JOIN cities c ON c.id = ? WHERE p.kingdom_id = c.kingdom_id
           AND n.alive = 1 AND n.age >= ? AND n.id != ? ORDER BY n.age DESC, n.id LIMIT 1""",
        (ward["city_id"], ADULT_AGE, ward["id"])
    ).fetchone()
    return get_npc(conn, elder["id"]) if elder else None


# ------------------------------------------------------------------ taking a title

def vacate_seats(conn, npc, except_city_id):
    """Someone who has just taken a greater title gives up any city they
    ruled: a county passes down their own line at once; a mayoralty is left
    for the next month's choosing."""

    from Data.Government.provinces import seat_province
    from Data.Database.repository import get_city

    for row in conn.execute("SELECT id FROM cities WHERE ruler_id = ? AND id != ?", (npc["id"], except_city_id)).fetchall():
        province = seat_province(conn, row["id"])
        if province:
            inherit_county(conn, province, get_city(conn, row["id"]), npc)
        else:
            conn.execute("UPDATE cities SET ruler_id = NULL WHERE id = ?", (row["id"],))


def inherit_county(conn, province, seat_city, previous):
    """The county passes down the line of `previous` (dead, or gone to a
    greater title). Returns the new lord, or None if the line is empty."""

    from Data.Government.provinces import install_lord

    monarch = conn.execute("SELECT ruler_id FROM kingdoms WHERE id = ?", (province["kingdom_id"],)).fetchone()["ruler_id"]
    heir, regent = choose_heir(conn, previous["id"], for_crown=False, monarch_id=monarch)
    if not heir:
        return None
    lord = install_lord(conn, province, seat_city, heir, inherited_from=previous, regent=regent)
    return lord


def inherit_crown(conn, capital_city, previous):
    """
    The crown passes down the dead monarch's line: the heir gives up any city
    they ruled, moves to the capital, and is crowned King or Queen; a child
    monarch gets a regent (logged and chronicled). Returns the new monarch,
    or None if the line is empty (then the people choose).
    """

    from Data.Database.repository import save_npc, get_npc, create_event
    from Data.Infrastructure.migration import relocate_npc
    from Data.Events.events import coronation_event
    from Data.History.chronicle import write_entry, current_date

    heir, regent = choose_heir(conn, previous["id"], for_crown=True, monarch_id=previous["id"])
    if not heir:
        return None

    vacate_seats(conn, heir, capital_city["id"])

    if heir["city_id"] != capital_city["id"]:
        relocate_npc(conn, heir["id"], capital_city["id"])
        heir = get_npc(conn, heir["id"])

    heir["title"] = "King" if heir["gender"] == "Male" else "Queen"
    save_npc(conn, heir)

    kingdom = conn.execute("SELECT id, name, world_id FROM kingdoms WHERE id = ?", (capital_city["kingdom"],)).fetchone()
    conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (heir["id"], capital_city["id"]))
    # the kingdom's record names its new monarch at once (the monthly tick
    # writes the same a moment later; nothing in between may see the dead one)
    conn.execute("UPDATE kingdoms SET ruler_id = ?, regent_id = ? WHERE id = ?",
                 (heir["id"], regent["id"] if regent else None, kingdom["id"]))

    coronation_event(conn, heir, capital_city)

    if regent:
        create_event(conn, "Regency",
                     f"{heir['name']} is only {heir['age']}, so {regent['name']} rules {kingdom['name']} as regent until they come of age.",
                     city_id=capital_city["id"], kingdom_id=kingdom["id"])
        year, season = current_date(conn, kingdom["world_id"])
        write_entry(conn, kingdom["id"], "Regency", "Regency Began", year, season,
                    {"kingdom": kingdom["name"], "ruler": heir["name"], "title": heir["title"],
                     "regent": regent["name"], "age": heir["age"]}, seed_parts=(heir["id"],))

    conn.commit()
    return heir


def check_regencies(conn, world_id):
    """
    Monthly: a ward who has come of age rules in their own right; a regent who
    has died is replaced. Logged (and, for crowns, chronicled). Returns how
    many regencies changed.
    """

    from Data.Database.repository import get_npc, create_event
    from Data.History.chronicle import write_entry, current_date

    changed = 0
    year, season = current_date(conn, world_id)

    holders = [("kingdoms", dict(k)) for k in conn.execute(
        "SELECT id, name, ruler_id, regent_id FROM kingdoms WHERE world_id = ? AND regent_id IS NOT NULL", (world_id,))]
    holders += [("provinces", dict(p)) for p in conn.execute(
        """SELECT p.id, p.name, p.lord_id ruler_id, p.regent_id, p.kingdom_id FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id
           WHERE k.world_id = ? AND p.regent_id IS NOT NULL""", (world_id,))]

    for table, holder in holders:
        ward = get_npc(conn, holder["ruler_id"]) if holder["ruler_id"] else None
        regent = get_npc(conn, holder["regent_id"])
        kingdom_id = holder["id"] if table == "kingdoms" else holder["kingdom_id"]
        realm = holder["name"]

        if not ward or not ward["alive"]:
            conn.execute(f"UPDATE {table} SET regent_id = NULL WHERE id = ?", (holder["id"],))
            changed += 1
        elif ward["age"] >= ADULT_AGE:
            conn.execute(f"UPDATE {table} SET regent_id = NULL WHERE id = ?", (holder["id"],))
            create_event(conn, "Came of Age", f"{ward['name']} came of age and now rules {realm} in their own right.",
                         kingdom_id=kingdom_id)
            if table == "kingdoms":
                write_entry(conn, kingdom_id, "Came of Age", "Came of Age", year, season,
                            {"kingdom": realm, "ruler": ward["name"], "title": ward["title"] or "monarch",
                             "regent": regent["name"] if regent else "the regency"}, seed_parts=(ward["id"],))
            changed += 1
        elif not regent or not regent["alive"]:
            new = choose_regent(conn, ward)
            conn.execute(f"UPDATE {table} SET regent_id = ? WHERE id = ?", (new["id"] if new else None, holder["id"]))
            create_event(conn, "Regency",
                         f"{new['name']} now rules {realm} as regent for the young {ward['name']}." if new
                         else f"{realm} has no regent left to rule for the young {ward['name']}.", kingdom_id=kingdom_id)
            changed += 1

    conn.commit()
    return changed
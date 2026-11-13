import json
import random
from pathlib import Path

# Family names. Everyone belongs to a family -- a name with a founding year --
# and is known by their given name and their family's: "Samantha Reed".
#
#   Children take the name their family uses (the household's).
#   When two people marry, the couple takes the OLDER family's name: the line
#   founded longest ago (at generation, families are founded at random over
#   the FOUNDED_YEARS_AGO before the world begins; a family founded during
#   play is the youngest of all).
#   Anyone without a family -- at generation, in an older world, or someone
#   who entered the story some other way -- joins their parents' family, else
#   their spouse's, else founds a new one (ensure_family_names).
#
# A lord's house takes their family's name: "House Reed".

FAMILY_NAMES = Path(__file__).resolve().parents[1] / "Names" / "FamilyNames.json"
FOUNDED_YEARS_AGO = (1, 400)


def _world_of(conn, npc_id):
    row = conn.execute("SELECT k.world_id FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id WHERE n.id = ?",
                       (npc_id,)).fetchone()
    return row[0] if row else None


def _current_year(conn, world_id):
    return conn.execute("SELECT clock_year FROM worlds WHERE id = ?", (world_id,)).fetchone()[0]


def new_family(conn, world_id, of_old=False):
    """A new family: a name no family in this world bears yet, while any are left -- after that,
    unrelated families share names, as in life (many Smiths, no kin). `of_old`: founded in the past."""
    names = json.load(open(FAMILY_NAMES, encoding="utf-8"))
    taken = {r[0] for r in conn.execute("SELECT name FROM families WHERE world_id = ?", (world_id,))}
    free = [n for n in names if n not in taken]
    name = random.choice(free or names)
    year = _current_year(conn, world_id)
    founded = year - random.randint(*FOUNDED_YEARS_AGO) if of_old else year
    return conn.execute("INSERT INTO families (world_id, name, founded_year) VALUES (?, ?, ?)", (world_id, name, founded)).lastrowid


def join_family(conn, npc_id, family_id):
    """Puts a person in a family: their full name becomes their given name and the family's."""
    row = conn.execute("SELECT name, given_name FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    given = row["given_name"] or row["name"]
    family = conn.execute("SELECT name FROM families WHERE id = ?", (family_id,)).fetchone()["name"]
    conn.execute("UPDATE npcs SET given_name = ?, family_id = ?, name = ? WHERE id = ?", (given, family_id, f"{given} {family}", npc_id))


def family_of(conn, npc_id):
    row = conn.execute("SELECT family_id FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    return row["family_id"] if row else None


def older(conn, family_a, family_b):
    """The older of two families: founded earlier (ties: the first made)."""
    rows = {r["id"]: r["founded_year"] for r in conn.execute(
        "SELECT id, founded_year FROM families WHERE id IN (?, ?)", (family_a, family_b))}
    return min((family_a, family_b), key=lambda f: (rows.get(f, 10 ** 9), f))


def marry_names(conn, npc_a, npc_b):
    """A married couple takes the older family's name (or the one family they have between them)."""
    fa, fb = family_of(conn, npc_a["id"]), family_of(conn, npc_b["id"])
    if fa is None and fb is None:
        return None
    chosen = fa if fb is None else fb if fa is None else older(conn, fa, fb)
    for npc in (npc_a, npc_b):
        join_family(conn, npc["id"], chosen)
        npc["name"] = conn.execute("SELECT name FROM npcs WHERE id = ?", (npc["id"],)).fetchone()["name"]
    return chosen


def name_child(conn, child, parent_a, parent_b=None):
    """A newborn takes the name their family uses (their parents' household)."""
    family = family_of(conn, parent_a["id"]) or (family_of(conn, parent_b["id"]) if parent_b else None)
    if family:
        join_family(conn, child["id"], family)
        child["name"] = conn.execute("SELECT name FROM npcs WHERE id = ?", (child["id"],)).fetchone()["name"]


def ensure_family_names(conn, world_id, of_old=False):
    """
    Everyone living without a family joins their parents' family, else their
    spouse's; a couple with none founds one together; anyone else founds
    their own. `of_old`: new families were founded in the past (generation, older worlds).
    Returns how many people were given a family.
    """
    named = 0
    people = [dict(r) for r in conn.execute(
        """SELECT n.id, n.spouse_id, n.parent_a_id, n.parent_b_id FROM npcs n JOIN cities c ON c.id = n.city_id
           JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND n.alive = 1 AND n.family_id IS NULL
           ORDER BY (n.parent_a_id IS NOT NULL), n.id""", (world_id,))]
    for person in people:                              # adults (no parents recorded) first, then children
        if family_of(conn, person["id"]):
            continue                                   # named already, with a spouse earlier in this pass
        family = None
        for parent in (person["parent_a_id"], person["parent_b_id"]):
            if parent and family_of(conn, parent):
                family = family_of(conn, parent)
                break
        if family is None and person["spouse_id"]:
            family = family_of(conn, person["spouse_id"])
        if family is None:
            family = new_family(conn, world_id, of_old)
            if person["spouse_id"]:
                join_family(conn, person["spouse_id"], family)
                named += 1
        join_family(conn, person["id"], family)
        named += 1
    conn.commit()
    return named
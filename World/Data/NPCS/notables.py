# Notables: the people the Decision Engine follows closely (piece 2). They review their lives each season,
# their decisions are logged, and they alone hold grievance and trust toward whole houses and kingdoms
# (Data.NPCS.regard). Everyone else keeps their grudges personal.
#
#   rulers and lords (a monarch's or a count's title), mayors and doges
#   heirs and noble kin: the children of rulers and lords
#   knights and captains
#   champions: renown CHAMPION_RENOWN or more (Data.Combat.nemesis)
#   guild heads: the masters of the thieves' guilds
#
# A noble house is a family with a living ruler or lord in it ("House Reed").

from Data.Combat.nemesis import MONARCH_TITLES, LORD_TITLES, CHAMPION_RENOWN

NOBLE_TITLES = MONARCH_TITLES + LORD_TITLES
SWORD_TRADES = ("Knight", "Captain")


def _marks(items):
    return ",".join("?" * len(items))


def notable_ids(conn, world_id):
    """The ids of every living notable in the world."""
    noble = _marks(NOBLE_TITLES)
    rows = conn.execute(
        f"""SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
            WHERE k.world_id = ? AND n.alive = 1 AND (
                n.title IS NOT NULL
                OR n.profession IN ({_marks(SWORD_TRADES)})
                OR n.renown >= ?
                OR n.id IN (SELECT master_id FROM thieves_guilds WHERE broken_day IS NULL AND master_id IS NOT NULL)
                OR n.parent_a_id IN (SELECT id FROM npcs WHERE alive = 1 AND title IN ({noble}))
                OR n.parent_b_id IN (SELECT id FROM npcs WHERE alive = 1 AND title IN ({noble})))""",
        (world_id, *SWORD_TRADES, CHAMPION_RENOWN, *NOBLE_TITLES, *NOBLE_TITLES)).fetchall()
    return [r[0] for r in rows]


def is_notable(conn, npc_id):
    """True if this person is a notable (see above)."""
    row = conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if not row or not row["alive"]:
        return False
    if row["title"] or row["profession"] in SWORD_TRADES or (row["renown"] or 0) >= CHAMPION_RENOWN:
        return True
    if conn.execute("SELECT 1 FROM thieves_guilds WHERE master_id = ? AND broken_day IS NULL", (npc_id,)).fetchone():
        return True
    parents = [p for p in (row["parent_a_id"], row["parent_b_id"]) if p]
    return bool(parents) and conn.execute(
        f"SELECT 1 FROM npcs WHERE id IN ({_marks(parents)}) AND alive = 1 AND title IN ({_marks(NOBLE_TITLES)})",
        (*parents, *NOBLE_TITLES)).fetchone() is not None


def house_of(conn, npc_id):
    """The noble house (family id) this person belongs to, or None if their family holds no rule."""
    row = conn.execute("SELECT family_id FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if not row or row["family_id"] is None:
        return None
    ruling = conn.execute(f"SELECT 1 FROM npcs WHERE family_id = ? AND alive = 1 AND title IN ({_marks(NOBLE_TITLES)}) LIMIT 1",
                          (row["family_id"], *NOBLE_TITLES)).fetchone()
    return row["family_id"] if ruling else None


def house_name(conn, family_id):
    row = conn.execute("SELECT name FROM families WHERE id = ?", (family_id,)).fetchone()
    return f"House {row['name']}" if row else "a fallen house"
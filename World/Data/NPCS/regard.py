# Grievance and trust (Decision Engine piece 2a), kept apart from liking (Data.Relationships): you can like a
# cousin and still not trust him.
#
# Grievance toward a PERSON is the grievances table the vendettas have always used (Data.Combat.nemesis):
# kin and close friends of the slain hold it, 0 to MAX_GRIEVANCE. Notables (Data.NPCS.notables) also hold
# grievance toward a whole HOUSE (a blood feud: half the grievance, when kin are slain by a member of a
# noble house) and toward a KINGDOM (half, when kin fall to its war or raid) -- kept here in `regard`.
#
# In a world with the Decision Engine, grievances fade each year by FADE x (1 - vengefulness/100): the
# forgiving (-100) lose 20% a year, the even-tempered 10%, the vengeful (+100) never forget. Below GONE a
# grievance is laid to rest. Older worlds keep their grievances as they always were.
#
# Trust runs -100 to +100. Part comes from ties, worked out live so it follows the family tree and
# friendships as they change (TIE_TRUST); part is earned and lost, kept here: a lord who rises against the
# crown loses the ruler's trust (BETRAYED, and BETRAYED_HOUSE toward their house); a lord who serves
# faithfully gains SERVICE a year, up to SERVICE_CAP. A ruler's trust in another kingdom follows kingdom
# favour (-100 to +100, Data.Database.repository.get_kingdom_favor).

FADE = 0.10
GONE = 10
HOUSE_SHARE = 0.5
KINGDOM_SHARE = 0.5
TIE_TRUST = {"spouse": 50, "parent": 40, "child": 40, "sibling": 30, "friend": 30}
FRIEND_LIKING = 70
BETRAYED = -60
BETRAYED_HOUSE = -30
SERVICE = 2
SERVICE_CAP = 30
KINDS = ("npc", "house", "kingdom")


def _clamp_trust(value):
    return max(-100.0, min(100.0, value))


def _engine(conn, npc_id):
    """True if this person's world runs the Decision Engine."""
    from Data.NPCS.personality import has_engine
    row = conn.execute("""SELECT k.world_id FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
                          WHERE n.id = ?""", (npc_id,)).fetchone()
    return bool(row) and has_engine(conn, row[0])


def hold(conn, holder_id, kind, target_id, amount, reason, day):
    """`holder_id` takes up `amount` more grievance toward a house or kingdom (capped like any grievance)."""
    from Data.Combat.nemesis import MAX_GRIEVANCE
    conn.execute(
        """INSERT INTO regard (holder_id, target_kind, target_id, grievance, trust, reason, since_day) VALUES (?, ?, ?, ?, 0, ?, ?)
           ON CONFLICT(holder_id, target_kind, target_id) DO UPDATE SET
               grievance = MIN(?, regard.grievance + excluded.grievance), reason = excluded.reason""",
        (holder_id, kind, target_id, amount, reason, day, MAX_GRIEVANCE))


def earn_trust(conn, holder_id, kind, target_id, delta, day, reason=None, cap=100):
    """`holder_id`'s earned trust toward a person, house or kingdom moves by `delta` (never past +/-cap)."""
    row = conn.execute("SELECT trust FROM regard WHERE holder_id = ? AND target_kind = ? AND target_id = ?",
                       (holder_id, kind, target_id)).fetchone()
    now = row["trust"] if row else 0.0
    new = _clamp_trust(now + delta)
    if delta > 0:
        new = min(new, max(now, cap))                   # earned trust never climbs past its cap
    conn.execute(
        """INSERT INTO regard (holder_id, target_kind, target_id, grievance, trust, reason, since_day) VALUES (?, ?, ?, 0, ?, ?, ?)
           ON CONFLICT(holder_id, target_kind, target_id) DO UPDATE SET trust = excluded.trust,
               reason = COALESCE(excluded.reason, regard.reason)""",
        (holder_id, kind, target_id, new, reason, day))
    return new


def tie(conn, holder_id, target_id):
    """What binds the holder to this person, as trust comes from it: 'spouse', 'parent' (the target is their
    parent), 'child', 'sibling', 'friend' or None."""
    h = conn.execute("SELECT spouse_id, parent_a_id, parent_b_id FROM npcs WHERE id = ?", (holder_id,)).fetchone()
    t = conn.execute("SELECT spouse_id, parent_a_id, parent_b_id FROM npcs WHERE id = ?", (target_id,)).fetchone()
    if not h or not t:
        return None
    if h["spouse_id"] == target_id or t["spouse_id"] == holder_id:
        return "spouse"
    if target_id in (h["parent_a_id"], h["parent_b_id"]):
        return "parent"
    if holder_id in (t["parent_a_id"], t["parent_b_id"]):
        return "child"
    if {h["parent_a_id"], h["parent_b_id"]} & {t["parent_a_id"], t["parent_b_id"]} - {None}:
        return "sibling"
    a, b = sorted((holder_id, target_id))
    rel = conn.execute("SELECT reputation FROM relationships WHERE npc_a_id = ? AND npc_b_id = ?", (a, b)).fetchone()
    if rel and rel["reputation"] >= FRIEND_LIKING:
        return "friend"
    return None


def trust(conn, holder_id, kind, target_id):
    """How far `holder_id` trusts a person ('npc'), a house or a kingdom: -100 to +100."""
    row = conn.execute("SELECT trust FROM regard WHERE holder_id = ? AND target_kind = ? AND target_id = ?",
                       (holder_id, kind, target_id)).fetchone()
    earned = row["trust"] if row else 0.0
    if kind == "npc":
        return _clamp_trust(TIE_TRUST.get(tie(conn, holder_id, target_id), 0) + earned)
    if kind == "kingdom":
        ruler = conn.execute("SELECT id FROM kingdoms WHERE ruler_id = ?", (holder_id,)).fetchone()
        if ruler:
            from Data.Database.repository import get_kingdom_favor
            return _clamp_trust(get_kingdom_favor(conn, ruler["id"], target_id) + earned)
    return _clamp_trust(earned)


def grievance(conn, holder_id, kind, target_id):
    """How much `holder_id` holds against a person ('npc'), a house or a kingdom: 0 to MAX_GRIEVANCE."""
    if kind == "npc":
        row = conn.execute("SELECT amount FROM grievances WHERE holder_id = ? AND target_id = ? AND settled = 0",
                           (holder_id, target_id)).fetchone()
        return row["amount"] if row else 0.0
    row = conn.execute("SELECT grievance FROM regard WHERE holder_id = ? AND target_kind = ? AND target_id = ?",
                       (holder_id, kind, target_id)).fetchone()
    return row["grievance"] if row else 0.0


def widen(conn, holder_id, killer_id, amount, reason, day, context=None):
    """A notable's grievance spreads past the killer: half against the killer's noble house (a blood feud),
    and half against the kingdom whose war or raid it was. Only in a world with the Decision Engine."""
    from Data.NPCS.notables import is_notable, house_of
    if not _engine(conn, holder_id) or not is_notable(conn, holder_id):
        return
    house = house_of(conn, killer_id)
    if house is not None and house != house_of(conn, holder_id):
        hold(conn, holder_id, "house", house, amount * HOUSE_SHARE, reason, day)
    if context in ("war", "raid", "battle", "siege", "uprising"):
        theirs = _kingdom_of(conn, killer_id)
        if theirs is not None and theirs != _kingdom_of(conn, holder_id):
            hold(conn, holder_id, "kingdom", theirs, amount * KINGDOM_SHARE, reason, day)


def _kingdom_of(conn, npc_id):
    row = conn.execute("SELECT c.kingdom_id FROM npcs n JOIN cities c ON c.id = n.city_id WHERE n.id = ?", (npc_id,)).fetchone()
    return row[0] if row else None


def betrayed(conn, ruler_id, lord_ids, day):
    """Lords rose against the crown: the ruler's trust in each falls by BETRAYED, and in their houses by BETRAYED_HOUSE."""
    from Data.NPCS.notables import house_of
    if not ruler_id or not _engine(conn, ruler_id):
        return
    houses = set()
    for lord in lord_ids:
        if lord and lord != ruler_id:
            earn_trust(conn, ruler_id, "npc", lord, BETRAYED, day, "rose against the crown")
            house = house_of(conn, lord)
            if house is not None:
                houses.add(house)
    mine = house_of(conn, ruler_id)
    for house in houses - {mine}:
        earn_trust(conn, ruler_id, "house", house, BETRAYED_HOUSE, day, "its lord rose against the crown")


def regard_year(conn, world_id):
    """Yearly, in a world with the Decision Engine: grievances fade by temper, and each lord who has served the
    crown another year earns a little of the ruler's trust. Returns {"faded", "forgotten", "served"}."""
    from Data.NPCS.personality import has_engine
    from Data.Government.edicts import today
    if not has_engine(conn, world_id):
        return {"faded": 0, "forgotten": 0, "served": 0}
    day = today(conn, world_id)
    faded = forgotten = 0
    for table, key, amount in (("grievances", "target_id", "amount"), ("regard", "target_kind, target_id", "grievance")):
        rows = conn.execute(
            f"""SELECT g.rowid AS rid, g.{amount} AS amount, h.vengefulness FROM {table} g JOIN npcs h ON h.id = g.holder_id
                JOIN cities c ON c.id = h.city_id JOIN kingdoms k ON k.id = c.kingdom_id
                WHERE k.world_id = ? AND g.{amount} > 0 {"AND g.settled = 0" if table == "grievances" else ""}""",
            (world_id,)).fetchall()
        for r in rows:
            left = r["amount"] * (1 - FADE * (1 - (r["vengefulness"] or 0) / 100))
            if left < GONE:
                left = 0
                forgotten += 1
            faded += 1
            if table == "grievances":
                conn.execute("UPDATE grievances SET amount = ?, settled = ? WHERE rowid = ?", (left, 1 if left == 0 else 0, r["rid"]))
            else:
                conn.execute("UPDATE regard SET grievance = ? WHERE rowid = ?", (left, r["rid"]))
    conn.execute("DELETE FROM regard WHERE grievance <= 0 AND trust = 0")
    served = 0
    for row in conn.execute(
            """SELECT k.ruler_id, n.id FROM provinces p JOIN npcs n ON n.id = p.lord_id JOIN kingdoms k ON k.id = p.kingdom_id
                WHERE k.world_id = ? AND n.alive = 1 AND k.ruler_id IS NOT NULL AND k.ruler_id != n.id""", (world_id,)).fetchall():
        earned = conn.execute("SELECT trust FROM regard WHERE holder_id = ? AND target_kind = 'npc' AND target_id = ?",
                              (row["ruler_id"], row["id"])).fetchone()
        if (earned["trust"] if earned else 0) < SERVICE_CAP:
            earn_trust(conn, row["ruler_id"], "npc", row["id"], SERVICE, day, "years of faithful service", cap=SERVICE_CAP)
            served += 1
    return {"faded": faded, "forgotten": forgotten, "served": served}
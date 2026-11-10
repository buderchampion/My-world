import random

# The nemesis engine, parts 1 and 2: kills, renown and grievances.
#
#   Kills. In every battle (raids, wars, civil wars, lords' risings, town
#   uprisings) each death is credited to a fighter of the other side --
#   stronger fighters, and champions twice over, claim more of them. Every
#   kill adds to the killer's tally and renown.
#
#   Notable people. Monarchs and Doges, Counts and Countesses, Mayors,
#   Knights, Captains, and champions -- fighters whose renown has reached
#   CHAMPION_RENOWN. Only THEIR deaths are recorded as killings (who slew
#   whom, where and when): the kind of death others avenge. Slaying one earns
#   renown by their rank.
#
#   Epithets. Great deeds earn a name: the Kingslayer (Queenslayer,
#   Dogeslayer), the Lordsbane, the Butcher of <town> (BUTCHER_KILLS or more
#   in one battle), the Dread (renown of DREAD_RENOWN). A grander epithet
#   replaces a lesser one, never the reverse.
#
#   Grievances. A slain notable's spouse, children, parents and siblings, and
#   their close friends, hold a grievance against the killer -- one-way, apart
#   from ordinary reputation, stronger the closer they were. What they do
#   about it comes with the engine's later parts (revenge in battle, duels,
#   assassins).

CHAMPION_RENOWN = 10
DREAD_RENOWN = 30
BUTCHER_KILLS = 4
KILL_RENOWN = 1
CHAMPION_KILL_WEIGHT = 2             # a champion claims kills twice as readily

MONARCH_TITLES = ("King", "Queen", "Doge")
LORD_TITLES = ("Count", "Countess")
RANK_RENOWN = {"monarch": 30, "lord": 15, "mayor": 5, "knight": 5, "captain": 5, "champion": 5}

GRIEVANCE = {"spouse": 100, "child": 80, "parent": 80, "sibling": 60, "friend": 40}
FRIEND_REPUTATION = 70               # a relationship at least this warm makes a close friend
MAX_GRIEVANCE = 200

EPITHET_RANK = {"the Dread": 1, "the Butcher": 2, "the Lordsbane": 3, "slayer": 4}


def renown_of(conn, npc_id):
    row = conn.execute("SELECT renown FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    return row["renown"] if row else 0


def rank_of(conn, npc):
    """What makes this person notable -- 'monarch', 'lord', 'mayor', 'knight', 'captain', 'champion' -- or None."""
    title = npc.get("title")
    if title in MONARCH_TITLES:
        return "monarch"
    if title in LORD_TITLES:
        return "lord"
    if title == "Mayor":
        return "mayor"
    if npc.get("profession") == "Knight":
        return "knight"
    if npc.get("profession") == "Captian":
        return "captain"
    if npc.get("id") and renown_of(conn, npc["id"]) >= CHAMPION_RENOWN:
        return "champion"
    return None


def display_name(conn, npc_id):
    """A person's name with their epithet, if they have earned one: 'Aldric the Kingslayer'."""
    row = conn.execute("SELECT name, epithet FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if not row:
        return "someone"
    return f"{row['name']} {row['epithet']}" if row["epithet"] else row["name"]


def _epithet_rank(epithet):
    if not epithet:
        return 0
    if epithet.endswith("slayer"):
        return EPITHET_RANK["slayer"]
    for key, rank in EPITHET_RANK.items():
        if epithet.startswith(key):
            return rank
    return 0


def grant_epithet(conn, npc_id, epithet):
    """Gives the epithet if it is grander than the one they bear. Returns True if granted."""
    current = conn.execute("SELECT epithet FROM npcs WHERE id = ?", (npc_id,)).fetchone()["epithet"]
    if _epithet_rank(epithet) <= _epithet_rank(current):
        return False
    conn.execute("UPDATE npcs SET epithet = ? WHERE id = ?", (epithet, npc_id))
    return True


def hold_grievances(conn, victim_id, killer_id, day):
    """The victim's kin and close friends take up a grievance against the killer. Returns how many did."""
    from Data.Government.succession import children_of, parents_of, siblings_of, spouse_of

    holders = {}
    spouse = spouse_of(conn, victim_id)
    if spouse:
        holders[spouse] = ("spouse", GRIEVANCE["spouse"])
    for kind, ids in (("child", children_of(conn, victim_id)), ("parent", parents_of(conn, victim_id)),
                      ("sibling", siblings_of(conn, victim_id))):
        for npc_id in ids:
            holders.setdefault(npc_id, (kind, GRIEVANCE[kind]))
    for row in conn.execute(
        "SELECT npc_a_id, npc_b_id FROM relationships WHERE (npc_a_id = ? OR npc_b_id = ?) AND reputation >= ?",
        (victim_id, victim_id, FRIEND_REPUTATION)).fetchall():
        friend = row["npc_b_id"] if row["npc_a_id"] == victim_id else row["npc_a_id"]
        holders.setdefault(friend, ("friend", GRIEVANCE["friend"]))

    victim_name = conn.execute("SELECT name FROM npcs WHERE id = ?", (victim_id,)).fetchone()["name"]
    count = 0
    for holder, (kind, amount) in holders.items():
        if holder == killer_id:
            continue                                  # no one holds a grievance against themselves
        alive = conn.execute("SELECT alive FROM npcs WHERE id = ?", (holder,)).fetchone()
        if not alive or not alive["alive"]:
            continue
        conn.execute(
            """INSERT INTO grievances (holder_id, target_id, amount, reason, since_day, settled) VALUES (?, ?, ?, ?, ?, 0)
               ON CONFLICT(holder_id, target_id) DO UPDATE SET amount = MIN(?, grievances.amount + excluded.amount), settled = 0""",
            (holder, killer_id, amount, f"slew their {'friend' if kind == 'friend' else kind_of_victim(kind)}, {victim_name}", day, MAX_GRIEVANCE))
        count += 1
    return count


def kind_of_victim(holder_kind):
    """How the holder would name the victim: a holder who is the victim's child lost a parent, and so on."""
    return {"spouse": "spouse", "child": "parent", "parent": "child", "sibling": "sibling"}[holder_kind]


def _credit(conn, dead, foes, city_id, context, day, kingdom_id):
    """Credits each of `dead` to one of `foes`. Returns the notable killings as (killer_id, victim_id, rank)."""
    from Data.Combat.combat import get_combat_power
    from Data.Database.repository import create_event

    foes = [f for f in foes if f.get("id") and f["id"] > 0]
    dead = [d for d in dead if d.get("id") and d["id"] > 0]
    if not foes or not dead:
        return []

    renown = {row["id"]: row["renown"] for row in conn.execute(
        f"SELECT id, renown FROM npcs WHERE id IN ({','.join('?' * len(foes))})", [f["id"] for f in foes])}
    weights = [max(0.1, get_combat_power(f)) * (CHAMPION_KILL_WEIGHT if renown.get(f["id"], 0) >= CHAMPION_RENOWN else 1) for f in foes]

    place = conn.execute("SELECT name FROM cities WHERE id = ?", (city_id,)).fetchone() if city_id else None
    tally, slayings = {}, []
    for victim in dead:
        killer = random.choices(foes, weights=weights)[0]
        tally[killer["id"]] = tally.get(killer["id"], 0) + 1
        gain = KILL_RENOWN
        rank = rank_of(conn, victim)
        if rank:
            gain += RANK_RENOWN[rank]
            conn.execute("INSERT INTO killings (killer_id, victim_id, day, city_id, context, victim_rank) VALUES (?, ?, ?, ?, ?, ?)",
                         (killer["id"], victim["id"], day, city_id, context, rank))
            hold_grievances(conn, victim["id"], killer["id"], day)
            title = victim.get("title") or victim.get("profession")
            title = "Captain" if title == "Captian" else title
            where = f" at {place['name']}" if place else ""
            create_event(conn, "Slaying", f"{display_name(conn, killer['id'])} slew {title} {victim['name']}{where}.",
                         participants=[killer["id"], victim["id"]], city_id=city_id, kingdom_id=kingdom_id)
            slayings.append((killer["id"], victim["id"], rank))
            if rank == "monarch":
                grant_epithet(conn, killer["id"], f"the {victim.get('title')}slayer")
            elif rank == "lord":
                grant_epithet(conn, killer["id"], "the Lordsbane")
        before = conn.execute("SELECT renown FROM npcs WHERE id = ?", (killer["id"],)).fetchone()["renown"]
        conn.execute("UPDATE npcs SET kills = kills + 1, renown = renown + ? WHERE id = ?", (gain, killer["id"]))
        if before < CHAMPION_RENOWN <= before + gain:
            create_event(conn, "Renown", f"{display_name(conn, killer['id'])} is now counted a champion.",
                         participants=[killer["id"]], city_id=city_id, kingdom_id=kingdom_id)
        if before < DREAD_RENOWN <= before + gain:
            grant_epithet(conn, killer["id"], "the Dread")

    for killer_id, kills in tally.items():
        if kills >= BUTCHER_KILLS and place:
            grant_epithet(conn, killer_id, f"the Butcher of {place['name']}")
    return slayings


def record_battle(conn, side_a, dead_a, side_b, dead_b, city_id=None, context="battle", kingdom_id=None):
    """
    After a battle: each side's dead are credited to fighters of the other.
    `side_a`/`side_b` are everyone who fought (the fallen included);
    `dead_a`/`dead_b` those who died. Returns the notable killings.
    """
    from Data.Government.edicts import today

    anyone = next((n for n in list(side_a) + list(side_b) if n.get("id") and n["id"] > 0), None)
    if anyone is None:
        return []
    where = city_id or conn.execute("SELECT city_id FROM npcs WHERE id = ?", (anyone["id"],)).fetchone()["city_id"]
    world = conn.execute("SELECT k.world_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE c.id = ?", (where,)).fetchone()
    if world is None:
        return []
    day = today(conn, world["world_id"])
    return (_credit(conn, dead_a, side_b, city_id, context, day, kingdom_id)
            + _credit(conn, dead_b, side_a, city_id, context, day, kingdom_id))
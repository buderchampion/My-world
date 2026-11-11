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
#   from ordinary reputation, stronger the closer they were.
#
# Part 3: revenge in battle. When two sides meet, anyone holding a grievance
# against a fighter of the other side seeks them out before the general
# fighting (strongest grievances first; each fighter duels at most once). The
# avenger's fury raises their strength by up to FURY at the greatest
# grievance; the loser dies. An avenger who wins settles the grievance, earns
# VENGEANCE_RENOWN and the name "the Avenger", and is chronicled. Whoever
# falls, their own kin take up a grievance against the one who slew them --
# a killing made in vengeance is always recorded -- so a feud can run on.
# (Peacetime revenge -- duels, journeys, training, assassins -- is part 4.)

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

EPITHET_RANK = {"the Avenger": 1, "the Dread": 2, "the Butcher": 3, "the Lordsbane": 4, "slayer": 5}

FURY = 1.0                           # an avenger's strength is raised by up to this share at MAX_GRIEVANCE
VENGEANCE_RENOWN = 3


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


def hold_grievances(conn, victim_id, killer_id, day, deed="slew"):
    """The victim's kin and close friends take up a grievance against the killer (or, with another `deed`,
    against whoever is to blame: "paid for the murder of"). Returns how many did."""
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
            (holder, killer_id, amount, f"{deed} their {'friend' if kind == 'friend' else kind_of_victim(kind)}, {victim_name}", day, MAX_GRIEVANCE))
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


def _kingdom_of(conn, npc_id):
    row = conn.execute("SELECT c.kingdom_id FROM npcs n JOIN cities c ON c.id = n.city_id WHERE n.id = ?", (npc_id,)).fetchone()
    return row["kingdom_id"] if row else None


def slay(conn, killer, victim, city_id, context, day, cause):
    """`killer` kills `victim` outside the battle's ordinary casualties (a duel, an assassin's knife).
    The victim dies and is mourned; the killing is recorded whatever the victim's rank; the victim's kin
    take up a grievance. Returns the renown the killer earned."""
    from Data.Database.repository import save_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief

    victim["health"], victim["alive"], victim["workplace"], victim["home"] = 0, False, None, None
    save_npc(conn, victim)
    rank = rank_of(conn, victim)
    gain = KILL_RENOWN + (RANK_RENOWN[rank] if rank else 0)
    conn.execute("INSERT INTO killings (killer_id, victim_id, day, city_id, context, victim_rank) VALUES (?, ?, ?, ?, ?, ?)",
                 (killer["id"], victim["id"], day, city_id, context, rank or "none"))
    conn.execute("UPDATE npcs SET kills = kills + 1, renown = renown + ? WHERE id = ?", (gain, killer["id"]))
    hold_grievances(conn, victim["id"], killer["id"], day)
    if rank == "monarch":
        grant_epithet(conn, killer["id"], f"the {victim.get('title')}slayer")
    elif rank == "lord":
        grant_epithet(conn, killer["id"], "the Lordsbane")
    death_event(conn, victim, cause, city_id=city_id, kingdom_id=_kingdom_of(conn, victim["id"]))
    apply_grief(conn, victim)
    return gain


def seek_nemeses(conn, side_a, side_b, city_id=None, context="battle"):
    """
    Before a battle's general fighting: everyone holding a grievance against
    a fighter of the other side seeks them out. Mutates the fallen (they are
    dead before the casualties are rolled). Returns the duels as
    (avenger_id, nemesis_id, avenger_won).
    """
    from Data.Combat.combat import get_combat_power
    from Data.Database.repository import create_event
    from Data.Government.edicts import today
    from Data.History.chronicle import write_entry, current_date

    a = {n["id"]: n for n in side_a if n.get("id") and n["id"] > 0 and n.get("alive", True)}
    b = {n["id"]: n for n in side_b if n.get("id") and n["id"] > 0 and n.get("alive", True)}
    if not a or not b:
        return []
    everyone = list(a) + list(b)
    marks = ",".join("?" * len(everyone))
    feuds = conn.execute(
        f"""SELECT * FROM grievances WHERE settled = 0 AND holder_id IN ({marks}) AND target_id IN ({marks})
            ORDER BY amount DESC, holder_id""", everyone + everyone).fetchall()

    duels, fought = [], set()
    for feud in feuds:
        holder, target = feud["holder_id"], feud["target_id"]
        if (holder in a) == (target in a) or holder in fought or target in fought:
            continue                                          # same side, or one of them has already dueled
        fought.update((holder, target))
        won = duel(conn, feud, a.get(holder) or b.get(holder), a.get(target) or b.get(target), city_id,
                   context="revenge", template="Vengeance (battle)")
        duels.append((holder, target, won))
    return duels


def win_chance(conn, feud, avenger, nemesis):
    """The avenger's chance in single combat: strength against strength, their fury included."""
    from Data.Combat.combat import get_combat_power
    fury = 1 + FURY * min(1.0, feud["amount"] / MAX_GRIEVANCE)
    power_a, power_n = max(0.1, get_combat_power(avenger)) * fury, max(0.1, get_combat_power(nemesis))
    return power_a / (power_a + power_n)


def duel(conn, feud, avenger, nemesis, city_id, context, template):
    """
    Single combat to the death between the holder of a grievance and its
    target -- in the press of battle, or in peacetime (Data.Combat.vendetta).
    The loser dies; an avenger who wins settles the grievance and is
    chronicled. Returns True if the avenger won.
    """
    from Data.Database.repository import create_event
    from Data.Government.edicts import today
    from Data.History.chronicle import write_entry, current_date

    holder, target = avenger["id"], nemesis["id"]
    world = conn.execute("SELECT k.world_id FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id WHERE n.id = ?",
                         (holder,)).fetchone()["world_id"]
    day = today(conn, world)
    place = conn.execute("SELECT name FROM cities WHERE id = ?", (city_id,)).fetchone() if city_id else None
    where = place["name"] if place else "the field of battle"

    avenger_won = random.random() < win_chance(conn, feud, avenger, nemesis)
    avenger_name, nemesis_name = display_name(conn, holder), display_name(conn, target)

    if avenger_won:
        slay(conn, avenger, nemesis, city_id, context, day, f"slain in vengeance by {avenger_name}")
        conn.execute("UPDATE grievances SET settled = 1 WHERE holder_id = ? AND target_id = ?", (holder, target))
        conn.execute("UPDATE npcs SET renown = renown + ? WHERE id = ?", (VENGEANCE_RENOWN, holder))
        grant_epithet(conn, holder, "the Avenger")
        kingdom_id = _kingdom_of(conn, holder)
        create_event(conn, "Vengeance", f"{avenger_name} found {nemesis_name}, who {feud['reason']}, at {where}, and slew them.",
                     participants=[holder, target], city_id=city_id, kingdom_id=kingdom_id)
        if kingdom_id:
            year, season = current_date(conn, world)
            kingdom = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()["name"]
            write_entry(conn, kingdom_id, "Vengeance", template, year, season,
                        {"kingdom": kingdom, "avenger": avenger_name, "nemesis": nemesis_name, "reason": feud["reason"], "city": where},
                        seed_parts=(holder, target))
    else:
        slay(conn, nemesis, avenger, city_id, context, day, f"slain seeking vengeance on {nemesis_name}")
        create_event(conn, "Vengeance", f"{avenger_name} sought out {nemesis_name}, who {feud['reason']}, at {where} -- and fell to them.",
                     participants=[holder, target], city_id=city_id, kingdom_id=_kingdom_of(conn, target))
    return avenger_won
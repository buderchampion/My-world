import random

# The nemesis engine, part 4: revenge in peacetime.
#
# Each month, everyone grown who holds a serious grievance (VENDETTA_MIN or
# more: kin, not mere friends) against someone living pursues their strongest
# one. What they do depends on their chance in single combat (strength
# against strength, their fury included -- Data.Combat.nemesis.win_chance),
# their age, their purse and their nature:
#
#   train     not READY, and YOUNG or younger: a level of Swordsmanship a
#             month (up to TRAINING_CAP) -- real strength -- until they are;
#   assassin  not ready and too old to train, or cautious and able to pay:
#             ASSASSIN_FEE to a Thief of the nemesis's town. It succeeds
#             ASSASSIN_SUCCESS of the time (less against the renowned). The
#             patron is exposed EXPOSED of the time -- then the victim's kin
#             hold a grievance against them as well as the blade. A failed
#             attempt always exposes them: the nemesis takes up a grievance;
#   duel      ready, the nemesis in the same town: bolder avengers sooner;
#   journey   ready, the nemesis elsewhere: they travel the roads to find
#             them (Data.Infrastructure.travel) and fight on arriving; if
#             the nemesis has gone, they come home;
#   bide      otherwise -- and children until they are grown.

VENDETTA_MIN = 60
READY = 0.4                    # a chance in single combat at least this good, and they will fight
YOUNG = 30
GROWN = 16
TRAINING_SKILL = "Swordsmanship"
TRAINING_CAP = 12
ACT_CHANCE = 0.25              # each month, for a ready avenger of even temper
CAUTIOUS = -30                 # boldness at or below this prefers a hired blade
ASSASSIN_FEE = 300
ASSASSIN_SUCCESS = 0.5
RENOWN_GUARD = 0.01            # each point of the target's renown takes this much off an assassin's chance
ASSASSIN_FLOOR = 0.15
EXPOSED = 0.3
FAILED_ATTEMPT_GRIEVANCE = 60
JOURNEY_STAY_DAYS = 5


def _away(conn, world_id):
    from Data.Infrastructure.travel import get_traveling_npc_ids
    from Data.Government.armies import get_army_npc_ids
    return set(get_traveling_npc_ids(conn, world_id)) | set(get_army_npc_ids(conn, world_id))


def train(conn, npc_id):
    """A month's training: one more level of Swordsmanship, up to TRAINING_CAP. Returns the new level."""
    row = conn.execute("SELECT level FROM npc_skills WHERE npc_id = ? AND skill_name = ?", (npc_id, TRAINING_SKILL)).fetchone()
    level = min(TRAINING_CAP, (row["level"] if row else 0) + 1)
    conn.execute(
        """INSERT INTO npc_skills (npc_id, skill_name, level, xp) VALUES (?, ?, ?, 0)
           ON CONFLICT(npc_id, skill_name) DO UPDATE SET level = excluded.level""", (npc_id, TRAINING_SKILL, level))
    return level


def hire_assassin(conn, feud, patron, nemesis, day):
    """The patron pays a Thief of the nemesis's town to kill them. Returns 'killed', 'failed', or None if no blade can be hired."""
    from Data.Combat.nemesis import slay, hold_grievances, display_name, renown_of
    from Data.Database.repository import get_npc, create_event

    thief_row = conn.execute(
        "SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Thief' AND id NOT IN (?, ?) ORDER BY RANDOM() LIMIT 1",
        (nemesis["city_id"], patron["id"], nemesis["id"])).fetchone()
    if thief_row is None or patron["gold"] < ASSASSIN_FEE:
        return None
    thief = get_npc(conn, thief_row["id"])
    conn.execute("UPDATE npcs SET gold = gold - ? WHERE id = ?", (ASSASSIN_FEE, patron["id"]))
    conn.execute("UPDATE npcs SET gold = gold + ? WHERE id = ?", (ASSASSIN_FEE, thief["id"]))

    town = conn.execute("SELECT name FROM cities WHERE id = ?", (nemesis["city_id"],)).fetchone()["name"]
    patron_name, nemesis_name = display_name(conn, patron["id"]), display_name(conn, nemesis["id"])
    chance = max(ASSASSIN_FLOOR, ASSASSIN_SUCCESS - RENOWN_GUARD * renown_of(conn, nemesis["id"]))

    if random.random() < chance:
        slay(conn, thief, nemesis, nemesis["city_id"], "assassination", day, "murdered by a hired blade")
        conn.execute("UPDATE grievances SET settled = 1 WHERE holder_id = ? AND target_id = ?", (patron["id"], nemesis["id"]))
        exposed = random.random() < EXPOSED
        if exposed:
            hold_grievances(conn, nemesis["id"], patron["id"], day, deed="paid for the murder of")   # the victim's kin learn who paid
        create_event(conn, "Assassination",
                     f"{nemesis_name}, who {feud['reason']}, was murdered in {town} by a hired blade"
                     + (f" -- and it was whispered that {patron_name} had paid for it." if exposed else "."),
                     participants=[nemesis["id"], thief["id"]] + ([patron["id"]] if exposed else []), city_id=nemesis["city_id"])
        return "killed"

    # a failed attempt: the nemesis learns who sent the blade
    conn.execute(
        """INSERT INTO grievances (holder_id, target_id, amount, reason, since_day, settled) VALUES (?, ?, ?, ?, ?, 0)
           ON CONFLICT(holder_id, target_id) DO UPDATE SET amount = MIN(200, grievances.amount + excluded.amount), settled = 0""",
        (nemesis["id"], patron["id"], FAILED_ATTEMPT_GRIEVANCE, "sent an assassin after them", day))
    create_event(conn, "Assassination", f"A hired blade failed to kill {nemesis_name} in {town}; {patron_name} was named as the one who paid.",
                 participants=[nemesis["id"], patron["id"]], city_id=nemesis["city_id"])
    return "failed"


def pursue_vendettas(conn, world_id):
    """Monthly: each avenger pursues their strongest grievance. Returns what each did, as (holder_id, course)."""
    from Data.Combat.nemesis import win_chance, duel
    from Data.Database.repository import get_npc, create_event
    from Data.Government.edicts import today
    from Data.Infrastructure.travel import dispatch_npc_travel

    day = today(conn, world_id)
    away = _away(conn, world_id)
    did, seen = [], set()

    for feud in conn.execute(
        """SELECT g.* FROM grievances g
           JOIN npcs h ON h.id = g.holder_id JOIN npcs t ON t.id = g.target_id
           JOIN cities c ON c.id = h.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND g.settled = 0 AND g.amount >= ? AND h.alive = 1 AND t.alive = 1 AND h.age >= ?
           ORDER BY g.amount DESC, g.holder_id""", (world_id, VENDETTA_MIN, GROWN)).fetchall():
        holder, target = feud["holder_id"], feud["target_id"]
        if holder in seen or holder in away:
            continue                                         # one vendetta at a time; not while on the road or with an army
        seen.add(holder)
        avenger, nemesis = get_npc(conn, holder), get_npc(conn, target)
        if not nemesis["alive"] or not avenger["alive"]:
            continue                                         # slain earlier this very month
        chance = win_chance(conn, feud, avenger, nemesis)
        boldness = conn.execute("SELECT boldness FROM npcs WHERE id = ?", (holder,)).fetchone()["boldness"] or 0
        can_pay = avenger["gold"] >= ASSASSIN_FEE

        if chance < READY and avenger["age"] <= YOUNG:
            level = train(conn, holder)
            if level == 1:
                create_event(conn, "Vengeance", f"{avenger['name']} took up the sword, swearing vengeance on {nemesis['name']}, who {feud['reason']}.",
                             participants=[holder, target], city_id=avenger["city_id"])
            did.append((holder, "train"))
        elif can_pay and (chance < READY or boldness <= CAUTIOUS):
            outcome = hire_assassin(conn, feud, avenger, nemesis, day)
            did.append((holder, f"assassin: {outcome}" if outcome else "bide"))
        elif chance >= READY and random.random() < ACT_CHANCE * (1 + boldness / 200):
            if nemesis["city_id"] == avenger["city_id"] and target not in away:
                won = duel(conn, feud, avenger, nemesis, avenger["city_id"], context="duel", template="Vengeance (duel)")
                did.append((holder, "duel: won" if won else "duel: fell"))
            elif target not in away:
                dispatch_npc_travel(conn, avenger, nemesis["city_id"], "Vengeance", stay_days=JOURNEY_STAY_DAYS)
                away.add(holder)
                town = conn.execute("SELECT name FROM cities WHERE id = ?", (nemesis["city_id"],)).fetchone()["name"]
                create_event(conn, "Vengeance", f"{avenger['name']} set out for {town} to find {nemesis['name']}, who {feud['reason']}.",
                             participants=[holder, target], city_id=avenger["city_id"])
                did.append((holder, "journey"))
            else:
                did.append((holder, "bide"))
        else:
            did.append((holder, "bide"))

    conn.commit()
    return did


def arrivals(conn, world_id):
    """Daily: an avenger who has reached their nemesis's town challenges them. Returns the duels fought."""
    from Data.Combat.nemesis import duel
    from Data.Database.repository import get_npc

    fought = []
    for trip in conn.execute(
        """SELECT t.* FROM npc_travels t JOIN npcs n ON n.id = t.npc_id
           JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND t.purpose = 'Vengeance' AND t.status = 'Staying' AND n.alive = 1""", (world_id,)).fetchall():
        feud = conn.execute(
            """SELECT g.* FROM grievances g JOIN npcs t ON t.id = g.target_id
               WHERE g.holder_id = ? AND g.settled = 0 AND t.alive = 1 AND t.city_id = ? ORDER BY g.amount DESC LIMIT 1""",
            (trip["npc_id"], trip["destination_city_id"])).fetchone()
        if feud is None:
            continue                                         # the nemesis is not here (or already dead): they wait out their stay and go home
        avenger, nemesis = get_npc(conn, trip["npc_id"]), get_npc(conn, feud["target_id"])
        won = duel(conn, feud, avenger, nemesis, trip["destination_city_id"], context="duel", template="Vengeance (duel)")
        if not won:
            conn.execute("UPDATE npc_travels SET status = 'Completed', days_remaining = 0 WHERE id = ?", (trip["id"],))
        fought.append((trip["npc_id"], feud["target_id"], won))
    conn.commit()
    return fought
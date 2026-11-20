"""
Crime: thieves and theft, guards and justice, and the thieves' guilds.

Theft     each month every grown townsman or villager might steal (THEFT_CHANCE),
          far likelier when poor, hungry, living in the slums, a Rogue, or
          hard-hearted (low mercy: Data.NPCS.personality), and when already a
          thief. A thief sizes up a few townsfolk and robs the best-off of them
          of a share of their gold.
Guards    each theft may be caught: by the town's watch (Guards and Soldiers,
          more of them a head, more catches), or by a hue and cry where there is
          no watch. Martial law doubles the watch's reach.
Justice   the caught give back what they took and stand before the crown's
          justice, as merciful as the ruler (or regent) is: a fine, the jail
          (JAIL_DAYS, losing their place of work), or for the hardened and under
          a harsh ruler the gallows. The robbed are aggrieved; the jailed are
          wretched (Data.NPCS.contentment); a town robbed often feels unsafe.
Guilds    where thieving thrives in a big town (GUILD_THEFTS in a year), the
          boldest unconvicted thief founds a thieves' guild and becomes its
          guildmaster. Thieves of the town join it: they steal more and are
          caught less, the guild takes its cut, and each month the guildmaster
          bribes the watch from the guild's chest (catching fewer still). Under
          martial law the crown may crack down: the guildmaster is seized, and
          if they hang or go to jail the guild breaks -- or a new master
          takes it. Founding and breaking are chronicled.
Dens      a Thieves Den is where a town's thieves and smugglers work. A Thief
          by trade (working at a den) lives by stealing: THIEF_TRADE times
          likelier to steal each month, and paid no wage (Wages.json). A den
          is a safehouse: every thief of its town is caught DEN_HIDDEN as
          often, and a guild forms there at half the thefts (DEN_GUILD).
Smugglers each month every Smuggler at a den runs a load (SMUGGLE_LOAD) of
          whatever good fetches the most more across a border: from their
          town to the nearest town of another realm (no ally needed, no
          customs paid, an export ban ignored), selling at the price there
          and pocketing SMUGGLE_SHARE of the difference. Into a besieged town
          of their own realm they carry Food through the lines instead, and
          are paid by the town. The watch at home may catch them
          (SMUGGLE_CAUGHT of their town's catch): the goods are seized and
          they stand before the crown's justice like any thief.
"""

from __future__ import annotations

import math
import random

THEFT_CHANCE = 0.002             # a month, for an ordinary grown person
POOR_BELOW = 20                  # gold
POOR, HUNGRY, SLUMS, ROGUE, THIEF_AGAIN = 3.0, 3.0, 2.0, 3.0, 2.0   # how much likelier each makes it
HARD_HEART = 1.5                 # at mercy -100 (scaled: none at 0 or above)
TAKE = (0.1, 0.3)                # the share of the victim's gold taken
MIN_TAKE = 2
FORTUNE_LOST = 100              # a theft this large is the loss of a fortune
MARKS = 5                        # a thief sizes up this many townsfolk and robs the best-off
WATCH = ("Guard", "Soldier")
WATCH_REACH = 0.5                # the watch's catch: 1 - e^(-REACH x watchmen per 100 people)...
HUE_AND_CRY = 0.2                # ...and where there is no watch, neighbors still catch this share
MARTIAL_LAW_WATCH = 2.0
FINE = 2.0                       # a fine: this many times what was taken (as much as they have)
JAIL_DAYS = 90
GALLOWS_AFTER = 2                # a thief already convicted this many times may hang...
GALLOWS_HARSH = 0.6              # ...with this chance under the harshest ruler (mercy -100), none under a merciful one
JAIL_CHANCE = 0.5                # otherwise jail rather than a fine, at mercy 0 (less as mercy rises, more as it falls)
VICTIM_GRIEF = 10                # happiness the robbed lose at once
UNSAFE_THEFTS = 3                # thefts per 100 people in the last season that make a town feel unsafe

GUILD_MIN_PEOPLE = 500           # a big town (most capitals, a few great towns)
GUILD_THEFTS = 20                # thefts in a town within a year that breed a guild
GUILD_STEALS = 1.5               # members steal this much more often...
GUILD_HIDDEN = 0.6               # ...and are caught this share as often
GUILD_CUT = 0.3                  # the guild's share of every take
BRIBE = 0.1                      # each month the guildmaster spends this share of the chest on the watch...
BRIBED = 0.7                     # ...who then catch this share as often
CRACKDOWN = 0.3                  # a month, under martial law: the crown moves against a guild

THIEF_TRADE = 40                 # a Thief by trade steals this much more often (most months)
DEN_HIDDEN = 0.7                 # the thieves of a town with a den are caught this share as often
DEN_GUILD = 0.5                  # ...and a guild forms there at this share of GUILD_THEFTS
SMUGGLE_LOAD = 20                # goods a smuggler carries a month
SMUGGLE_SHARE = 0.5              # of the price gap, the smuggler's (the rest is the cost of the run)
SMUGGLE_GOODS = ("Food", "Tools", "Weapons", "Cloth", "Steel", "Potions", "Armor")
SMUGGLE_CAUGHT = 0.5             # a run is caught this share as often as a theft in their town
SIEGE_RUN_PAY = 3.0              # gold a unit of Food a besieged town pays its smugglers
DEN_RECRUITS = 1                 # each month a den with room takes on this many of its town's idle rogues


def _day(conn, world_id):
    from Data.Government.edicts import today
    return today(conn, world_id)


def _mercy(conn, kingdom_id):
    """The mercy of whoever rules (the regent, if there is one): -100 .. 100."""
    k = conn.execute("SELECT ruler_id, regent_id FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    who = k and (k["regent_id"] or k["ruler_id"])
    row = conn.execute("SELECT mercy FROM npcs WHERE id = ? AND alive = 1", (who,)).fetchone() if who else None
    return row[0] if row and row[0] is not None else 0


def watch_catch(watchmen, people, martial=False, bribed=False):
    """The chance a theft in this town is caught."""
    if watchmen <= 0:
        p = HUE_AND_CRY
    else:
        p = 1 - math.exp(-WATCH_REACH * watchmen * (MARTIAL_LAW_WATCH if martial else 1) / max(1, people) * 100)
        p = max(p, HUE_AND_CRY)
    if bribed:
        p *= BRIBED
    return min(0.95, p)


def motive(p):
    """How much likelier than THEFT_CHANCE this person is to steal this month."""
    m = 1.0
    if (p["gold"] or 0) < POOR_BELOW:
        m *= POOR
    if (p["fed"] if p["fed"] is not None else 1.0) < 0.5:
        m *= HUNGRY
    if p["in_slums"]:
        m *= SLUMS
    if p["class"] == "Rogue":
        m *= ROGUE
    if (p["convictions"] or 0) > 0 or p["guild_id"]:
        m *= THIEF_AGAIN
    if (p["mercy"] or 0) < 0:
        m *= 1 + (HARD_HEART - 1) * (-(p["mercy"]) / 100)
    if p["guild_id"]:
        m *= GUILD_STEALS
    if p.get("profession") == "Thief":
        m *= THIEF_TRADE                         # a thief by trade (Thieves Den)
    return m


def den_towns(conn, world_id):
    """Towns with a Thieves Den."""
    return {r[0] for r in conn.execute(
        """SELECT DISTINCT b.city_id FROM buildings b JOIN cities c ON c.id = b.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND b.type = 'Thieves Den'""", (world_id,))}


def sentence(conn, thief, mercy, day):
    """The crown's justice on a caught thief. Returns 'fine', 'jail' or 'gallows'."""
    from Data.Database.repository import get_npc, save_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    harsh = max(0.0, -mercy / 100)
    convictions = (thief["convictions"] or 0)
    conn.execute("UPDATE npcs SET convictions = convictions + 1 WHERE id = ?", (thief["id"],))
    if convictions >= GALLOWS_AFTER and random.random() < GALLOWS_HARSH * harsh:
        npc = get_npc(conn, thief["id"])
        npc.update(alive=False, health=0, workplace=None, home=None)
        save_npc(conn, npc)
        death_event(conn, npc, "the gallows", city_id=npc["city_id"])
        apply_grief(conn, npc)
        return "gallows"
    jail = JAIL_CHANCE * (1 - mercy / 100) * (1 + 0.5 * convictions)
    if random.random() < jail:
        # in jail they lose their place: out again, they take day labour until a place is free (Data.Economy.jobs)
        conn.execute("UPDATE npcs SET jailed_until = ?, workplace_id = NULL, profession = 'Laborer' WHERE id = ?",
                     (day + JAIL_DAYS, thief["id"]))
        return "jail"
    return "fine"


def _people(conn, world_id):
    return [dict(r) for r in conn.execute(
        """SELECT n.id, n.name, n.city_id, n.gold, n.fed, n.class, n.mercy, n.boldness, n.convictions, n.guild_id,
                  n.jailed_until, n.profession, c.kingdom_id, c.name city, (b.type = 'Slums') in_slums
           FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           LEFT JOIN buildings b ON b.id = n.home_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND n.alive = 1 AND n.age >= 16""", (world_id,))]


def thefts_month(conn, world_id):
    """Monthly: thieves steal, the watch catches some, justice is done. Returns the thefts."""
    from Data.Government.edicts import martial_law_active
    from Data.Infrastructure.travel import get_traveling_npc_ids
    from Data.Government.armies import get_army_npc_ids
    day = _day(conn, world_id)
    away = set(get_traveling_npc_ids(conn, world_id)) | set(get_army_npc_ids(conn, world_id))
    people = [p for p in _people(conn, world_id) if p["id"] not in away and not ((p["jailed_until"] or -1) >= day)]
    by_city = {}
    for p in people:
        by_city.setdefault(p["city_id"], []).append(p)
    kingdoms = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM kingdoms WHERE world_id = ?", (world_id,))}
    mercy = {kid: _mercy(conn, kid) for kid in kingdoms}
    guilds = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM thieves_guilds WHERE world_id = ? AND status = 'Active'", (world_id,))}
    bribed = {g["city_id"] for g in guilds.values() if (g["chest"] or 0) > 0}
    dens = den_towns(conn, world_id)
    thefts = []
    for city_id, folk in by_city.items():
        watchmen = sum(1 for p in folk if p["profession"] in WATCH)
        kid = folk[0]["kingdom_id"]
        catch = watch_catch(watchmen, len(folk), martial_law_active(kingdoms[kid], day), city_id in bribed)
        for thief in folk:
            if thief["profession"] in WATCH or random.random() >= THEFT_CHANCE * motive(thief):
                continue
            marks = random.sample(folk, min(MARKS, len(folk)))      # a thief eyes a few and robs the best-off of them
            marks = [v for v in marks if v["id"] != thief["id"] and (v["gold"] or 0) >= 2 * MIN_TAKE]
            if not marks:
                continue
            victim = max(marks, key=lambda v: v["gold"] or 0)
            take = round(max(MIN_TAKE, victim["gold"] * random.uniform(*TAKE)), 2)
            take = min(take, victim["gold"])
            caught = random.random() < catch * (GUILD_HIDDEN if thief["guild_id"] else 1) * (DEN_HIDDEN if city_id in dens else 1)
            punishment = None
            conn.execute("UPDATE npcs SET needs_happiness = MAX(0, needs_happiness - ?) WHERE id = ?", (VICTIM_GRIEF, victim["id"]))
            if caught:
                fine = min(max(0, thief["gold"] or 0), FINE * take)
                conn.execute("UPDATE npcs SET gold = gold - ? WHERE id = ?", (fine, thief["id"]))
                thief["gold"] = (thief["gold"] or 0) - fine
                conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?", (fine, city_id))
                punishment = sentence(conn, thief, mercy.get(kid, 0), day)
                thief["convictions"] = (thief["convictions"] or 0) + 1
            else:
                cut = take * GUILD_CUT if thief["guild_id"] in guilds else 0
                conn.execute("UPDATE npcs SET gold = gold - ? WHERE id = ?", (take, victim["id"]))
                conn.execute("UPDATE npcs SET gold = gold + ? WHERE id = ?", (take - cut, thief["id"]))
                if cut:
                    conn.execute("UPDATE thieves_guilds SET chest = chest + ? WHERE id = ?", (cut, thief["guild_id"]))
                if take >= FORTUNE_LOST:                    # robbed of a fortune (Data.NPCS.personality)
                    from Data.NPCS.personality import nudge
                    nudge(conn, [victim["id"]], "loss")
                victim["gold"] -= take
                thief["gold"] = (thief["gold"] or 0) + take - cut
            conn.execute("""INSERT INTO crimes (world_id, city_id, thief_id, victim_id, kind, amount, day, caught, punishment)
                            VALUES (?, ?, ?, ?, 'Theft', ?, ?, ?, ?)""",
                         (world_id, city_id, thief["id"], victim["id"], take, day, int(caught), punishment))
            thefts.append({"thief": thief["id"], "victim": victim["id"], "city": city_id, "take": take,
                           "caught": caught, "punishment": punishment})
    conn.commit()
    return thefts


def release(conn, world_id):
    """The jailed whose time is served go free (to find work again like anyone without it)."""
    day = _day(conn, world_id)
    n = conn.execute("UPDATE npcs SET jailed_until = NULL WHERE jailed_until IS NOT NULL AND jailed_until < ?", (day,)).rowcount
    return n


def unsafe_towns(conn, world_id, day):
    """Towns robbed often in the last season (UNSAFE_THEFTS per 100 people)."""
    rows = conn.execute(
        """SELECT cr.city_id, COUNT(*), (SELECT COUNT(*) FROM npcs n WHERE n.city_id = cr.city_id AND n.alive = 1)
           FROM crimes cr WHERE cr.world_id = ? AND cr.day >= ? AND cr.kind = 'Theft' GROUP BY cr.city_id""", (world_id, day - 90)).fetchall()
    return {r[0] for r in rows if r[2] and r[1] * 100 / r[2] >= UNSAFE_THEFTS}


def guilds_month(conn, world_id):
    """Monthly: guilds are founded where thieving thrives, gather the town's thieves, bribe the watch, and may be
    broken by the crown under martial law. Returns (founded, broken)."""
    from Data.Government.edicts import martial_law_active
    from Data.History.chronicle import write_entry, current_date
    from Data.Database.repository import create_event
    day = _day(conn, world_id)
    year, season = current_date(conn, world_id)
    founded, broken = [], []
    active = {r["city_id"]: dict(r) for r in conn.execute("SELECT * FROM thieves_guilds WHERE world_id = ? AND status = 'Active'", (world_id,))}
    rows = conn.execute(
        """SELECT cr.city_id, COUNT(*) n, c.name, c.kingdom_id, k.name kingdom,
                  (SELECT COUNT(*) FROM npcs x WHERE x.city_id = cr.city_id AND x.alive = 1) people
           FROM crimes cr JOIN cities c ON c.id = cr.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE cr.world_id = ? AND cr.day >= ? AND c.type != 'Village' AND cr.kind = 'Theft' GROUP BY cr.city_id""", (world_id, day - 360)).fetchall()
    dens = den_towns(conn, world_id)
    for r in rows:
        needed = GUILD_THEFTS * (DEN_GUILD if r["city_id"] in dens else 1)      # a den is a guild half-made
        if r["city_id"] in active or r["n"] < needed or r["people"] < GUILD_MIN_PEOPLE:
            continue
        master = conn.execute(
            """SELECT n.id, n.name FROM npcs n WHERE n.alive = 1 AND n.city_id = ? AND n.age >= 18 AND n.jailed_until IS NULL
               AND n.id IN (SELECT thief_id FROM crimes WHERE city_id = ? AND caught = 0)
               ORDER BY (n.class = 'Rogue') DESC, n.boldness DESC LIMIT 1""", (r["city_id"], r["city_id"])).fetchone()
        if not master:
            continue
        name = guild_name(r["name"])
        cur = conn.execute("""INSERT INTO thieves_guilds (world_id, city_id, name, master_id, chest, founded_day, status)
                              VALUES (?, ?, ?, ?, 0, ?, 'Active')""", (world_id, r["city_id"], name, master["id"], day))
        active[r["city_id"]] = {"id": cur.lastrowid, "city_id": r["city_id"], "name": name, "master_id": master["id"], "chest": 0}
        write_entry(conn, r["kingdom_id"], "Crime", "Thieves' Guild (founded)", year, season,
                    {"kingdom": r["kingdom"], "city": r["name"], "guild": name, "ruler": master["name"]}, seed_parts=(r["city_id"], day))
        create_event(conn, "Guild", f"{master['name']} gathered the thieves of {r['name']} into {name}.", city_id=r["city_id"])
        founded.append(r["city_id"])
    for city_id, g in active.items():
        # the town's thieves gather to the guild
        conn.execute("""UPDATE npcs SET guild_id = ? WHERE alive = 1 AND city_id = ? AND guild_id IS NULL AND jailed_until IS NULL
                        AND id IN (SELECT thief_id FROM crimes WHERE city_id = ? AND caught = 0 AND day >= ?)""",
                     (g["id"], city_id, city_id, day - 360))      # the town's free thieves of the last year
        conn.execute("UPDATE npcs SET guild_id = ? WHERE id = ?", (g["id"], g["master_id"]))
        bribe = (g["chest"] or 0) * BRIBE
        conn.execute("UPDATE thieves_guilds SET chest = chest - ? WHERE id = ?", (bribe, g["id"]))
        kingdom = dict(conn.execute("SELECT k.* FROM kingdoms k JOIN cities c ON c.kingdom_id = k.id WHERE c.id = ?", (city_id,)).fetchone())
        master = conn.execute("SELECT * FROM npcs WHERE id = ?", (g["master_id"],)).fetchone()
        if not master or not master["alive"]:
            g_new = _new_master(conn, g)
            if g_new is None:
                _break(conn, world_id, g, kingdom, "its master dead and none to follow", year, season, day)
                broken.append(city_id)
            continue
        if martial_law_active(kingdom, day) and random.random() < CRACKDOWN:
            punishment = sentence(conn, dict(master), _mercy(conn, kingdom["id"]), day)
            create_event(conn, "Crackdown", f"The crown seized {master['name']}, master of {g['name']} ({punishment}).", city_id=city_id)
            if punishment in ("gallows", "jail") and _new_master(conn, g) is None:
                _break(conn, world_id, g, kingdom, f"its master {'hanged' if punishment == 'gallows' else 'in chains'}",
                       year, season, day)
                broken.append(city_id)
    conn.commit()
    return founded, broken


def _new_master(conn, g):
    """The boldest free member takes the guild. Returns their id, or None."""
    row = conn.execute("""SELECT id FROM npcs WHERE guild_id = ? AND alive = 1 AND jailed_until IS NULL AND id != ?
                          ORDER BY boldness DESC LIMIT 1""", (g["id"], g["master_id"])).fetchone()
    if row:
        conn.execute("UPDATE thieves_guilds SET master_id = ? WHERE id = ?", (row[0], g["id"]))
        g["master_id"] = row[0]
        return row[0]
    return None


def _break(conn, world_id, g, kingdom, how, year, season, day):
    from Data.History.chronicle import write_entry
    conn.execute("UPDATE thieves_guilds SET status = 'Broken', broken_day = ? WHERE id = ?", (day, g["id"]))
    conn.execute("UPDATE npcs SET guild_id = NULL WHERE guild_id = ?", (g["id"],))
    city = conn.execute("SELECT name FROM cities WHERE id = ?", (g["city_id"],)).fetchone()[0]
    write_entry(conn, kingdom["id"], "Crime", "Thieves' Guild (broken)", year, season,
                {"kingdom": kingdom["name"], "city": city, "guild": g["name"], "fate": how}, seed_parts=(g["city_id"], day))


GUILD_WORDS = (("the", "Night"), ("the", "Silent"), ("the", "Grey"), ("the", "Crooked"), ("the", "Hidden"), ("the", "Black"),
               ("the", "Velvet"), ("the", "Copper"))
GUILD_NOUNS = ("Hand", "Cloaks", "Rats", "Brotherhood", "Knives", "Lantern", "Purse", "Company")


def guild_name(city):
    the, adj = random.choice(GUILD_WORDS)
    return f"the {adj} {random.choice(GUILD_NOUNS)} of {city}"


def smuggling_month(conn, world_id):
    """Monthly: every smuggler at a den runs a load across a border (or Food into a besieged town of their realm).
    Returns the runs."""
    import math as _math
    from Data.Database.repository import get_city, save_city
    from Data.Economy.economy import get_price
    from Data.Government.campaigns import besieged_city_ids
    from Data.Government.edicts import martial_law_active
    from Data.Infrastructure.travel import get_traveling_npc_ids
    day = _day(conn, world_id)
    away = set(get_traveling_npc_ids(conn, world_id))
    smugglers = [dict(r) for r in conn.execute(
        """SELECT n.id, n.name, n.city_id, n.gold, n.convictions, c.kingdom_id, c.map_x, c.map_y FROM npcs n
           JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id JOIN buildings b ON b.id = n.workplace_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND n.alive = 1 AND n.profession = 'Smuggler' AND b.type = 'Thieves Den'
           AND n.jailed_until IS NULL""", (world_id,))]
    if not smugglers:
        return []
    towns = [dict(r) for r in conn.execute(
        """SELECT c.id, c.kingdom_id, c.map_x, c.map_y FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND c.type != 'Village'""", (world_id,))]
    besieged = besieged_city_ids(conn, world_id)
    kingdoms = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM kingdoms WHERE world_id = ?", (world_id,))}
    mercy = {}
    runs = []
    for sm in smugglers:
        if sm["id"] in away:
            continue
        home = get_city(conn, sm["city_id"])
        under_siege = [t for t in towns if t["id"] in besieged and t["kingdom_id"] == sm["kingdom_id"]]
        if under_siege:                                            # Food through the lines
            dest = min(under_siege, key=lambda t: _math.dist((t["map_x"], t["map_y"]), (sm["map_x"], sm["map_y"])))
            good, gain = "Food", SIEGE_RUN_PAY
        else:
            abroad = [t for t in towns if t["kingdom_id"] != sm["kingdom_id"] and t["id"] not in besieged]
            if not abroad:
                continue
            dest = min(abroad, key=lambda t: _math.dist((t["map_x"], t["map_y"]), (sm["map_x"], sm["map_y"])))
            there = get_city(conn, dest["id"])
            gaps = [(get_price(g, there) - get_price(g, home), g) for g in SMUGGLE_GOODS
                    if home.get("resources", {}).get(g, 0) >= SMUGGLE_LOAD]
            if not gaps:
                continue
            gain, good = max(gaps)
            if gain <= 0:
                continue                                           # nothing worth the risk this month
            gain *= SMUGGLE_SHARE
        load = min(SMUGGLE_LOAD, home.get("resources", {}).get(good, 0))
        if load <= 0:
            continue
        folk = conn.execute("SELECT COUNT(*), SUM(profession IN ('Guard', 'Soldier')) FROM npcs WHERE city_id = ? AND alive = 1",
                            (sm["city_id"],)).fetchone()
        kingdom = kingdoms[sm["kingdom_id"]]
        catch = watch_catch(folk[1] or 0, folk[0] or 1, martial_law_active(kingdom, day)) * SMUGGLE_CAUGHT
        caught = not under_siege and random.random() < catch          # no one turns back food for a besieged town
        home["resources"][good] = round(home["resources"][good] - load, 2)
        save_city(conn, home)
        punishment, pay = None, 0.0
        if caught:
            conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?", (load, sm["city_id"]))   # seized and sold
            mercy.setdefault(sm["kingdom_id"], _mercy(conn, sm["kingdom_id"]))
            punishment = sentence(conn, sm, mercy[sm["kingdom_id"]], day)
        else:
            conn.execute("""INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, ?, ?)
                            ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount""",
                         (dest["id"], good, load))
            pay = round(load * gain, 2)
            conn.execute("UPDATE npcs SET gold = gold + ? WHERE id = ?", (pay, sm["id"]))
            if under_siege:
                conn.execute("UPDATE cities SET wealth = MAX(0, wealth - ?) WHERE id = ?", (pay, dest["id"]))
        conn.execute("""INSERT INTO crimes (world_id, city_id, thief_id, victim_id, kind, amount, day, caught, punishment)
                        VALUES (?, ?, ?, NULL, 'Smuggling', ?, ?, ?, ?)""",
                     (world_id, sm["city_id"], sm["id"], pay, day, int(caught), punishment))
        runs.append({"smuggler": sm["id"], "good": good, "load": load, "to": dest["id"], "pay": pay, "caught": caught,
                     "siege": bool(under_siege)})
    conn.commit()
    return runs


def recruit_dens(conn, world_id):
    """Monthly: a Thieves Den with room takes on idle rogues of its town -- day laborers already convicted, Rogues,
    or the hard-hearted -- as Thieves and Smugglers by turns (long_run.py: without it the jailed lost their trade,
    and the smugglers of a world were gone in three years). Returns how many were taken on."""
    taken = 0
    for den in conn.execute(
            """SELECT b.id, b.city_id, b.capacity, (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1) workers,
                      (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1 AND n.profession = 'Smuggler') smugglers
               FROM buildings b JOIN cities c ON c.id = b.city_id JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen' AND b.type = 'Thieves Den'""", (world_id,)).fetchall():
        room = min(DEN_RECRUITS, (den["capacity"] or 0) - den["workers"])
        if room <= 0:
            continue
        rogues = conn.execute(
            """SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND age BETWEEN 16 AND 50 AND profession = 'Laborer'
               AND jailed_until IS NULL AND (convictions > 0 OR class = 'Rogue' OR mercy < -30)
               ORDER BY convictions DESC, mercy LIMIT ?""", (den["city_id"], room)).fetchall()
        for (npc_id,) in rogues:
            trade = "Smuggler" if den["smugglers"] * 2 < den["workers"] + 1 else "Thief"
            conn.execute("UPDATE npcs SET profession = ?, workplace_id = ? WHERE id = ?", (trade, den["id"], npc_id))
            den = dict(den, workers=den["workers"] + 1, smugglers=den["smugglers"] + (trade == "Smuggler"))
            taken += 1
    return taken


def crime_month(conn, world_id):
    """Monthly: thefts and justice, the jailed released, the guilds, the smugglers' runs. Returns a report."""
    released = release(conn, world_id)
    thefts = thefts_month(conn, world_id)
    founded, broken = guilds_month(conn, world_id)
    recruit_dens(conn, world_id)
    runs = smuggling_month(conn, world_id)
    return {"thefts": len(thefts), "caught": sum(t["caught"] for t in thefts), "smuggled": len(runs),
            "hanged": sum(t["punishment"] == "gallows" for t in thefts), "released": released,
            "founded": founded, "broken": broken}
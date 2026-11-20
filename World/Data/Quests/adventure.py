"""
Adventurers and the quests they take (the board itself: Data.Quests.quests).

Adventurers   young, unmarried folk of the adventuring classes (fighters,
              rogues, casters of every kind) take up the adventuring life --
              the idle most readily (TAKE_UP a month), now and then one who
              leaves a trade for it (TAKE_UP_WORKER; never the sworn, the
              ordained, the learned or the criminal: KEEP_TO_TRADE) -- while
              their town has fewer than ADVENTURERS_PER_TAVERN for each Tavern.
              They earn no wage, only quest rewards, and give it up when they
              marry, grow old (RETIRE_AGE), run out of gold (BROKE), or now and
              then once rich (RETIRE_RICH). When a party is short, townsfolk of
              those classes sign on for the one job, and go back to their
              trade after.
Quests        the board posts them (Data.Quests.quests: bandit bounties,
              supply runs, mending rivalries) and so does this module, monthly:
                Lair      a town pays to have a monster lair near it cleared
                          (lairs no greater than LAIR_QUEST_HD hit dice)
                Rescue    villagers carried off by raiders (Data.Monsters.raids)
                Search    a traveller lost in the wilds after a beast's attack
                          (Data.Monsters.travelers)
                Dragon    a crown's bounty on an evil dragon that burned it
                Herbs     a temple sends for healing herbs while plague is abroad
                Recovery  a victim of a great theft pays to have it recovered
                Escort    a merchant hires guards for a rich caravan as it sets
                          out (Data.Infrastructure.caravans: escorts fight raiders
                          and beasts beside the caravan)
              A quest not taken within QUEST_EXPIRE_DAYS lapses.
Taking them   each month every open quest looks for adventurers within reach
              (ADVENTURE_REACH; a dragon draws them from further). Errands go
              to one adventurer, the best suited by class. A fight gathers a
              party sized to the danger: the strongest first, one more at a
              time, until trial fights (TRIALS, by Data.Combat.melee) say the
              party wins often enough (ACCEPT; DRAGON_ACCEPT for a dragon), up
              to MAX_PARTY. A quest no party in reach can win waits.
Outcomes      the fight is by the book. The party's dead are mourned; the
              survivors share the reward, gain Swordsmanship and renown
              (QUEST_RENOWN) -- enough renown and they are counted champions
              (Data.Combat.nemesis). They are away until they are home again
              (Data.Infrastructure.travel counts them away). Great deeds are
              chronicled. Quests won by fighting feed the Age of Heroes
              (Data.History.eras).
"""

from __future__ import annotations

import copy
import math
import random

ADVENTURING_CLASSES = ("Fighter", "Barbarian", "Ranger", "Rogue", "Paladin", "Wizard", "Sorcerer", "Warlock",
                       "Cleric", "Druid", "Bard", "Monk")
TAKE_UP = 0.15                  # a month, for each idle young person of an adventuring class in a town with room...
TAKE_UP_WORKER = 0.08           # ...and for one who has a trade but would rather adventure
ADVENTURERS_PER_TAVERN = 3
KEEP_TO_TRADE = ("Child", "Adventurer", "Knight", "Captain", "Soldier", "Guard", "Priest", "Healer", "Mage", "Scholar",
                 "Thief", "Smuggler", "Bandit", "innkeeper")   # the sworn, the ordained, the learned and the criminal stay put
YOUNGEST, OLDEST_RECRUIT = 18, 35
RETIRE_AGE = 45
BROKE = 5                       # gold: an adventurer with less than this gives it up for day labour
RETIRE_RICH = (5000, 0.02)      # an adventurer with this much gold settles down this often a month (long_run.py: at
                                # 2000 and 5%, with the dead, the world's adventurers fell from 108 to 28 in six years)
HIRED_MAX_AGE = 50              # idle folk who sign on for one job

ADVENTURE_REACH = 1200          # map units: how far an adventurer goes for a quest
DRAGON_REACH = 3000
MAX_PARTY = 6
TRIALS = 6                      # trial fights to judge a party
ACCEPT = 0.8                    # a party takes a fight it wins this often in trials (long_run.py: at 0.75 with 4 trials,
                                # 19 of 49 lair quests failed in the first year)...
DRAGON_ACCEPT = 0.5             # ...a dragon, this often (the glory is worth the risk)
MAX_QUESTS_A_MONTH = 200        # at most this many quests are taken up in one month: lives first, then the most rewarding
URGENT = ("Rescue", "Search", "Herbs", "Dragon")   # (long_run.py: by reward alone, 60 a month went to feuds and thefts
                                                   # and no herbs were ever fetched)
QUEST_EXPIRE_DAYS = 360
TRAVEL_SPEED = 90               # map units a day (Data.Infrastructure.roads)
SET_OUT_DAYS = 3                # gathering, preparing, resting after

LAIR_QUEST_REACH = 800          # a town posts quests for lairs this near
LAIR_QUEST_HD = 40              # ...no greater than this many hit dice all told (greater ones are for armies)
LAIR_GOLD_PER_HD = 12
RESCUE_GUARDS = 8               # a rescue meets at most this many of the lair (the rest are out, or asleep)
RESCUE_GOLD = 150               # for each captive
SEARCH_GOLD = 120               # (long_run.py: at 40, below every other errand, no one went: 25 of 26 lost died)
DRAGON_BOUNTY = 4000            # from the crown, on top of the hoard
DRAGON_MEMORY = 3               # years a crown keeps a bounty on a dragon that burned it
HERB_SICK = 5                   # a town with this many sick sends for herbs...
HERB_GOLD = 80
HERB_CURES = 12                 # ...which cure this many
HERB_BEASTS = 0.2               # one who goes into the wilds (for herbs, or the lost) meets a beast this often...
WILD_PACK = 1                   # ...this many of them (long_run.py: at 0.35 and three beasts, 80 adventurers died
                                # alone in the wilds in three years)
RECOVERY_MIN = 150              # a theft this great is worth a quest...
RECOVERY_SHARE = 0.3            # ...for this share of it
RECOVERY_DAYS = 90              # ...if it was this recent
CAPTIVE_DAYS = 120              # captives not rescued by then are dead
LOST_DAYS = 60                  # a lost traveller not found by then is dead
ESCORT_VALUE = 1500             # a caravan worth this much hires an escort...
ESCORT_SHARE = 0.08             # ...for this share of its worth
ESCORT_SIZE = 3

SUITED = {                      # the classes an errand goes to first
    "Diplomacy": ("Bard", "Cleric", "Paladin"),
    "Herbs": ("Druid", "Ranger", "Cleric"),
    "Recovery": ("Rogue", "Ranger", "Bard"),
    "Search": ("Ranger", "Druid", "Rogue"),
}
SUCCESS = {"Diplomacy": 0.4, "Recovery": 0.45, "Search": 0.5, "Supply Run": 0.95}   # an errand's chance...
SUITED_BONUS = 0.3                                                                    # ...more for the suited
FIGHTS = ("Bounty", "Lair", "Rescue", "Dragon")
QUEST_RENOWN = {"Bounty": 2, "Lair": 2, "Rescue": 3, "Dragon": 5, "Escort": 0.5, "Herbs": 1, "Search": 1,
                "Recovery": 0.25, "Diplomacy": 0.25, "Supply Run": 0.25}   # (long_run.py: at 1 an errand, 45 of 77 adventurers
                                                                           # were counted champions by the third year)
QUEST_XP = 600                  # Swordsmanship for a fight survived
CHRONICLED = ("Lair", "Rescue", "Dragon", "Herbs", "Bounty")
HERBS_CHRONICLED = 8            # herbs that cure this many are written into the chronicle
CHRONICLE_SLAIN = 5             # a lair or a bandits' camp is written into the chronicle only if this many foes fell (or adventurers)


# ---------------------------------------------------------------- small helpers

def _today(conn, world_id):
    from Data.Government.edicts import today
    return today(conn, world_id)


def _event(conn, kind, text, city_id=None, kingdom_id=None):
    from Data.Database.repository import create_event
    event = create_event(conn, kind, text, city_id=city_id, kingdom_id=kingdom_id)
    return event["id"] if isinstance(event, dict) else event


def _chronicle(conn, world_id, kingdom_id, key, fields, seed, event_id=None):
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    kingdom = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    if kingdom:
        write_entry(conn, kingdom_id, "Quest", key, year, season, dict(fields, kingdom=kingdom[0]),
                    source_event_id=event_id if isinstance(event_id, int) else None, seed_parts=seed)


def _city(conn, city_id):
    row = conn.execute("SELECT id, name, type, kingdom_id, map_x, map_y FROM cities WHERE id = ?", (city_id,)).fetchone()
    return dict(row) if row else None


def busy_ids(conn, world_id, day=None):
    """Adventurers (and the hired) away on a quest -- not yet home again."""
    day = _today(conn, world_id) if day is None else day
    try:
        return {r[0] for r in conn.execute(
            """SELECT p.npc_id FROM quest_party p JOIN quests q ON q.id = p.quest_id JOIN cities c ON c.id = q.city_id
               JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND p.back_day > ?""", (world_id, day))}
    except Exception:
        return set()


def captive_ids(conn, world_id):
    """People held by monsters or lost in the wilds."""
    try:
        return {r[0] for r in conn.execute("SELECT npc_id FROM captives WHERE world_id = ? AND status = 'Held'", (world_id,))}
    except Exception:
        return set()


def skills_of(conn, npc_id):
    return dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc_id,)).fetchall())


def fighter(conn, npc):
    from Data.Combat import melee
    return melee.person_fighter(dict(npc), side="party", skills=skills_of(conn, npc["id"]))


def strength(f):
    return (f.hit_dice or 0) + getattr(f, "caster_level", 0) + f.hp / 20


def pay(conn, quest, amount, from_crown=False):
    """The giver pays: the town's purse, or the crown's when the town's is short (or the crown posted it)."""
    city = conn.execute("SELECT wealth, kingdom_id FROM cities WHERE id = ?", (quest["city_id"],)).fetchone()
    if not from_crown and city and (city["wealth"] or 0) >= amount:
        conn.execute("UPDATE cities SET wealth = wealth - ? WHERE id = ?", (amount, quest["city_id"]))
        return amount
    if city:
        treasury = conn.execute("SELECT treasury FROM kingdoms WHERE id = ?", (city["kingdom_id"],)).fetchone()[0] or 0
        paid = min(amount, max(0.0, treasury))
        conn.execute("UPDATE kingdoms SET treasury = treasury - ? WHERE id = ?", (paid, city["kingdom_id"]))
        return paid
    return 0.0


def reward(conn, ids, gold, renown, xp=0):
    """Gold shared, renown and Swordsmanship to each survivor."""
    from Data.NPCS.employment import add_skill_xp
    if not ids:
        return
    share = round(gold / len(ids), 2)
    for i in ids:
        conn.execute("UPDATE npcs SET gold = gold + ?, renown = COALESCE(renown, 0) + ? WHERE id = ?", (share, renown, i))
        if xp:
            row = conn.execute("SELECT level, xp FROM npc_skills WHERE npc_id = ? AND skill_name = 'Swordsmanship'", (i,)).fetchone()
            if row:
                skill = {"level": row[0], "xp": row[1] or 0}
                add_skill_xp(skill, xp)
                conn.execute("UPDATE npc_skills SET level = ?, xp = ? WHERE npc_id = ? AND skill_name = 'Swordsmanship'",
                             (skill["level"], skill["xp"], i))
            else:
                conn.execute("INSERT INTO npc_skills (npc_id, skill_name, level, xp) VALUES (?, 'Swordsmanship', 1, ?)", (i, xp))
    from Data.Combat.nemesis import CHAMPION_RENOWN, display_name
    for i in ids:
        r = conn.execute("SELECT renown, city_id FROM npcs WHERE id = ?", (i,)).fetchone()
        if r and r[0] is not None and r[0] - renown < CHAMPION_RENOWN <= r[0]:
            _event(conn, "Renown", f"{display_name(conn, i)} is now counted a champion, for deeds of adventure.", city_id=r[1])


def bury(conn, fallen, city_id, how):
    from Data.Monsters.dragons import _bury
    return _bury(conn, fallen, city_id, how)


def party_names(conn, ids):
    from Data.Combat.nemesis import display_name
    names = [display_name(conn, i) for i in ids]
    if not names:
        return "no one"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# ---------------------------------------------------------------- adventurers

def take_up_adventuring(conn, world_id):
    """Monthly: young, unmarried folk of the adventuring classes take up the life, where a tavern has room -- the idle
    most readily, now and then one who leaves a trade for it. Returns how many."""
    from Data.History.eras import effect
    marks = ",".join("?" * len(ADVENTURING_CLASSES))
    taken = 0
    for town in conn.execute(
            """SELECT c.id, (SELECT COUNT(*) FROM buildings b WHERE b.city_id = c.id AND b.type = 'Tavern') taverns,
                      (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1 AND n.profession = 'Adventurer') have
               FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen' AND k.type != 'Horde'""", (world_id,)).fetchall():
        room = town["taverns"] * ADVENTURERS_PER_TAVERN - town["have"]
        if room <= 0:
            continue
        keep = ",".join("?" * len(KEEP_TO_TRADE))
        for npc_id, trade in conn.execute(
                f"""SELECT id, profession FROM npcs WHERE city_id = ? AND alive = 1 AND profession NOT IN ({keep}) AND spouse_id IS NULL
                    AND age BETWEEN ? AND ? AND jailed_until IS NULL AND title IS NULL AND class IN ({marks})
                    ORDER BY profession != 'Laborer', RANDOM() LIMIT ?""",
                (town["id"], *KEEP_TO_TRADE, YOUNGEST, OLDEST_RECRUIT, *ADVENTURING_CLASSES, room * 3)).fetchall():
            if room <= 0:
                break
            if random.random() < (TAKE_UP if trade == "Laborer" else TAKE_UP_WORKER) * effect("adventure"):
                room -= 1
                conn.execute("UPDATE npcs SET profession = 'Adventurer', workplace_id = NULL WHERE id = ?", (npc_id,))
                taken += 1
    conn.commit()
    return taken


def settle_down(conn, world_id):
    """Monthly: adventurers who marry, grow old, go broke -- or, now and then, grow rich -- give it up. Returns how many."""
    busy = busy_ids(conn, world_id)
    out = 0
    for a in conn.execute(
            """SELECT n.id, n.age, n.gold, n.spouse_id FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND n.alive = 1 AND n.profession = 'Adventurer'""", (world_id,)).fetchall():
        if a["id"] in busy:
            continue
        rich = (a["gold"] or 0) >= RETIRE_RICH[0] and random.random() < RETIRE_RICH[1]
        if a["spouse_id"] or a["age"] > RETIRE_AGE or (a["gold"] or 0) < BROKE or rich:
            conn.execute("UPDATE npcs SET profession = 'Laborer' WHERE id = ?", (a["id"],))
            out += 1
    conn.commit()
    return out


def roster(conn, world_id):
    """The world's adventurers at home and free to go this month, strongest first: [(npc row, fighter, x, y)]."""
    from Data.Infrastructure.travel import get_traveling_npc_ids
    away = set(get_traveling_npc_ids(conn, world_id))
    out = []
    for npc in conn.execute(
            """SELECT n.*, c.map_x AS at_x, c.map_y AS at_y FROM npcs n JOIN cities c ON c.id = n.city_id
               JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND k.status != 'Fallen' AND n.alive = 1
               AND n.profession = 'Adventurer' AND n.jailed_until IS NULL""", (world_id,)).fetchall():
        if npc["id"] not in away:
            out.append((npc, fighter(conn, npc), npc["at_x"], npc["at_y"]))
    out.sort(key=lambda t: -strength(t[1]))
    return out


def pool(conn, world_id, x, y, reach, hired=True, adventurers=None):
    """Who could go: idle adventurers within reach (the strongest first), then idle folk of the adventuring classes
    in the towns within reach who would sign on for the one job. Returns [(npc row, fighter, hired?)]."""
    adventurers = roster(conn, world_id) if adventurers is None else adventurers
    out = [(a[0], a[1], False) for a in adventurers if math.dist((a[2], a[3]), (x, y)) <= reach]
    if hired:
        from Data.Infrastructure.travel import get_traveling_npc_ids
        away = set(get_traveling_npc_ids(conn, world_id))
        near = [c["id"] for c in conn.execute(
            """SELECT c.id, c.map_x, c.map_y FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,)) if math.dist((c["map_x"], c["map_y"]), (x, y)) <= reach]
        if not near:
            return out
        marks = ",".join("?" * len(near))
        cls = ",".join("?" * len(ADVENTURING_CLASSES))
        keep = ",".join("?" * len(KEEP_TO_TRADE))
        extra = []
        for npc in conn.execute(f"""SELECT * FROM npcs WHERE city_id IN ({marks}) AND alive = 1 AND profession NOT IN ({keep})
                                    AND age BETWEEN ? AND ? AND jailed_until IS NULL AND title IS NULL AND class IN ({cls})
                                    ORDER BY RANDOM() LIMIT 20""",
                                 (*near, *KEEP_TO_TRADE, YOUNGEST, HIRED_MAX_AGE, *ADVENTURING_CLASSES)).fetchall():
            if npc["id"] not in away:
                extra.append((npc, fighter(conn, npc), True))
        extra.sort(key=lambda t: -strength(t[1]))
        out += extra
    return out


def win_rate(party, foes_fn, trials=TRIALS):
    from Data.Combat import melee
    wins = 0
    for _ in range(trials):
        result = melee.battle(foes_fn(), [copy.deepcopy(f) for f in party])
        wins += result["winner"] == "b"
    return wins / trials


def gather(candidates, foes_fn, accept, taken):
    """A party sized to the danger: the strongest first, one more at a time, until the trials say it will win.
    Returns [(npc, fighter, hired)] or None."""
    party = []
    for c in candidates:
        if c[0]["id"] in taken:
            continue
        party.append(c)
        if win_rate([p[1] for p in party], foes_fn) >= accept:
            return party
        if len(party) >= MAX_PARTY:
            return None
    return None


def choose_one(candidates, kind, taken):
    """The errand-runner: an adventurer of a suited class if one is near, else any."""
    free = [c for c in candidates if c[0]["id"] not in taken and not c[2]]
    if not free:
        return None
    suited = [c for c in free if c[0]["class"] in SUITED.get(kind, ())]
    return (suited or free)[0]


def set_out(conn, quest, party, day, distance):
    back = day + SET_OUT_DAYS + math.ceil(2 * distance / TRAVEL_SPEED)
    for npc, _, hired in party:
        conn.execute("INSERT INTO quest_party (quest_id, npc_id, hired, back_day) VALUES (?, ?, ?, ?)",
                     (quest["id"], npc["id"], int(hired), back))
    conn.execute("UPDATE quests SET status = 'Taken', taken_day = ? WHERE id = ?", (day, quest["id"]))


def close(conn, quest, status, day, outcome):
    conn.execute("UPDATE quests SET status = ?, done_day = ?, outcome = ? WHERE id = ?", (status, day, outcome, quest["id"]))


# ---------------------------------------------------------------- posting the new kinds of quest

def _open(conn, **filters):
    from Data.Database.repository import has_open_quest
    return has_open_quest(conn, **filters) or bool(conn.execute(
        "SELECT 1 FROM quests WHERE status = 'Taken' AND " + " AND ".join(f"{k} = ?" for k in filters) + " LIMIT 1",
        tuple(filters.values())).fetchone())


def _post(conn, day, **fields):
    from Data.Database.repository import create_quest
    return create_quest(conn, posted_day=day, **fields)


def lair_hd(lair):
    from Data.Monsters.bestiary import by_name
    m = by_name(lair["monster"])
    return (lair["count"] or 0) * max(1, (m["stats"].get("hit_dice") or 1) if m else 1)


def post_quests(conn, world_id, day):
    """Monthly: towns, temples, crowns and victims post the new kinds of quest. Returns how many."""
    from Data.Monsters.dragons import is_dragon
    posted = 0
    towns = [dict(r) for r in conn.execute(
        """SELECT c.id, c.name, c.kingdom_id, c.map_x, c.map_y, c.ruler_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND k.type != 'Horde' AND c.type != 'Village'""", (world_id,))]
    if not towns:
        return 0
    # lairs near a town
    for lair in conn.execute("""SELECT * FROM monster_lairs WHERE world_id = ? AND status = 'Active' AND count > 0
                                AND kind IN ('raider', 'beast')""", (world_id,)).fetchall():
        lair = dict(lair)
        if is_dragon(lair["monster"]) or lair_hd(lair) > LAIR_QUEST_HD or _open(conn, target_lair_id=lair["id"], type="Lair"):
            continue
        town = min(towns, key=lambda t: math.dist((t["map_x"], t["map_y"]), (lair["map_x"], lair["map_y"])))
        if math.dist((town["map_x"], town["map_y"]), (lair["map_x"], lair["map_y"])) > LAIR_QUEST_REACH:
            continue
        from Data.Monsters.raids import plural
        _post(conn, day, city_id=town["id"], type="Lair", title=f"Clear the lair of the {plural(lair['monster'])} near {town['name']}",
              giver_id=town["ruler_id"], target_lair_id=lair["id"], reward_gold=round(lair_hd(lair) * LAIR_GOLD_PER_HD, 2))
        posted += 1
    # captives and the lost
    for c in conn.execute("SELECT * FROM captives WHERE world_id = ? AND status = 'Held'", (world_id,)).fetchall():
        if c["kind"] == "captive":
            if _open(conn, target_lair_id=c["lair_id"], type="Rescue"):
                continue
            held = conn.execute("SELECT COUNT(*) FROM captives WHERE lair_id = ? AND status = 'Held'", (c["lair_id"],)).fetchone()[0]
            home = _city(conn, c["home_city_id"])
            _post(conn, day, city_id=c["home_city_id"], type="Rescue", title=f"Rescue the folk of {home['name']} carried off by monsters",
                  target_lair_id=c["lair_id"], reward_gold=RESCUE_GOLD * held)
        else:
            if _open(conn, target_npc_a_id=c["npc_id"], type="Search"):
                continue
            name = conn.execute("SELECT name FROM npcs WHERE id = ?", (c["npc_id"],)).fetchone()[0]
            _post(conn, day, city_id=c["home_city_id"], type="Search", title=f"Find {name}, lost in the wilds",
                  target_npc_a_id=c["npc_id"], reward_gold=SEARCH_GOLD)
        posted += 1
    # a bounty on a dragon that burned the realm
    for d in conn.execute("""SELECT d.*, l.map_x, l.map_y FROM dragons d JOIN monster_lairs l ON l.id = d.lair_id
                             WHERE d.world_id = ? AND d.status = 'Alive' AND d.good = 0""", (world_id,)).fetchall():
        for (kid,) in conn.execute(
                """SELECT DISTINCT c.kingdom_id FROM events e JOIN cities c ON c.id = e.city_id
                   WHERE e.type = 'Dragon Attack' AND e.day >= ? AND e.description LIKE ?""",
                (day - DRAGON_MEMORY * 360, f"%{d['name']}%")).fetchall():
            k = conn.execute("SELECT capital_city_id, treasury, status FROM kingdoms WHERE id = ?", (kid,)).fetchone()
            if not k or k["status"] == "Fallen" or (k["treasury"] or 0) < DRAGON_BOUNTY or _open(conn, target_dragon_id=d["id"]):
                continue
            _post(conn, day, city_id=k["capital_city_id"], type="Dragon", title=f"Slay {d['name']}",
                  target_dragon_id=d["id"], reward_gold=DRAGON_BOUNTY)
            posted += 1
    # herbs while plague is abroad
    for t in conn.execute(
            """SELECT c.id, c.name, COUNT(n.id) sick FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id JOIN npcs n ON n.city_id = c.id
               WHERE k.world_id = ? AND k.status != 'Fallen' AND n.alive = 1 AND n.disease IS NOT NULL AND c.type != 'Village'
               GROUP BY c.id HAVING sick >= ?""", (world_id, HERB_SICK)).fetchall():
        if not _open(conn, city_id=t["id"], type="Herbs"):
            _post(conn, day, city_id=t["id"], type="Herbs", title=f"Gather healing herbs for the sick of {t['name']}",
                  reward_gold=HERB_GOLD)
            posted += 1
    # great thefts
    for th in conn.execute(
            """SELECT r.* FROM crimes r JOIN npcs v ON v.id = r.victim_id JOIN npcs t ON t.id = r.thief_id
               WHERE r.world_id = ? AND r.kind = 'Theft' AND r.caught = 0 AND r.amount >= ? AND r.day >= ? AND v.alive = 1 AND t.alive = 1""",
            (world_id, RECOVERY_MIN, day - RECOVERY_DAYS)).fetchall():
        if _open(conn, crime_id=th["id"]) or conn.execute("SELECT 1 FROM quests WHERE crime_id = ?", (th["id"],)).fetchone():
            continue
        victim = conn.execute("SELECT name FROM npcs WHERE id = ?", (th["victim_id"],)).fetchone()[0]
        _post(conn, day, city_id=th["city_id"], type="Recovery", title=f"Recover what was stolen from {victim}",
              giver_id=th["victim_id"], crime_id=th["id"], target_npc_a_id=th["thief_id"],
              reward_gold=round(th["amount"] * RECOVERY_SHARE, 2))
        posted += 1
    conn.commit()
    return posted


def lapse(conn, world_id, day):
    """Quests no one took within QUEST_EXPIRE_DAYS lapse (an older world's quests are dated today). Returns how many."""
    conn.execute("""UPDATE quests SET posted_day = ? WHERE posted_day IS NULL AND city_id IN
                    (SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?)""", (day, world_id))
    n = conn.execute("""UPDATE quests SET status = 'Lapsed', done_day = ? WHERE status = 'Available' AND posted_day < ? AND city_id IN
                        (SELECT c.id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?)""",
                     (day, day - QUEST_EXPIRE_DAYS, world_id)).rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------- what each quest is up against

def where(conn, quest):
    """The quest's ground: its target's place on the map, or its town's."""
    if quest.get("target_lair_id"):
        r = conn.execute("SELECT map_x, map_y FROM monster_lairs WHERE id = ?", (quest["target_lair_id"],)).fetchone()
        if r:
            return r[0], r[1]
    if quest.get("target_dragon_id"):
        r = conn.execute("SELECT l.map_x, l.map_y FROM dragons d JOIN monster_lairs l ON l.id = d.lair_id WHERE d.id = ?",
                         (quest["target_dragon_id"],)).fetchone()
        if r:
            return r[0], r[1]
    if quest["type"] == "Search":
        r = conn.execute("SELECT map_x, map_y FROM captives WHERE npc_id = ? AND status = 'Held'", (quest["target_npc_a_id"],)).fetchone()
        if r and r[0] is not None:
            return r[0], r[1]
    c = _city(conn, quest["city_id"])
    return c["map_x"], c["map_y"]


def foes_for(conn, quest):
    """A function that makes the quest's foes afresh (for trials and the fight), or None if there is no one to fight
    any more (the camp is empty, the lair cleared, the dragon dead): the quest lapses."""
    from Data.Combat import melee
    from Data.Monsters.bestiary import by_name
    kind = quest["type"]
    if kind == "Bounty":
        bandits = [dict(r) for r in conn.execute(
            "SELECT * FROM npcs WHERE workplace_id = ? AND alive = 1 AND jailed_until IS NULL", (quest["target_building_id"],))]
        if not bandits:
            return None
        made = [(b, skills_of(conn, b["id"])) for b in bandits]
        return lambda: [melee.person_fighter(b, side="bandits", skills=s) for b, s in made]
    if kind in ("Lair", "Rescue"):
        lair = conn.execute("SELECT * FROM monster_lairs WHERE id = ?", (quest["target_lair_id"],)).fetchone()
        if not lair or lair["status"] != "Active" or lair["count"] <= 0 or not by_name(lair["monster"]):
            return None
        if kind == "Rescue" and not conn.execute("SELECT 1 FROM captives WHERE lair_id = ? AND status = 'Held'", (lair["id"],)).fetchone():
            return None
        n = lair["count"] if kind == "Lair" else min(lair["count"], RESCUE_GUARDS)
        m = by_name(lair["monster"])
        return lambda: melee.monster_fighters(m, n, lair["hit_points_each"], side="monsters", ref=lair["id"])
    if kind == "Dragon":
        d = conn.execute("""SELECT d.*, l.count, l.hit_points_each FROM dragons d JOIN monster_lairs l ON l.id = d.lair_id
                            WHERE d.id = ? AND d.status = 'Alive'""", (quest["target_dragon_id"],)).fetchone()
        if not d:
            return None
        d = dict(d)
        from Data.Monsters.dragons import fighter_of
        m = by_name(d["monster"])
        return lambda: [fighter_of(d, "dragon")] + (melee.monster_fighters(m, d["count"] - 1, d["hit_points_each"], side="dragon")
                                                    if d["count"] > 1 else [])
    if kind == "Search" and not conn.execute("SELECT 1 FROM captives WHERE npc_id = ? AND status = 'Held'",
                                             (quest["target_npc_a_id"],)).fetchone():
        return None                     # found, or dead: no one to search for
    return False                        # an errand: no fight


# ---------------------------------------------------------------- doing them

def fight(conn, world_id, quest, party, foes, how):
    """The party fights it out. Returns (won, survivors' ids, the dead, the battle)."""
    from Data.Combat import melee
    from Data.Combat.specials import aftermath
    fighters = [copy.deepcopy(p[1]) for p in party]
    result = melee.battle(foes, fighters)
    won = result["winner"] == "b"
    dead = bury(conn, result["dead_b"], quest["city_id"], how)
    aftermath(conn, fighters)
    living = [f.ref for f in fighters if f.hp > 0 and f.ref is not None
              and conn.execute("SELECT alive FROM npcs WHERE id = ?", (f.ref,)).fetchone()[0]]
    return won, living, dead, result


def do_bounty(conn, world_id, quest, party, foes, day):
    from Data.Events.events import death_event
    bandits = foes()
    won, living, dead, result = fight(conn, world_id, quest, party, bandits, "slain by bandits on a quest")
    for f in result["dead_a"]:
        if f.ref is not None:
            conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (f.ref,))
            from Data.Database.repository import get_npc
            death_event(conn, get_npc(conn, f.ref), "slain by adventurers", city_id=quest["city_id"])
    if won:                                      # the rest scatter
        conn.execute("UPDATE npcs SET workplace_id = NULL, profession = 'Laborer' WHERE workplace_id = ? AND alive = 1",
                     (quest["target_building_id"],))
    return won, living, dead, len(result["dead_a"])


def do_monsters(conn, world_id, quest, party, foes, day):
    lair = dict(conn.execute("SELECT * FROM monster_lairs WHERE id = ?", (quest["target_lair_id"],)).fetchone())
    from Data.Monsters.raids import plural
    won, living, dead, result = fight(conn, world_id, quest, party, foes(),
                                      f"slain by {plural(lair['monster']).lower()} on a quest")
    slain = len(result["dead_a"])
    left = max(0, lair["count"] - slain)
    if won and quest["type"] == "Lair":
        left = 0                                 # the rest flee the land
    conn.execute("UPDATE monster_lairs SET count = ?, status = ? WHERE id = ?", (left, "Cleared" if left <= 0 else "Active", lair["id"]))
    if won and quest["type"] == "Rescue" or left <= 0:
        free_captives(conn, lair["id"], "rescued")
    return won, living, dead, slain


def free_captives(conn, lair_id, how):
    n = conn.execute("UPDATE captives SET status = 'Freed' WHERE lair_id = ? AND status = 'Held'", (lair_id,)).rowcount
    return n


def do_dragon(conn, world_id, quest, party, foes, day):
    from Data.Monsters.dragons import _slain, _slayer, full_name, KINDS
    d = dict(conn.execute("SELECT * FROM dragons WHERE id = ?", (quest["target_dragon_id"],)).fetchone())
    beasts = foes()
    if random.random() < KINDS.get(d["monster"], {}).get("asleep", 0):
        from Data.Monsters.dragons import SLEEPING_ROUNDS
        beasts[0].held = SLEEPING_ROUNDS
    won, living, dead, result = fight(conn, world_id, quest, party, beasts, f"slain by {full_name(d)}")
    if won:
        from Data.Combat.nemesis import display_name
        survivors = [f for f in result["b"] if f.ref in living]
        slayer = _slayer(conn, survivors)
        _slain(conn, d, day, display_name(conn, slayer) if slayer else "adventurers")
        conn.execute("UPDATE monster_lairs SET count = 0, status = 'Cleared' WHERE id = ?", (d["lair_id"],))
        free_captives(conn, d["lair_id"], "rescued")
        quest["hoard"] = d["hoard"] or 0
        quest["slayer"] = display_name(conn, slayer) if slayer else party_names(conn, living)
    return won, living, dead, len(result["dead_a"])


def do_errand(conn, world_id, quest, one, day):
    """An errand by one adventurer. Returns (done, survived)."""
    npc = one[0]
    kind = quest["type"]
    chance = SUCCESS.get(kind, 0.6) + (SUITED_BONUS if npc["class"] in SUITED.get(kind, ()) else 0)
    if kind == "Herbs" or kind == "Search":
        lair = nearest_beasts(conn, world_id, *where(conn, quest))
        if lair and random.random() < HERB_BEASTS:          # beasts in the wild places where herbs grow (and the lost are)
            from Data.Combat import melee
            from Data.Monsters.bestiary import by_name
            from Data.Monsters.raids import plural
            pack = melee.monster_fighters(by_name(lair["monster"]), min(lair["count"], WILD_PACK), lair["hit_points_each"], side="beasts")
            won, living, _, result = fight(conn, world_id, quest, [one], pack, f"slain by {plural(lair['monster']).lower()} in the wilds")
            fallen = len(result["dead_a"])
            conn.execute("UPDATE monster_lairs SET count = MAX(0, count - ?) WHERE id = ?", (fallen, lair["id"]))
            conn.execute("UPDATE monster_lairs SET status = 'Cleared' WHERE id = ? AND count <= 0", (lair["id"],))
            if not living:
                return False, False
    if kind == "Supply Run":
        from Data.Database.repository import get_city, save_city
        src, dst = get_city(conn, quest["from_city_id"]), get_city(conn, quest["to_city_id"])
        have = (src or {}).get("resources", {}).get(quest["resource"], 0)
        if not src or not dst or have < (quest["quantity"] or 0):
            return False, True
        src["resources"][quest["resource"]] = round(have - quest["quantity"], 2)
        dst["resources"][quest["resource"]] = round(dst["resources"].get(quest["resource"], 0) + quest["quantity"], 2)
        save_city(conn, src)
        save_city(conn, dst)
        return True, True                                  # an honest errand: the goods arrive
    if random.random() >= chance:
        return False, True
    if kind == "Diplomacy":
        a, b = sorted((quest["target_npc_a_id"], quest["target_npc_b_id"]))
        conn.execute("UPDATE relationships SET reputation = 0, type = 'Acquaintance' WHERE npc_a_id = ? AND npc_b_id = ?", (a, b))
    elif kind == "Herbs":
        sick = [r[0] for r in conn.execute("""SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND disease IS NOT NULL
                                              ORDER BY health LIMIT ?""", (quest["city_id"], HERB_CURES))]
        for i in sick:
            conn.execute("UPDATE npcs SET disease = NULL, disease_days = 0, disease_severity = NULL WHERE id = ?", (i,))
        quest["cured"] = len(sick)
    elif kind == "Recovery":
        from Data.Government.crime import sentence, _mercy
        crime = conn.execute("SELECT * FROM crimes WHERE id = ?", (quest["crime_id"],)).fetchone()
        thief = conn.execute("SELECT * FROM npcs WHERE id = ? AND alive = 1", (crime["thief_id"],)).fetchone() if crime else None
        if not thief:
            return False, True
        back = min(crime["amount"], max(0.0, thief["gold"] or 0))
        conn.execute("UPDATE npcs SET gold = gold - ? WHERE id = ?", (back, thief["id"]))
        conn.execute("UPDATE npcs SET gold = gold + ? WHERE id = ?", (back, crime["victim_id"]))
        conn.execute("UPDATE crimes SET caught = 1 WHERE id = ?", (crime["id"],))
        kingdom = conn.execute("SELECT kingdom_id FROM cities WHERE id = ?", (crime["city_id"],)).fetchone()[0]
        punishment = sentence(conn, dict(thief), _mercy(conn, kingdom), day)
        conn.execute("UPDATE crimes SET punishment = ? WHERE id = ?", (punishment, crime["id"]))
    elif kind == "Search":
        conn.execute("UPDATE captives SET status = 'Freed' WHERE npc_id = ? AND status = 'Held'", (quest["target_npc_a_id"],))
    return True, True


def nearest_beasts(conn, world_id, x, y, reach=600):
    best = None
    for l in conn.execute("""SELECT * FROM monster_lairs WHERE world_id = ? AND status = 'Active' AND kind = 'beast' AND count > 0""",
                          (world_id,)):
        dist = math.dist((l["map_x"], l["map_y"]), (x, y))
        if dist <= reach and (best is None or dist < best[0]):
            best = (dist, dict(l))
    return best[1] if best else None


def undertake(conn, world_id, quest, day, taken, adventurers=None):
    """One open quest: someone takes it up, and it is done (or failed). Returns a report, or None if no one would go."""
    from Data.Monsters.raids import plural
    quest = dict(quest)
    foes = foes_for(conn, quest)
    if foes is None:
        close(conn, quest, "Lapsed", day, "there was no one left to fight")
        return None
    x, y = where(conn, quest)
    home = _city(conn, quest["city_id"])
    kind = quest["type"]
    reach = DRAGON_REACH if kind == "Dragon" else ADVENTURE_REACH
    if adventurers is not None and not any(a[0]["id"] not in taken and math.dist((a[2], a[3]), (x, y)) <= reach for a in adventurers):
        return None                                             # no adventurer free and near: it waits
    candidates = pool(conn, world_id, x, y, reach, hired=kind not in ("Dragon",) and foes is not False, adventurers=adventurers)
    if foes is False:                                           # an errand, for one
        one = choose_one(candidates, kind, taken)
        if not one:
            return None
        set_out(conn, quest, [one], day, math.dist((x, y), (home["map_x"], home["map_y"])) + 100)
        taken.add(one[0]["id"])
        done, survived = do_errand(conn, world_id, quest, one, day)
        living = [one[0]["id"]] if survived else []
        if done:
            gold = pay(conn, quest, quest["reward_gold"])
            reward(conn, living, gold, QUEST_RENOWN.get(kind, 1))
            close(conn, quest, "Done", day, f"done by {party_names(conn, living)}")
        else:
            conn.execute("UPDATE quests SET status = 'Available', taken_day = NULL WHERE id = ?", (quest["id"],))   # someone else may try
        report = {"quest": quest["id"], "type": kind, "done": done, "party": 1, "dead": 0 if survived else 1}
        if done and kind == "Herbs" and quest.get("cured", 0) >= HERBS_CHRONICLED:
            text = f"{party_names(conn, living)} brought healing herbs to {home['name']}, and {quest.get('cured', 0)} of the sick were cured."
            event_id = _event(conn, "Quest", text, city_id=home["id"], kingdom_id=home["kingdom_id"])
            _chronicle(conn, world_id, home["kingdom_id"], "Quest Done (herbs)",
                       {"champion": party_names(conn, living), "city": home["name"], "dead": quest.get("cured", 0)},
                       (quest["id"],), event_id)
        elif done and kind != "Diplomacy":           # a feud mended is no news (long_run.py: 450 a year)
            _event(conn, "Quest", f"{party_names(conn, living)} completed a quest: {quest['title']}.", city_id=home["id"])
        elif not survived:
            _event(conn, "Quest", f"{one[0]['name']} set out to {quest['title'][0].lower() + quest['title'][1:]} and never came back.",
                   city_id=home["id"])
        return report
    accept = DRAGON_ACCEPT if kind == "Dragon" else ACCEPT
    party = gather(candidates, foes, accept, taken)
    if not party:
        return None
    set_out(conn, quest, party, day, math.dist((x, y), (home["map_x"], home["map_y"])))
    taken.update(p[0]["id"] for p in party)
    if kind == "Bounty":
        won, living, dead, slain = do_bounty(conn, world_id, quest, party, foes, day)
    elif kind == "Dragon":
        won, living, dead, slain = do_dragon(conn, world_id, quest, party, foes, day)
    else:
        won, living, dead, slain = do_monsters(conn, world_id, quest, party, foes, day)
    who = party_names(conn, [p[0]["id"] for p in party])
    if won:
        gold = pay(conn, quest, quest["reward_gold"], from_crown=(kind == "Dragon")) + quest.get("hoard", 0)
        reward(conn, living, gold, QUEST_RENOWN.get(kind, 1), QUEST_XP)
        close(conn, quest, "Done", day, f"done by {who}; {dead} fell")
    else:
        reward(conn, living, 0, 0, QUEST_XP // 2)
        close(conn, quest, "Failed", day, f"{who} were beaten; {dead} fell")
    text = (f"{who} took up the quest to {quest['title'][0].lower() + quest['title'][1:]}"
            + (f" and won through, slaying {slain}" if won else f", and were beaten") + (f"; {dead} of them fell." if dead else "."))
    if kind == "Dragon" and won:
        text = (f"Adventurers slew {quest['title'][5:]} in its lair: {quest.get('slayer')} struck the last blow, "
                f"and {quest.get('hoard', 0):,.0f} gold of its hoard was shared among them.")
    event_id = _event(conn, "Dragon Slain" if kind == "Dragon" and won else "Quest", text, city_id=home["id"], kingdom_id=home["kingdom_id"])
    great = kind not in ("Lair", "Bounty") or slain >= CHRONICLE_SLAIN or dead
    if kind in CHRONICLED and (won or dead) and great:
        key = f"Quest {'Done' if won else 'Failed'} ({kind.lower()})"
        fields = {"champion": who, "city": home["name"], "dead": dead, "gold": f"{quest.get('hoard', 0) + quest['reward_gold']:,.0f}"}
        if kind in ("Lair", "Rescue"):
            m = conn.execute("SELECT monster FROM monster_lairs WHERE id = ?", (quest["target_lair_id"],)).fetchone()[0]
            fields["monsters"] = plural(m).lower() if "," not in m else plural(m)
        if kind == "Dragon":
            fields["dragon"] = quest["title"][5:]
            fields["slayer"] = quest.get("slayer", who)
        _chronicle(conn, world_id, home["kingdom_id"], key, fields, (quest["id"],), event_id)
    return {"quest": quest["id"], "type": kind, "done": won, "party": len(party), "dead": dead,
            "hired": sum(1 for p in party if p[2])}


# ---------------------------------------------------------------- captives

def take_captives(conn, world_id, lair, village_id, living_ids, n):
    """Raiders carry off up to `n` of the village's living folk to their lair. Returns their ids."""
    day = _today(conn, world_id)
    held = captive_ids(conn, world_id)
    taken = [i for i in living_ids if i not in held][:n]
    for i in taken:
        conn.execute("""INSERT INTO captives (world_id, npc_id, lair_id, home_city_id, kind, map_x, map_y, taken_day, status)
                        VALUES (?, ?, ?, ?, 'captive', ?, ?, ?, 'Held')""",
                     (world_id, i, lair["id"], village_id, lair["map_x"], lair["map_y"], day))
    return taken


def lose_traveller(conn, world_id, npc_id, home_city_id, x, y):
    """A traveller beaten by beasts on the road, but not slain: lost and hurt in the wilds."""
    conn.execute("""INSERT INTO captives (world_id, npc_id, lair_id, home_city_id, kind, map_x, map_y, taken_day, status)
                    VALUES (?, ?, NULL, ?, 'lost', ?, ?, ?, 'Held')""", (world_id, npc_id, home_city_id, x, y, _today(conn, world_id)))


def captives_month(conn, world_id, day):
    """Monthly: captives not rescued in time are slain; the lost not found in time die in the wilds; captives of a
    lair that is no more go home. Returns {"dead", "freed"}."""
    from Data.Database.repository import get_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    freed = conn.execute("""UPDATE captives SET status = 'Freed' WHERE world_id = ? AND status = 'Held' AND kind = 'captive'
                            AND lair_id IN (SELECT id FROM monster_lairs WHERE status != 'Active' OR count <= 0)""", (world_id,)).rowcount
    dead = 0
    for c in conn.execute("""SELECT c.*, l.monster FROM captives c LEFT JOIN monster_lairs l ON l.id = c.lair_id
                             WHERE c.world_id = ? AND c.status = 'Held'""", (world_id,)).fetchall():
        limit = CAPTIVE_DAYS if c["kind"] == "captive" else LOST_DAYS
        if day - c["taken_day"] < limit:
            continue
        conn.execute("UPDATE captives SET status = 'Dead' WHERE id = ?", (c["id"],))
        npc = get_npc(conn, c["npc_id"])
        if npc and npc["alive"]:
            from Data.Monsters.raids import plural
            conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (npc["id"],))
            npc = get_npc(conn, npc["id"])
            death_event(conn, npc, f"slain in captivity by {plural(c['monster']).lower()}" if c["kind"] == "captive"
                        else "lost in the wilds", city_id=npc["city_id"])
            apply_grief(conn, npc)
            dead += 1
    conn.commit()
    return {"dead": dead, "freed": freed}


# ---------------------------------------------------------------- the month

def quests_month(conn, world_id):
    """Monthly: adventurers take up and give up the life; quests are posted, lapse, and are taken up and done;
    captives' fates. Returns a report."""
    day = _today(conn, world_id)
    joined = take_up_adventuring(conn, world_id)
    left = settle_down(conn, world_id)
    lapsed = lapse(conn, world_id, day)
    posted = post_quests(conn, world_id, day)
    fates = captives_month(conn, world_id, day)
    taken = busy_ids(conn, world_id, day)
    adventurers = roster(conn, world_id)
    reports = []
    open_quests = conn.execute(
        f"""SELECT q.* FROM quests q JOIN cities c ON c.id = q.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND q.status = 'Available' AND q.type != 'Escort'
           ORDER BY q.type IN ({",".join("?" * len(URGENT))}) DESC, q.reward_gold DESC""", (world_id, *URGENT)).fetchall()
    for q in open_quests:
        if len(reports) >= MAX_QUESTS_A_MONTH or all(a[0]["id"] in taken for a in adventurers):
            break
        r = undertake(conn, world_id, q, day, taken, adventurers)
        if r:
            reports.append(r)
    conn.commit()
    return {"joined": joined, "left": left, "lapsed": lapsed, "posted": posted, "captives_dead": fates["dead"],
            "captives_freed": fates["freed"], "quests": reports}


# ---------------------------------------------------------------- escorts (Data.Infrastructure.caravans)

def hire_escort(conn, world_id, caravan):
    """A rich caravan setting out hires adventurers of its town as guards. Returns the quest id, or None."""
    from Data.Economy.economy import get_price
    from Data.Database.repository import get_city, create_quest
    origin = get_city(conn, caravan["origin_city_id"])
    worth = (caravan["amount"] or 0) * get_price(caravan["resource"], origin)
    if worth < ESCORT_VALUE:
        return None
    day = _today(conn, world_id)
    busy = busy_ids(conn, world_id, day) | captive_ids(conn, world_id)
    from Data.Infrastructure.travel import get_traveling_npc_ids
    busy |= set(get_traveling_npc_ids(conn, world_id))
    guards = [dict(r) for r in conn.execute(
        """SELECT * FROM npcs WHERE city_id = ? AND alive = 1 AND profession = 'Adventurer' AND jailed_until IS NULL""",
        (caravan["origin_city_id"],)) if r["id"] not in busy][:ESCORT_SIZE]
    if not guards:
        return None
    dest = conn.execute("SELECT name FROM cities WHERE id = ?", (caravan["destination_city_id"],)).fetchone()[0]
    quest = create_quest(conn, city_id=caravan["origin_city_id"], type="Escort", status="Taken", posted_day=day, taken_day=day,
                         title=f"Guard a caravan of {caravan['resource']} to {dest}", caravan_id=caravan["id"],
                         reward_gold=round(worth * ESCORT_SHARE, 2))
    back = day + 2 * (caravan["days_remaining"] or 1) + SET_OUT_DAYS
    for g in guards:
        conn.execute("INSERT INTO quest_party (quest_id, npc_id, hired, back_day) VALUES (?, ?, 0, ?)", (quest["id"], g["id"], back))
    return quest["id"]


def escort_fighters(conn, caravan):
    """The escort riding with a caravan, as fighters (for the road's fights)."""
    if not caravan.get("escort_quest_id"):
        return []
    rows = conn.execute("""SELECT n.* FROM quest_party p JOIN npcs n ON n.id = p.npc_id
                           WHERE p.quest_id = ? AND n.alive = 1""", (caravan["escort_quest_id"],)).fetchall()
    out = []
    for n in rows:
        f = fighter(conn, n)
        f.side = "travelers"
        out.append(f)
    return out


def escort_done(conn, world_id, caravan, delivered):
    """The caravan came in (paid) or was lost (not)."""
    qid = caravan.get("escort_quest_id")
    if not qid:
        return
    quest = conn.execute("SELECT * FROM quests WHERE id = ? AND status = 'Taken'", (qid,)).fetchone()
    if not quest:
        return
    day = _today(conn, world_id)
    living = [r[0] for r in conn.execute("""SELECT p.npc_id FROM quest_party p JOIN npcs n ON n.id = p.npc_id
                                            WHERE p.quest_id = ? AND n.alive = 1""", (qid,))]
    if delivered:
        seller = caravan["seller_kingdom_id"]                      # the selling crown's merchants pay what they can
        treasury = conn.execute("SELECT treasury FROM kingdoms WHERE id = ?", (seller,)).fetchone()[0] or 0
        paid = min(quest["reward_gold"], max(0.0, treasury))
        conn.execute("UPDATE kingdoms SET treasury = treasury - ? WHERE id = ?", (paid, seller))
        reward(conn, living, paid, QUEST_RENOWN["Escort"], QUEST_XP // 4)
        close(conn, dict(quest), "Done", day, f"the caravan came in, guarded by {party_names(conn, living)}")
    else:
        close(conn, dict(quest), "Failed", day, "the caravan was lost")
    conn.execute("UPDATE quest_party SET back_day = MIN(back_day, ?) WHERE quest_id = ?", (day + SET_OUT_DAYS, qid))


def escort_defends(conn, world_id, caravan, here=None):
    """Bandits fall on an escorted caravan: the escort fights a band from the nearest camp. Returns True if it held."""
    from Data.Combat import melee
    guards = escort_fighters(conn, caravan)
    if not guards:
        return False
    camps = conn.execute(
        """SELECT b.id, b.city_id, c.map_x, c.map_y FROM buildings b JOIN cities c ON c.id = b.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND b.type = 'Bandit Camp' AND EXISTS (SELECT 1 FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1)""",
        (world_id,)).fetchall()
    camp = min(camps, key=lambda c: math.dist((c["map_x"], c["map_y"]), here)) if camps and here else (camps[0] if camps else None)
    if not camp:
        return True
    bandits = [melee.person_fighter(dict(n), side="bandits", skills=skills_of(conn, n["id"])) for n in conn.execute(
        "SELECT * FROM npcs WHERE workplace_id = ? AND alive = 1 LIMIT 8", (camp["id"],))]
    result = melee.battle(bandits, guards)
    from Data.Events.events import death_event
    from Data.Database.repository import get_npc
    for f in result["dead_a"]:
        if f.ref is not None:
            conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (f.ref,))
            death_event(conn, get_npc(conn, f.ref), "slain by a caravan's guards", city_id=camp["city_id"])
    bury(conn, result["dead_b"], caravan["origin_city_id"], "slain by bandits guarding a caravan")
    return result["winner"] == "b"
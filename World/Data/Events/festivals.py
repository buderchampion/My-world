"""
Festivals: the yearly feasts, the great fairs, and the tournaments.

Feasts        every settlement keeps three feasts a year, edict or no edict:
              Harvest Home (the month after the harvest), Midwinter, and its own
              Founding Day (a month of its own). A town pays from its purse and
              its Food stock, a village from its households' store; everyone
              there is happier and calmer for it, and a few become friends.
              No feast in a plague town, a besieged or occupied one, or a village going
              hungry -- and a poor town keeps a meagre feast, at half the joy.
Fairs         once a year (in a season of its own) every capital and every town
              of FAIR_MIN_PEOPLE holds a fair. Folk with gold to spend come
              from the towns its roads reach -- across borders too -- and stay
              its days: they spend at the stalls (the town's purse), meet the
              townsfolk (new friendships), and a foreign crowd warms its
              kingdom a little toward the host.
Tournaments   each summer every capital that can pay the prize holds a
              tournament. Its knights and captains, and a few from friendly
              realms, ride against each other two by two, by the book
              (Data.Combat.melee), until one yields; now and then one is killed.
              Everyone who rode gains Swordsmanship; the champion takes the
              prize and renown, and the chronicle names them.
"""

from __future__ import annotations

import random

FEASTS = {"Harvest Home": 8, "Midwinter": 11}     # month of each common feast (the harvest is in as Autumn begins, month 7;
                                                  # Winter is months 10-12: Data.Simulation.clock)
FEAST_GOLD_PER_PERSON = 1.0      # a town's purse, for each of its people
FEAST_FOOD_PER_PERSON = 0.5      # and its Food (a village takes its share from the households' store)
FEAST_JOY = 10                   # happiness, at once (it drifts back over the following days)
FEAST_CALM = 10                  # stress eased
MEAGRE = 0.5                     # a poor town's feast: this share of the joy
FEAST_FRIENDS = 0.05             # this share of the people make a new friend at a feast

FAIR_MIN_PEOPLE = 150            # a town this size (or any capital) holds a yearly fair
FAIR_DAYS = 10
FAIR_GOER_CHANCE = 0.05          # each adult with gold to spare in a town the fair's roads reach comes, with this chance
FAIR_MIN_GOLD = 30
FAIR_SPEND = 20                  # gold each fair-goer spends at the stalls (into the host town's purse)
FAIR_FRIENDS = 2                 # townsfolk each fair-goer meets
FAIR_FAVOR = 0.2                 # a foreign kingdom warms this much toward the host, for each of its folk at the fair (up to...)
FAIR_FAVOR_MAX = 5.0

TOURNEY_MONTH = 5                # midsummer (Summer is months 4-6)
TOURNEY_PRIZE = 500              # from the crown's treasury, to the champion
TOURNEY_ENTRANTS = ("Knight", "Captain")
TOURNEY_MAX = 16                 # riders in the lists (a bracket of up to 16)
TOURNEY_GUESTS = 4               # knights from friendly realms (favor at least TOURNEY_FRIENDLY toward the host)
TOURNEY_FRIENDLY = 20
TOURNEY_XP = 400                 # Swordsmanship gained by every rider...
CHAMPION_XP = 1200               # ...and by the champion
CHAMPION_RENOWN = 5
YIELD_AT = 0.5                   # a rider yields having lost this share of their hit points
JOUST_KILL_CHANCE = 0.2          # a rider struck down to nothing (past yielding) dies of it this often


def _clock(conn, world_id):
    return conn.execute("SELECT clock_year, clock_month FROM worlds WHERE id = ?", (world_id,)).fetchone()


def _settlements(conn, world_id):
    return [dict(r) for r in conn.execute(
        """SELECT c.id, c.name, c.type, c.kingdom_id, c.wealth, c.households, c.household_store, c.hungry_days,
                  k.name kingdom, k.capital_city_id,
                  (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1) people
           FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,))]


def founding_month(city_id):
    """A town's own Founding Day falls in a month of its own (the same every year)."""
    return 1 + (city_id * 7) % 12


def fair_month(city_id):
    """A town's fair, in its own month of spring, summer or autumn (months 1-9)."""
    return 1 + (city_id * 5) % 9


def _cancelled(conn, world_id, city):
    from Data.NPCS.sickness import plague_towns, FEAR_AT
    from Data.Government.campaigns import besieging_army
    if city["id"] in plague_towns(conn, world_id, FEAR_AT) or besieging_army(conn, city["id"]):
        return True
    from Data.Government.occupation import occupied
    if occupied(conn, city["id"]):                      # no feasting under a foreign garrison
        return True
    return city["type"] == "Village" and (city["hungry_days"] or 0) > 0


def _befriend(conn, pairs, origin):
    """Some pairs meet and get on (how their peoples feel about each other sways it)."""
    from Data.Relationships.relationships import roll_relationship
    from Data.NPCS.races import attitude
    from Data.Database.repository import set_relationship
    made = 0
    races = {}
    ids = {i for pair in pairs for i in pair}
    if ids:
        races = {r[0]: r[1] for r in conn.execute(
            f"SELECT id, race FROM npcs WHERE id IN ({','.join('?' * len(ids))})", list(ids))}
    for a, b in pairs:
        if a == b or conn.execute("SELECT 1 FROM relationships WHERE npc_a_id = ? AND npc_b_id = ?", tuple(sorted((a, b)))).fetchone():
            continue
        kind, rep = roll_relationship(attitude(races.get(a), races.get(b)))
        set_relationship(conn, a, b, kind, rep, origin=origin)
        made += 1
    return made


def feast(conn, world_id, city, name):
    """One settlement keeps a feast. Returns its report (None if it was not kept)."""
    from Data.Database.repository import create_event
    if _cancelled(conn, world_id, city) or not city["people"]:
        return None
    people = city["people"] + (city["households"] or 0) * 5
    joy = FEAST_JOY
    if city["type"] == "Village":
        food = min(city["household_store"] or 0, FEAST_FOOD_PER_PERSON * people)
        conn.execute("UPDATE cities SET household_store = household_store - ? WHERE id = ?", (food, city["id"]))
    else:
        gold = FEAST_GOLD_PER_PERSON * city["people"]
        food_have = conn.execute("SELECT COALESCE(amount, 0) FROM city_resources WHERE city_id = ? AND resource_name = 'Food'",
                                 (city["id"],)).fetchone()
        food = min(food_have[0] if food_have else 0, FEAST_FOOD_PER_PERSON * city["people"])
        if (city["wealth"] or 0) < gold:
            joy *= MEAGRE                                   # a poor town's feast
            gold = max(0, city["wealth"] or 0) * 0.5
        conn.execute("UPDATE cities SET wealth = wealth - ? WHERE id = ?", (gold, city["id"]))
        conn.execute("UPDATE city_resources SET amount = MAX(0, amount - ?) WHERE city_id = ? AND resource_name = 'Food'",
                     (food, city["id"]))
    conn.execute("""UPDATE npcs SET needs_happiness = MIN(100, needs_happiness + ?), needs_stress = MAX(0, needs_stress - ?)
                    WHERE city_id = ? AND alive = 1""", (joy, FEAST_CALM, city["id"]))
    folk = [r[0] for r in conn.execute("SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND age >= 14", (city["id"],))]
    n = int(len(folk) * FEAST_FRIENDS)
    pairs = [tuple(random.sample(folk, 2)) for _ in range(n)] if len(folk) >= 2 else []
    friends = _befriend(conn, pairs, f"met at {name}")
    create_event(conn, "Feast", f"{city['name']} kept {name}" + (" (a meagre feast)" if joy < FEAST_JOY else "") + ".",
                 city_id=city["id"])
    return {"city": city["id"], "feast": name, "joy": joy, "friends": friends}


def fair(conn, world_id, city):
    """A town's yearly fair. Returns its report."""
    from Data.Infrastructure.travel import dispatch_npc_travel, get_traveling_npc_ids
    from Data.Database.repository import create_event, adjust_kingdom_favor
    if _cancelled(conn, world_id, city):
        return None
    near = {r[0] for r in conn.execute("SELECT city_b_id FROM roads WHERE city_a_id = ? UNION SELECT city_a_id FROM roads WHERE city_b_id = ?",
                                       (city["id"], city["id"]))}
    if not near:
        return None
    away = set(get_traveling_npc_ids(conn, world_id))
    marks = ",".join("?" * len(near))
    candidates = conn.execute(
        f"""SELECT n.*, c.kingdom_id FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE n.alive = 1 AND n.age >= 16 AND n.gold >= ? AND n.disease IS NULL AND n.city_id IN ({marks})""",
        (FAIR_MIN_GOLD, *near)).fetchall()
    goers = [dict(c) for c in candidates if c["id"] not in away and random.random() < FAIR_GOER_CHANCE]
    locals_ = [r[0] for r in conn.execute("SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND age >= 14", (city["id"],))]
    foreign = {}
    pairs = []
    for g in goers:
        dispatch_npc_travel(conn, g, city["id"], purpose="Fair", stay_days=FAIR_DAYS)
        conn.execute("UPDATE npcs SET gold = gold - ? WHERE id = ?", (FAIR_SPEND, g["id"]))
        if g["kingdom_id"] != city["kingdom_id"]:
            foreign[g["kingdom_id"]] = foreign.get(g["kingdom_id"], 0) + 1
        pairs += [(g["id"], random.choice(locals_)) for _ in range(FAIR_FRIENDS)] if locals_ else []
    takings = FAIR_SPEND * len(goers)
    conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?", (takings, city["id"]))
    for kid, n in foreign.items():
        adjust_kingdom_favor(conn, kid, city["kingdom_id"], min(FAIR_FAVOR_MAX, FAIR_FAVOR * n))
    friends = _befriend(conn, pairs, f"met at the fair of {city['name']}")
    create_event(conn, "Fair", f"The fair of {city['name']}: {len(goers)} came from the country round about"
                 + (f" ({sum(foreign.values())} from other realms)" if foreign else "") + f", spending {takings:.0f} gold.",
                 city_id=city["id"])
    return {"city": city["id"], "goers": len(goers), "foreign": sum(foreign.values()), "takings": takings, "friends": friends}


def _rider(conn, npc_id):
    from Data.Combat import melee
    npc = conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    skills = dict(conn.execute("SELECT skill_name, level FROM npc_skills WHERE npc_id = ?", (npc_id,)).fetchall())
    f = melee.person_fighter(dict(npc), side=str(npc_id), armored=True, skills=skills)
    f.spells = []                                   # no spells in the lists
    return f


def joust(conn, a_id, b_id):
    """Two ride against each other until one yields. Returns (winner id, loser id, loser died)."""
    from Data.Combat import melee
    a, b = _rider(conn, a_id), _rider(conn, b_id)
    for _ in range(50):
        hit_b, hit_a = melee.attack_all(a, b), melee.attack_all(b, a)
        b.hp -= hit_b
        a.hp -= hit_a
        down_a, down_b = a.hp <= a.max_hp * YIELD_AT, b.hp <= b.max_hp * YIELD_AT
        if down_a or down_b:
            if down_a and down_b:
                loser = a if a.hp / a.max_hp < b.hp / b.max_hp else b
            else:
                loser = a if down_a else b
            winner = b if loser is a else a
            died = loser.hp <= 0 and random.random() < JOUST_KILL_CHANCE
            return winner.ref, loser.ref, died
    return (a.ref, b.ref, False) if a.hp >= b.hp else (b.ref, a.ref, False)


def tournament(conn, world_id, kingdom_id):
    """A capital's tournament. Returns its report (None if it was not held)."""
    from Data.Database.repository import get_npc, save_npc, spend_treasury, create_event, get_kingdom_favor
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    from Data.History.chronicle import write_entry, current_date
    k = conn.execute("SELECT * FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    if not k or k["status"] == "Fallen" or not k["capital_city_id"] or (k["treasury"] or 0) < TOURNEY_PRIZE:
        return None
    capital = conn.execute("SELECT * FROM cities WHERE id = ?", (k["capital_city_id"],)).fetchone()
    if _cancelled(conn, world_id, dict(capital) | {"type": capital["type"]}):
        return None
    marks = ",".join("?" * len(TOURNEY_ENTRANTS))
    home = [r[0] for r in conn.execute(
        f"""SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1
            AND n.disease IS NULL AND n.profession IN ({marks})""", (kingdom_id, *TOURNEY_ENTRANTS))]
    guests = []
    for other in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND id != ? AND status != 'Fallen'", (world_id, kingdom_id)):
        if get_kingdom_favor(conn, other[0], kingdom_id) >= TOURNEY_FRIENDLY:
            guests += [r[0] for r in conn.execute(
                f"""SELECT n.id FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1
                    AND n.disease IS NULL AND n.profession IN ({marks})""", (other[0], *TOURNEY_ENTRANTS))]
    random.shuffle(home)
    random.shuffle(guests)
    riders = guests[:TOURNEY_GUESTS] + home
    riders = riders[:TOURNEY_MAX]
    if len(riders) < 2:
        return None
    rounds, fallen = list(riders), []
    while len(rounds) > 1:
        nxt = []
        if len(rounds) % 2:
            nxt.append(rounds.pop())                    # a bye
        for i in range(0, len(rounds), 2):
            w, l, died = joust(conn, rounds[i], rounds[i + 1])
            nxt.append(w)
            if died:
                fallen.append(l)
        rounds = nxt
    champion = rounds[0]
    for rid in riders:
        gain = CHAMPION_XP if rid == champion else TOURNEY_XP
        row = conn.execute("SELECT level, xp FROM npc_skills WHERE npc_id = ? AND skill_name = 'Swordsmanship'", (rid,)).fetchone()
        if row:
            from Data.NPCS.employment import add_skill_xp
            skill = {"level": row[0], "xp": row[1] + 0}
            add_skill_xp(skill, gain)
            conn.execute("UPDATE npc_skills SET level = ?, xp = ? WHERE npc_id = ? AND skill_name = 'Swordsmanship'",
                         (skill["level"], skill["xp"], rid))
        else:
            conn.execute("INSERT INTO npc_skills (npc_id, skill_name, level, xp) VALUES (?, 'Swordsmanship', 1, ?)", (rid, gain))
    for rid in fallen:
        npc = get_npc(conn, rid)
        if npc and npc["alive"]:
            npc.update(alive=False, health=0, workplace=None, home=None)
            save_npc(conn, npc)
            death_event(conn, npc, "the lists", city_id=capital["id"])
            apply_grief(conn, npc)
    from Data.NPCS.personality import nudge          # glory for the champion; the unhorsed are humbled
    nudge(conn, [champion], "glory")
    nudge(conn, [r for r in riders if r != champion and r not in fallen], "beaten")
    spend_treasury(conn, kingdom_id, TOURNEY_PRIZE)
    conn.execute("UPDATE npcs SET gold = gold + ?, renown = renown + ? WHERE id = ?", (TOURNEY_PRIZE, CHAMPION_RENOWN, champion))
    conn.execute("UPDATE npcs SET needs_happiness = MIN(100, needs_happiness + ?) WHERE city_id = ? AND alive = 1",
                 (FEAST_JOY // 2, capital["id"]))
    champ = get_npc(conn, champion)
    champ_home = conn.execute("SELECT k.name FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE c.id = ?",
                              (champ["city_id"],)).fetchone()[0]
    year, season = current_date(conn, world_id)
    write_entry(conn, kingdom_id, "Tournament", "Tournament", year, season,
                {"kingdom": k["name"], "city": capital["name"], "champion": champ["name"], "other": champ_home,
                 "riders": len(riders)}, seed_parts=(kingdom_id, year))
    create_event(conn, "Tournament", f"{champ['name']} of {champ_home} won the tournament at {capital['name']} against "
                 f"{len(riders) - 1} riders" + (f"; {len(fallen)} died in the lists." if fallen else "."), city_id=capital["id"])
    return {"kingdom": kingdom_id, "riders": len(riders), "champion": champion, "fallen": fallen, "guests": min(len(guests), TOURNEY_GUESTS)}


def festivals_month(conn, world_id):
    """Monthly: the feasts that fall this month, the fairs that open, and (at midsummer) the tournaments.
    Returns the reports."""
    year, month = _clock(conn, world_id)
    reports = []
    for city in _settlements(conn, world_id):
        for name, m in FEASTS.items():
            if m == month:
                r = feast(conn, world_id, city, name)
                if r:
                    reports.append(r)
        if founding_month(city["id"]) == month:
            r = feast(conn, world_id, city, f"the Founding Day of {city['name']}")
            if r:
                reports.append(r)
        if city["type"] != "Village" and (city["type"] == "Capital" or city["people"] >= FAIR_MIN_PEOPLE) \
                and fair_month(city["id"]) == month:
            r = fair(conn, world_id, city)
            if r:
                reports.append(r)
    if month == TOURNEY_MONTH:
        for (kid,) in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall():
            r = tournament(conn, world_id, kid)
            if r:
                reports.append(r)
    conn.commit()
    return reports
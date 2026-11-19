"""
Prosperity: how well a town is doing, what it draws, what it builds, and the
merchant families it raises.

The score   each month every town and capital gets a prosperity score, 0-100,
            from what the world already tracks (WEIGHTS):
                purse     its purse a head, against the other towns of the world
                work      the share of its grown folk in a trade (not day labour)
                mood      how happy its people are
                trade     caravans it has sent and received, a head, against
                          the other towns
                safety    few thefts and little sickness
            It is shown on the map: thriving (RICH_AT and up), struggling
            (below POOR_AT), steady between.
Growth      each month households in a struggling town may move to the most
            prosperous town of their kingdom with a house free -- the poor
            towns shrink, the rich ones grow.
Grand       a thriving town with the gold in its purse raises a grand building,
buildings   one of each kind, chronicled:
                Cathedral  its people are happier (CATHEDRAL_JOY)
                Guildhall  trade pays its purse each month (GUILDHALL_TAKINGS a head)
                Great Library  its working folk learn faster (LIBRARY_XP a month in every skill)
Merchant    a family whose living members hold MERCHANT_WEALTH in gold between
families    them is a Merchant House. Each year a crown with a county lacking a
            lord sells it to its richest merchant house: their eldest grown
            member becomes its Count or Countess, the family a noble house
            (chronicled), and the gold goes to the crown.
Upkeep      each month every settlement pays for its watch, its streets and
            walls (UPKEEP_PER_HEAD), and its council spends a share of what
            its purse holds beyond a reserve (RESERVE_PER_HEAD) on works,
            wages and show. Measured with the long test run (long_run.py):
            bread money and market fees flowed into the purses and nothing
            large flowed out, so they grew by 17 million a year without end;
            with upkeep a purse settles where its spending meets its takings.
            A crown likewise spends CROWN_SURPLUS_SPEND of its treasury beyond
            CROWN_RESERVE_PER_HEAD a subject on its court, castles and
            officials (long_run.py: treasuries otherwise grew by 2 million a
            year with nothing to spend it on).
"""

from __future__ import annotations

import math
import random

WEIGHTS = {"purse": 0.3, "work": 0.2, "mood": 0.2, "trade": 0.15, "safety": 0.15}
RICH_AT = 65
POOR_AT = 35
MOVE_CHANCE = 0.02               # a month, for each household of a struggling town (more the wider the gap)
MOVE_MIN_GAP = 25                # the destination must score at least this much higher

GRAND = {                        # kind: (purse it costs, Stone, Wood)
    "Cathedral": (20000, 300, 150),
    "Guildhall": (15000, 200, 150),
    "Great Library": (12000, 150, 150),
}
GRAND_AT = 70                    # a town this prosperous...
GRAND_CHANCE = 0.25              # ...and able to pay, builds with this chance a month
CATHEDRAL_JOY = 5
GUILDHALL_TAKINGS = 1.0          # gold a head, a month, into the purse
LIBRARY_XP = 30                  # a month, in every skill of every working person

UPKEEP_PER_HEAD = 1.0            # gold a month, each of its people: the watch, streets and walls
RESERVE_PER_HEAD = 100           # a council keeps this much a head in its purse...
SURPLUS_SPEND = 0.1              # ...and spends this share of the rest each month
CROWN_RESERVE_PER_HEAD = 200     # a crown keeps this much a subject in its treasury (for famine grain, wars)...
CROWN_SURPLUS_SPEND = 0.05       # ...and spends this share of the rest each month
MERCHANT_WEALTH = 20000          # gold held by a family's living members between them
TITLE_PRICE = 2000               # what the crown asks for a county


def _towns(conn, world_id):
    return [dict(r) for r in conn.execute(
        """SELECT c.id, c.name, c.type, c.kingdom_id, c.wealth, k.name kingdom,
                  (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1) people
           FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND c.type != 'Village'""", (world_id,))]


def _rank(values):
    """Each value's place among the others, 0 (lowest) to 1 (highest)."""
    order = sorted(values.values())
    n = len(order)
    if n <= 1 or order[0] == order[-1]:
        return {k: 0.5 for k in values}
    return {k: sum(1 for x in order if x < v) / (n - 1) for k, v in values.items()}


def scores(conn, world_id):
    """{town id: (score, parts)} for every town and capital."""
    from Data.Government.edicts import today
    day = today(conn, world_id)
    towns = [t for t in _towns(conn, world_id) if t["people"]]
    if not towns:
        return {}
    ids = [t["id"] for t in towns]
    marks = ",".join("?" * len(ids))
    purse = {t["id"]: (t["wealth"] or 0) / t["people"] for t in towns}
    trade = {i: 0.0 for i in ids}
    for r in conn.execute(f"""SELECT origin_city_id, destination_city_id FROM caravans
                              WHERE origin_city_id IN ({marks}) OR destination_city_id IN ({marks})""", (*ids, *ids)):
        for c in r:
            if c in trade:
                trade[c] += 1
    trade = {i: trade[i] / max(1, next(t["people"] for t in towns if t["id"] == i)) for i in ids}
    rows = {r[0]: r for r in conn.execute(
        f"""SELECT city_id, SUM(age >= 16), SUM(age >= 16 AND profession NOT IN ('Laborer', 'Child')),
                   AVG(needs_happiness), SUM(disease IS NOT NULL), COUNT(*)
            FROM npcs WHERE alive = 1 AND city_id IN ({marks}) GROUP BY city_id""", ids)}
    thefts = {r[0]: r[1] for r in conn.execute(
        f"SELECT city_id, COUNT(*) FROM crimes WHERE kind = 'Theft' AND day >= ? AND city_id IN ({marks}) GROUP BY city_id", (day - 90, *ids))} \
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'crimes'").fetchone() else {}
    purse_rank, trade_rank = _rank(purse), _rank(trade)
    out = {}
    for t in towns:
        r = rows.get(t["id"])
        adults, working, mood, sick, n = (r[1] or 0, r[2] or 0, r[3] or 50, r[4] or 0, r[5] or 1) if r else (0, 0, 50, 0, 1)
        safety = max(0.0, 1 - (thefts.get(t["id"], 0) * 100 / n) / 5 - 5 * sick / n)
        parts = {"purse": purse_rank[t["id"]], "work": working / adults if adults else 0.0, "mood": mood / 100,
                 "trade": trade_rank[t["id"]], "safety": safety}
        out[t["id"]] = (round(100 * sum(WEIGHTS[k] * v for k, v in parts.items()), 1), parts)
    return out


def describe(score):
    if score is None:
        return "unknown"
    return "thriving" if score >= RICH_AT else "struggling" if score < POOR_AT else "steady"


def update_scores(conn, world_id):
    s = scores(conn, world_id)
    conn.executemany("UPDATE cities SET prosperity = ? WHERE id = ?", [(v[0], k) for k, v in s.items()])
    return s


def draw_settlers(conn, world_id, s):
    """Households of struggling towns move to their kingdom's most prosperous town with room. Returns how many moved."""
    from Data.Infrastructure.migration import get_family_members, RULING_TITLES
    from Data.Infrastructure.travel import dispatch_npc_travel, get_traveling_npc_ids
    from Data.Database.repository import get_npc, find_open_building
    towns = {t["id"]: t for t in _towns(conn, world_id)}
    away = set(get_traveling_npc_ids(conn, world_id))
    moved = 0
    by_kingdom = {}
    for tid, (score, _) in s.items():
        by_kingdom.setdefault(towns[tid]["kingdom_id"], []).append((score, tid))
    for kid, ranked in by_kingdom.items():
        ranked.sort(reverse=True)
        for score, tid in ranked:
            if score >= POOR_AT:
                continue
            dest = next((d for ds, d in ranked if ds - score >= MOVE_MIN_GAP and ds >= RICH_AT - 10
                         and find_open_building(conn, d, "House", "home_id")), None)
            if dest is None:
                continue
            chance = MOVE_CHANCE * (1 + (POOR_AT - score) / POOR_AT)
            handled = set()
            for (npc_id,) in conn.execute("SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND age >= 18", (tid,)).fetchall():
                if npc_id in handled or npc_id in away or random.random() >= chance:
                    continue
                npc = get_npc(conn, npc_id)
                family = get_family_members(conn, npc)
                handled.update(m["id"] for m in family)
                if any(m.get("title") in RULING_TITLES for m in family) or any(m["id"] in away for m in family):
                    continue
                for m in family:
                    dispatch_npc_travel(conn, m, dest, purpose="Migration")
                moved += 1
                away.update(m["id"] for m in family)
    return moved


def build_grand(conn, world_id, s):
    """Thriving towns with the gold raise a grand building. Returns [(town id, kind)]."""
    from Data.Database.repository import create_building, create_event
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    built = []
    for t in _towns(conn, world_id):
        if s.get(t["id"], (0,))[0] < GRAND_AT or random.random() >= GRAND_CHANCE:
            continue
        have = {r[0] for r in conn.execute("SELECT type FROM buildings WHERE city_id = ? AND category = 'Grand'", (t["id"],))}
        stores = dict(conn.execute("SELECT resource_name, amount FROM city_resources WHERE city_id = ?", (t["id"],)).fetchall())
        for kind, (gold, stone, wood) in GRAND.items():
            if kind in have or (t["wealth"] or 0) < gold or stores.get("Stone", 0) < stone or stores.get("Wood", 0) < wood:
                continue
            conn.execute("UPDATE cities SET wealth = wealth - ? WHERE id = ?", (gold, t["id"]))
            conn.execute("UPDATE city_resources SET amount = amount - ? WHERE city_id = ? AND resource_name = 'Stone'", (stone, t["id"]))
            conn.execute("UPDATE city_resources SET amount = amount - ? WHERE city_id = ? AND resource_name = 'Wood'", (wood, t["id"]))
            create_building(conn, t["id"], f"the {kind} of {t['name']}", kind, "Grand", 0)
            write_entry(conn, t["kingdom_id"], "Grand Building", "Grand Building Raised", year, season,
                        {"kingdom": t["kingdom"], "city": t["name"], "building": kind.lower()}, seed_parts=(t["id"], kind))
            create_event(conn, "Grand Building", f"{t['name']} raised its {kind}.", city_id=t["id"])
            built.append((t["id"], kind))
            break                                                        # one a year is plenty
    return built


def grand_towns(conn, kind):
    return {r[0] for r in conn.execute("SELECT DISTINCT city_id FROM buildings WHERE type = ?", (kind,))}


def grand_benefits(conn, world_id):
    """Monthly: guildhalls pay their towns' purses; great libraries teach. Returns (gold paid, people taught)."""
    paid = 0.0
    for city_id in grand_towns(conn, "Guildhall"):
        people = conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1", (city_id,)).fetchone()[0]
        conn.execute("UPDATE cities SET wealth = wealth + ? WHERE id = ?", (GUILDHALL_TAKINGS * people, city_id))
        paid += GUILDHALL_TAKINGS * people
    taught = 0
    libraries = grand_towns(conn, "Great Library")
    if libraries:
        marks = ",".join("?" * len(libraries))
        taught = conn.execute(
            f"""UPDATE npc_skills SET xp = xp + ? WHERE npc_id IN (SELECT id FROM npcs WHERE alive = 1 AND age >= 16
                AND workplace_id IS NOT NULL AND city_id IN ({marks}))""", (LIBRARY_XP, *libraries)).rowcount
    return paid, taught


def merchant_houses(conn, world_id):
    """[(family id, name, gold, kingdom id)] of families rich enough to count as merchant houses, richest first."""
    return [tuple(r) for r in conn.execute(
        """SELECT f.id, f.name, SUM(n.gold) g, MIN(c.kingdom_id) FROM families f JOIN npcs n ON n.family_id = f.id AND n.alive = 1
           JOIN cities c ON c.id = n.city_id WHERE f.world_id = ? AND COALESCE(f.status, '') != 'Noble House'
           GROUP BY f.id HAVING SUM(n.gold) >= ? ORDER BY g DESC""", (world_id, MERCHANT_WEALTH))]


def ennoble(conn, world_id):
    """Yearly: a crown sells a lordless county to its richest merchant house. Returns [(family id, province id)]."""
    from Data.Database.repository import get_npc, save_npc, add_treasury, create_event
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    conn.execute("UPDATE families SET status = 'Merchant House' WHERE world_id = ? AND status IS NULL AND id IN (%s)" %
                 (",".join(str(h[0]) for h in merchant_houses(conn, world_id)) or "0"), (world_id,))
    raised = []
    for (kid, kname) in conn.execute("SELECT id, name FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall():
        county = conn.execute("""SELECT p.id, p.name FROM provinces p WHERE p.kingdom_id = ? AND
                                 (p.lord_id IS NULL OR (SELECT alive FROM npcs WHERE id = p.lord_id) = 0) LIMIT 1""", (kid,)).fetchone()
        if not county:
            continue
        house = next((h for h in merchant_houses(conn, world_id) if h[3] == kid), None)
        if not house:
            continue
        head = conn.execute("""SELECT id FROM npcs WHERE family_id = ? AND alive = 1 AND age >= 16 AND title IS NULL
                               ORDER BY age DESC LIMIT 1""", (house[0],)).fetchone()
        if not head:
            continue
        # the family pays from its members' purses, richest first
        owed = TITLE_PRICE
        for (mid, gold) in conn.execute("SELECT id, gold FROM npcs WHERE family_id = ? AND alive = 1 ORDER BY gold DESC", (house[0],)).fetchall():
            pay = min(owed, gold or 0)
            conn.execute("UPDATE npcs SET gold = gold - ? WHERE id = ?", (pay, mid))
            owed -= pay
            if owed <= 0:
                break
        add_treasury(conn, kid, TITLE_PRICE - max(0, owed))
        lord = get_npc(conn, head[0])
        lord["title"] = "Count" if lord["gender"] == "Male" else "Countess"
        save_npc(conn, lord)
        conn.execute("UPDATE provinces SET lord_id = ?, house = ?, regent_id = NULL WHERE id = ?",
                     (lord["id"], f"House of {house[1]}", county[0]))
        conn.execute("UPDATE families SET status = 'Noble House', ennobled_year = ? WHERE id = ?", (year, house[0]))
        write_entry(conn, kid, "Ennobled", "Merchant Ennobled", year, season,
                    {"kingdom": kname, "ruler": lord["name"], "county": county[1], "title": lord["title"]},
                    seed_parts=(kid, year, house[0]))
        create_event(conn, "Ennobled", f"The merchant house of {house[1]} bought {county[1]}; {lord['name']} is its {lord['title']}.",
                     kingdom_id=kid)
        raised.append((house[0], county[0]))
    return raised


def town_upkeep(conn, world_id):
    """
    Monthly: every settlement (villages too) pays its upkeep from its purse,
    as far as the purse goes: UPKEEP_PER_HEAD for each of its people, and
    SURPLUS_SPEND of whatever lies beyond RESERVE_PER_HEAD a head.
    The people: named folk, and a village's households of five. Returns the gold spent.
    """
    spent = 0.0
    for c in conn.execute(
            """SELECT c.id, c.wealth,
                      (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1) + COALESCE(c.households, 0) * 5 people
               FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,)).fetchall():
        purse = c[1] or 0.0
        if purse <= 0:
            continue
        cost = c[2] * UPKEEP_PER_HEAD + max(0.0, purse - c[2] * RESERVE_PER_HEAD) * SURPLUS_SPEND
        cost = round(min(purse, cost), 2)
        if cost > 0:
            conn.execute("UPDATE cities SET wealth = wealth - ? WHERE id = ?", (cost, c[0]))
            spent += cost
    return round(spent, 2)


def crown_upkeep(conn, world_id):
    """
    Monthly: every crown spends CROWN_SURPLUS_SPEND of its treasury beyond
    CROWN_RESERVE_PER_HEAD for each subject (named folk, and five to a village
    household) on its court, castles and officials. Returns the gold spent.
    """
    spent = 0.0
    for k in conn.execute(
            """SELECT k.id, k.treasury,
                      (SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = k.id AND n.alive = 1)
                      + (SELECT COALESCE(SUM(households), 0) FROM cities c WHERE c.kingdom_id = k.id) * 5 subjects
               FROM kingdoms k WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,)).fetchall():
        cost = round(max(0.0, (k[1] or 0) - k[2] * CROWN_RESERVE_PER_HEAD) * CROWN_SURPLUS_SPEND, 2)
        if cost > 0:
            conn.execute("UPDATE kingdoms SET treasury = treasury - ? WHERE id = ?", (cost, k[0]))
            spent += cost
    return round(spent, 2)


def prosperity_month(conn, world_id):
    """Monthly: scores, settlers to the thriving, grand buildings, their benefits, and upkeep. Returns a report."""
    s = update_scores(conn, world_id)
    moved = draw_settlers(conn, world_id, s)
    built = build_grand(conn, world_id, s)
    paid, taught = grand_benefits(conn, world_id)
    upkeep = town_upkeep(conn, world_id) + crown_upkeep(conn, world_id)
    conn.commit()
    return {"scores": s, "moved": moved, "built": built, "paid": paid, "taught": taught, "upkeep": upkeep}
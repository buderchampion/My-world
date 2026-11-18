import random

# Tribute empires. No world begins with one: a crown becomes an empire by
# war, and then makes its weaker neighbours pay.
#
# Proclamation  each year, a Crown that has taken EMPIRE_CONQUESTS towns by
#               war -- or DOMINANT_CONQUESTS while its army is at least
#               DOMINANCE times the average of every other realm's -- is
#               proclaimed an Empire. Its monarch is Emperor or Empress from
#               then on (Data.Government.succession.monarch_title), and the
#               chronicle records it.
#
# Demands       each year an empire may (DEMAND_CHANCE) demand tribute of one
#               neighbour it could march on (a Trade Route between capitals;
#               no ally, no treaty, not already paying) whose army is at most
#               1 / DEMAND_ODDS of its own. The weaker the neighbour, the more
#               likely it yields; a bold ruler is likelier to refuse, a timid
#               one to pay. A refusal means war at once (a campaign).
#
# Conquest      a kingdom that has lost a town to an empire in the last year,
#               and still stands, becomes its tributary too.
#
# Tribute       each year a tributary pays TRIBUTE_SHARE of its treasury as it
#               stood when it submitted (fixed then, the "tribute owed"), and
#               sends GRAIN_SHARE of whatever Food it has beyond its own needs
#               until the next harvest (Data.Economy.economy.food_sources), by
#               caravan to the empire's capital. One that cannot pay in full
#               falls behind; behind MISSED_LIMIT years running, the empire
#               marches. Paying breeds resentment: the tributary's favor
#               toward the empire sinks RESENTMENT a year.
#
# Revolt        each year, a tributary whose army has grown to at least
#               1 / REVOLT_ODDS of the empire's -- or whose empire has lost its
#               capital, or fallen -- may throw off the yoke (REVOLT_CHANCE,
#               more the more it resents). The empire may march to punish it
#               (PUNISH_CHANCE), if it is still the stronger.
#
# An empire never wars on a tributary that pays, nor a tributary on its
# empire (Data.Government.diplomacy.war_candidates).

EMPIRE_CONQUESTS = 2
DOMINANT_CONQUESTS = 1
DOMINANCE = 2.0
DEMAND_CHANCE = 0.5
DEMAND_ODDS = 2.0
YIELD_FULL_AT = 4.0          # at these odds or more a neighbour always yields
BOLDNESS_SWAY = 0.3          # a ruler of boldness +100 is this much less likely to yield (-100: more)
TRIBUTE_SHARE = 0.10
MIN_TRIBUTE = 100            # gold a year, however poor the tributary
GRAIN_SHARE = 0.10
MISSED_LIMIT = 2
RESENTMENT = 5
REVOLT_ODDS = 1.5
REVOLT_CHANCE = 0.3          # a year, when the odds allow it; doubled at favor -100, nothing at +100
PUNISH_CHANCE = 0.5


def soldiers(conn, kingdom_id):
    """How many soldiers a kingdom fields (every military profession, wherever they are)."""
    from Data.Government.military import MILITARY_PROFESSIONS
    marks = ",".join("?" * len(MILITARY_PROFESSIONS))
    return conn.execute(
        f"""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks})""",
        (kingdom_id, *MILITARY_PROFESSIONS)).fetchone()[0]


def conquests(conn, kingdom_id):
    """Towns this kingdom has taken by war (held or since lost)."""
    return conn.execute("SELECT COUNT(*) FROM occupations WHERE occupier_kingdom_id = ?", (kingdom_id,)).fetchone()[0]


def tribute_of(conn, a_id, b_id):
    """The paying tribute between two kingdoms, either way round, or None."""
    row = conn.execute(
        """SELECT * FROM tributaries WHERE status = 'Paying'
           AND ((empire_id = ? AND tributary_id = ?) OR (empire_id = ? AND tributary_id = ?))""",
        (a_id, b_id, b_id, a_id)).fetchone()
    return dict(row) if row else None


def _living(conn, world_id):
    return [dict(k) for k in conn.execute(
        "SELECT * FROM kingdoms WHERE world_id = ? AND status != 'Fallen' AND capital_city_id IS NOT NULL", (world_id,))]


def _ruler_name(conn, kingdom):
    row = conn.execute("SELECT name, title FROM npcs WHERE id = ?", (kingdom["ruler_id"],)).fetchone() if kingdom.get("ruler_id") else None
    return f"{row['title']} {row['name']}" if row and row["title"] else (row["name"] if row else "the crown")


def _chronicle(conn, world_id, kingdom_id, kind, key, fields, seed):
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    write_entry(conn, kingdom_id, kind, key, year, season, fields, seed_parts=seed)


# ------------------------------------------------------------------ proclamation
def proclaim_empires(conn, world_id):
    """Yearly: crowns that have won enough by war become empires. Returns their ids."""
    from Data.Database.repository import create_event
    from Data.Government.succession import monarch_title
    realms = _living(conn, world_id)
    strength = {k["id"]: soldiers(conn, k["id"]) for k in realms}
    proclaimed = []
    for k in realms:
        if k["type"] != "Crown":
            continue
        won = conquests(conn, k["id"])
        others = [strength[o["id"]] for o in realms if o["id"] != k["id"]]
        average = sum(others) / len(others) if others else 0
        dominant = average > 0 and strength[k["id"]] >= DOMINANCE * average
        if not (won >= EMPIRE_CONQUESTS or (won >= DOMINANT_CONQUESTS and dominant)):
            continue
        conn.execute("UPDATE kingdoms SET type = 'Empire' WHERE id = ?", (k["id"],))
        if k.get("ruler_id"):
            ruler = conn.execute("SELECT gender FROM npcs WHERE id = ? AND alive = 1", (k["ruler_id"],)).fetchone()
            if ruler:
                conn.execute("UPDATE npcs SET title = ? WHERE id = ?", (monarch_title(conn, k["id"], ruler["gender"]), k["ruler_id"]))
        k = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (k["id"],)).fetchone())
        _chronicle(conn, world_id, k["id"], "Empire", "Empire Proclaimed",
                   {"kingdom": k["name"], "ruler": _ruler_name(conn, k)}, (k["id"],))
        create_event(conn, "Empire", f"{k['name']} was proclaimed an empire.", kingdom_id=k["id"])
        proclaimed.append(k["id"])
    conn.commit()
    return proclaimed


# ------------------------------------------------------------------ submission
def submit(conn, world_id, empire, tributary, how, day):
    """The tributary begins paying. Returns the tributary record."""
    from Data.Database.repository import create_event
    owed = round(max(MIN_TRIBUTE, (tributary["treasury"] or 0) * TRIBUTE_SHARE), 2)
    cursor = conn.execute(
        """INSERT INTO tributaries (empire_id, tributary_id, since_day, how, tribute_gold) VALUES (?, ?, ?, ?, ?)""",
        (empire["id"], tributary["id"], day, how, owed))
    fields = {"kingdom": tributary["name"], "other": empire["name"], "ruler": _ruler_name(conn, tributary), "gold": f"{owed:,.0f}"}
    _chronicle(conn, world_id, tributary["id"], "Tribute", f"Tribute ({how})", fields, (empire["id"], day))
    _chronicle(conn, world_id, empire["id"], "Tribute", "Tribute (received)",
               {"kingdom": empire["name"], "other": tributary["name"], "gold": f"{owed:,.0f}"}, (tributary["id"], day))
    create_event(conn, "Tribute", f"{tributary['name']} became a tributary of {empire['name']} ({how}), owing {owed:,.0f} gold a year.",
                 kingdom_id=tributary["id"])
    return dict(conn.execute("SELECT * FROM tributaries WHERE id = ?", (cursor.lastrowid,)).fetchone())


def yield_chance(conn, odds, ruler_id):
    """How likely a neighbour is to yield to a demand at these odds (empire's army / its own)."""
    chance = min(1.0, max(0.0, (odds - 1) / (YIELD_FULL_AT - 1)))
    if odds >= YIELD_FULL_AT:
        return 1.0
    row = conn.execute("SELECT boldness FROM npcs WHERE id = ?", (ruler_id,)).fetchone() if ruler_id else None
    if row and row[0] is not None:
        chance -= BOLDNESS_SWAY * row[0] / 100
    return min(1.0, max(0.0, chance))


def make_demands(conn, world_id, day, year):
    """Yearly: each empire may demand tribute of one weaker neighbour. Returns [(empire id, target id, 'yielded'|'refused')]."""
    from Data.Database.repository import get_world_roads, get_kingdom
    from Data.Government.diplomacy import war_candidates
    from Data.Government.campaigns import start_campaign, active_campaign
    routes = [r for r in get_world_roads(conn, world_id) if r["kind"] == "Trade Route"]
    happened = []
    for empire in _living(conn, world_id):
        if empire["type"] != "Empire" or random.random() >= DEMAND_CHANCE:
            continue
        mine = soldiers(conn, empire["id"])
        game_empire = get_kingdom(conn, empire["id"])
        targets = []
        for _, other_id in war_candidates(conn, game_empire, routes, year):
            other = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (other_id,)).fetchone())
            if other["status"] == "Fallen" or other["type"] == "Empire":
                continue
            if conn.execute("SELECT 1 FROM tributaries WHERE tributary_id = ? AND status = 'Paying'", (other_id,)).fetchone():
                continue                                    # already pays someone
            theirs = soldiers(conn, other_id)
            odds = mine / max(1, theirs)
            if odds >= DEMAND_ODDS:
                targets.append((odds, other))
        if not targets:
            continue
        odds, target = max(targets, key=lambda t: t[0])     # the weakest
        if random.random() < yield_chance(conn, odds, target.get("ruler_id")):
            submit(conn, world_id, empire, target, "yielded", day)
            happened.append((empire["id"], target["id"], "yielded"))
        else:
            _chronicle(conn, world_id, target["id"], "Tribute", "Tribute (refused)",
                       {"kingdom": target["name"], "other": empire["name"], "ruler": _ruler_name(conn, target)}, (empire["id"], day))
            if not active_campaign(conn, empire["id"]):
                start_campaign(conn, game_empire, get_kingdom(conn, target["id"]), day)
            happened.append((empire["id"], target["id"], "refused"))
    conn.commit()
    return happened


def conquered_submit(conn, world_id, day):
    """Yearly: a kingdom that lost a town to an empire in the last year, and stands, becomes its tributary."""
    taken = []
    for row in conn.execute(
            """SELECT DISTINCT o.occupier_kingdom_id e, o.former_kingdom_id t FROM occupations o
               JOIN kingdoms ek ON ek.id = o.occupier_kingdom_id JOIN kingdoms tk ON tk.id = o.former_kingdom_id
               WHERE ek.world_id = ? AND ek.type = 'Empire' AND ek.status != 'Fallen'
                 AND tk.status != 'Fallen' AND tk.capital_city_id IS NOT NULL AND o.since_day > ?""",
            (world_id, day - 360)).fetchall():
        if conn.execute("SELECT 1 FROM tributaries WHERE tributary_id = ? AND status = 'Paying'", (row["t"],)).fetchone():
            continue
        empire = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (row["e"],)).fetchone())
        tributary = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (row["t"],)).fetchone())
        submit(conn, world_id, empire, tributary, "conquered", day)
        taken.append((row["e"], row["t"]))
    conn.commit()
    return taken


# ------------------------------------------------------------------ paying, and revolt
def pay_tribute(conn, world_id, day):
    """Yearly: every tributary pays its gold and sends its grain. Returns [(tributary id, gold, food, in full?)]."""
    from Data.Database.repository import get_city, get_kingdom_cities, save_city, adjust_kingdom_favor, spend_treasury, add_treasury
    from Data.Economy.economy import food_sources, food_horizon
    from Data.Infrastructure.caravans import dispatch_caravan
    from Data.Government.campaigns import start_campaign, active_campaign
    from Data.Database.repository import get_kingdom
    paid = []
    horizon = food_horizon(conn, world_id)
    for t in conn.execute(
            """SELECT tr.* FROM tributaries tr JOIN kingdoms e ON e.id = tr.empire_id
               WHERE e.world_id = ? AND tr.status = 'Paying'""", (world_id,)).fetchall():
        t = dict(t)
        empire = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (t["empire_id"],)).fetchone())
        tributary = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (t["tributary_id"],)).fetchone())
        if "Fallen" in (empire["status"], tributary["status"]) or not tributary["capital_city_id"]:
            conn.execute("UPDATE tributaries SET status = 'Ended', ended_day = ? WHERE id = ?", (day, t["id"]))
            continue

        gold = round(min(t["tribute_gold"], max(0.0, tributary["treasury"] or 0)), 2)
        if gold > 0 and spend_treasury(conn, tributary["id"], gold):
            add_treasury(conn, empire["id"], gold)
        else:
            gold = 0.0
        full = gold >= t["tribute_gold"] - 0.01

        food = 0.0
        if empire["capital_city_id"]:
            sources = food_sources([get_city(conn, c["id"]) for c in get_kingdom_cities(conn, tributary["id"])],
                                   tributary["capital_city_id"], horizon)
            left = round(sum(extra for _, extra in sources) * GRAIN_SHARE, 2)
            for source, extra in sources:
                part = round(min(left, extra), 2)
                if part <= 0:
                    continue
                source["resources"]["Food"] = round(source["resources"]["Food"] - part, 2)
                save_city(conn, source)
                if dispatch_caravan(conn, source["id"], empire["capital_city_id"], "Food", part,
                                    empire["id"], tributary["id"]) is None:
                    source["resources"]["Food"] = round(source["resources"]["Food"] + part, 2)
                    save_city(conn, source)
                else:
                    food += part
                left = round(left - part, 2)
                if left <= 0:
                    break

        missed = 0 if full else t["missed"] + 1
        conn.execute("UPDATE tributaries SET paid_gold = paid_gold + ?, paid_food = paid_food + ?, missed = ? WHERE id = ?",
                     (gold, round(food, 2), missed, t["id"]))
        adjust_kingdom_favor(conn, tributary["id"], empire["id"], -RESENTMENT)
        paid.append((tributary["id"], gold, round(food, 2), full))

        if missed >= MISSED_LIMIT and not active_campaign(conn, empire["id"]):
            conn.execute("UPDATE tributaries SET missed = 0 WHERE id = ?", (t["id"],))
            _chronicle(conn, world_id, tributary["id"], "Tribute", "Tribute (behind)",
                       {"kingdom": tributary["name"], "other": empire["name"]}, (empire["id"], day))
            start_campaign(conn, get_kingdom(conn, empire["id"]), get_kingdom(conn, tributary["id"]), day)
    conn.commit()
    return paid


def revolt_chance(conn, tributary_id, empire_id):
    from Data.Database.repository import get_kingdom_favor
    favor = get_kingdom_favor(conn, tributary_id, empire_id)
    return REVOLT_CHANCE * (1 - favor / 100)


def revolts(conn, world_id, day):
    """Yearly: tributaries strong enough -- or whose empire has faltered -- may throw off the yoke. Returns their ids."""
    from Data.Database.repository import create_event, adjust_kingdom_favor, get_kingdom
    from Data.Government.campaigns import start_campaign, active_campaign
    thrown = []
    for t in conn.execute(
            """SELECT tr.* FROM tributaries tr JOIN kingdoms e ON e.id = tr.empire_id
               WHERE e.world_id = ? AND tr.status = 'Paying'""", (world_id,)).fetchall():
        empire = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (t["empire_id"],)).fetchone())
        tributary = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (t["tributary_id"],)).fetchone())
        faltered = empire["status"] == "Fallen" or not empire["capital_city_id"]
        odds = soldiers(conn, empire["id"]) / max(1, soldiers(conn, tributary["id"]))
        if not (faltered or odds < REVOLT_ODDS):
            continue
        if not faltered and random.random() >= revolt_chance(conn, tributary["id"], empire["id"]):
            continue
        conn.execute("UPDATE tributaries SET status = 'Revolted', ended_day = ? WHERE id = ?", (day, t["id"]))
        _chronicle(conn, world_id, tributary["id"], "Tribute", "Tribute (thrown off)",
                   {"kingdom": tributary["name"], "other": empire["name"], "ruler": _ruler_name(conn, tributary)}, (empire["id"], day))
        if not faltered:
            _chronicle(conn, world_id, empire["id"], "Tribute", "Tribute (lost)",
                       {"kingdom": empire["name"], "other": tributary["name"]}, (tributary["id"], day))
            adjust_kingdom_favor(conn, empire["id"], tributary["id"], -30)
        create_event(conn, "Tribute", f"{tributary['name']} threw off the yoke of {empire['name']}.", kingdom_id=tributary["id"])
        thrown.append(tributary["id"])
        if not faltered and odds >= 1 and random.random() < PUNISH_CHANCE and not active_campaign(conn, empire["id"]):
            start_campaign(conn, get_kingdom(conn, empire["id"]), get_kingdom(conn, tributary["id"]), day)
    conn.commit()
    return thrown


def empires_year(conn, world_id, year):
    """Yearly, before the year's wars: proclamations, conquered and threatened neighbours submit, tribute, revolts."""
    from Data.Government.edicts import today
    day = today(conn, world_id)
    report = {"proclaimed": proclaim_empires(conn, world_id),
              "conquered": conquered_submit(conn, world_id, day),
              "demands": make_demands(conn, world_id, day, year),
              "revolts": revolts(conn, world_id, day)}
    report["paid"] = pay_tribute(conn, world_id, day)
    return report
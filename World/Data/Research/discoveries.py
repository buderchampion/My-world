"""
Scholars and discoveries (Discoveries.json).

Research    each month every kingdom's working Scholars earn research points
            (RESEARCH_PER_SCHOLAR, more the more learned they are: SKILL_STEP),
            half again as many in a town with a Great Library
            (Data.Economy.prosperity), more again once the kingdom has
            Paper and Ink or the Printing Press.
Discoveries the kingdom studies one discovery at a time -- whatever it needs
            most: farming when food is dear, healing when sickness is about,
            siegecraft when it is at war, otherwise any it can study (its
            prerequisites known), the cheaper likelier. A discovery already
            known to a kingdom it trades with is half as costly (KNOWN_NEARBY).
            When its research reaches the cost, the discovery is made: an
            event, and the chronicle.
Spreading   each year a discovery may spread from a kingdom that knows it:
            to a kingdom that trades with it (SPREAD_TRADE: the merchants
            carry it), or to a neighbour at peace (capitals within FAIR_REACH:
            folk meet at each other's fairs, SPREAD_FAIR). A conqueror who
            takes a town carries off the learning of the conquered
            (CONQUEST_LEARN for each of their discoveries). Each spread is
            chronicled by the kingdom that learns it.
Effects     (they add up across discoveries) -- harvest: villages reap more
            (Data.World.harvest); output: every workshop makes more
            (Data.Economy.economy); tool_wear: Tools wear out more slowly;
            sickness, contagion: sickness harms less and spreads less
            (Data.NPCS.sickness); siege: siege works rise faster
            (Data.Government.campaigns); tax: more of the taxes reach the
            crown (SimulationManager.on_day); research: scholars work faster.
            The day's systems read them from a cache (BONUS) refreshed each
            month and whenever a discovery is made.
"""

from __future__ import annotations

import json
import math
import random
from functools import lru_cache
from pathlib import Path

DISCOVERIES_FILE = Path(__file__).resolve().parent / "Discoveries.json"

RESEARCH_PER_SCHOLAR = 0.4     # research points a month, a scholar of little learning... (long_run.py: at 2.0, six
                               # kingdoms knew 31 discoveries by the second year; now about one each every few years)
SKILL_STEP = 10                # ...and as much again for every this many levels of their best skill
GREAT_LIBRARY = 1.5            # scholars in a town with a Great Library work this much faster
KNOWN_NEARBY = 0.5             # a discovery a trading partner knows costs this share
SPREAD_TRADE = 0.05            # a year, for each discovery a trading partner knows
SPREAD_FAIR = 0.02             # a year, for each discovery a neighbour at peace knows
FAIR_REACH = 2000
CONQUEST_LEARN = 0.5           # each of the conquered's discoveries, when a town is taken
DEAR_FOOD = 1.3                # a kingdom whose food is this dear studies farming first
SICK_SHARE = 0.03              # ...one with this share of its people sick, healing first

BONUS = {}                     # kingdom id -> {effect: total}, for the day's systems
CITY_KINGDOM = {}              # city id -> kingdom id, for the systems that know only a town


@lru_cache(maxsize=1)
def discoveries():
    with open(DISCOVERIES_FILE, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def bonus(kingdom_id, effect):
    """The total of an effect for a kingdom (0 if it knows nothing of it, or the cache is not yet filled)."""
    return BONUS.get(kingdom_id, {}).get(effect, 0.0)


def city_bonus(city_id, effect):
    return bonus(CITY_KINGDOM.get(city_id), effect)


def refresh(conn, world_id):
    """Fills the cache the day's systems read from."""
    table = discoveries()
    BONUS.clear()
    for kingdom_id, name in conn.execute(
            """SELECT d.kingdom_id, d.discovery FROM kingdom_discoveries d JOIN kingdoms k ON k.id = d.kingdom_id
               WHERE k.world_id = ?""", (world_id,)):
        for effect, value in table.get(name, {}).get("effects", {}).items():
            BONUS.setdefault(kingdom_id, {})[effect] = BONUS.get(kingdom_id, {}).get(effect, 0.0) + value
    CITY_KINGDOM.clear()
    CITY_KINGDOM.update({r[0]: r[1] for r in conn.execute(
        "SELECT c.id, c.kingdom_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?", (world_id,))})


def known(conn, kingdom_id):
    return {r[0] for r in conn.execute("SELECT discovery FROM kingdom_discoveries WHERE kingdom_id = ?", (kingdom_id,))}


def available(conn, kingdom_id):
    have = known(conn, kingdom_id)
    return [name for name, d in discoveries().items() if name not in have and all(n in have for n in d["needs"])]


def partners(conn, kingdom_id):
    """The kingdoms it has traded with (a caravan either way)."""
    return {r[0] for r in conn.execute(
        """SELECT DISTINCT CASE WHEN buyer_kingdom_id = ? THEN seller_kingdom_id ELSE buyer_kingdom_id END FROM caravans
           WHERE (buyer_kingdom_id = ? OR seller_kingdom_id = ?) AND buyer_kingdom_id != seller_kingdom_id""",
        (kingdom_id, kingdom_id, kingdom_id))}


def _today(conn, world_id):
    from Data.Government.edicts import today
    return today(conn, world_id)


def learn(conn, world_id, kingdom_id, name, how="research", from_kingdom_id=None):
    """The kingdom knows it now: recorded, logged and chronicled. how: research, trade, fair or conquest."""
    from Data.Database.repository import create_event
    from Data.History.chronicle import write_entry, current_date
    if name in known(conn, kingdom_id):
        return False
    conn.execute("""INSERT INTO kingdom_discoveries (kingdom_id, discovery, day, how, from_kingdom_id)
                    VALUES (?, ?, ?, ?, ?)""", (kingdom_id, name, _today(conn, world_id), how, from_kingdom_id))
    kingdom = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()[0]
    other = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (from_kingdom_id,)).fetchone()[0] if from_kingdom_id else ""
    about = discoveries()[name].get("about", "")
    text = (f"The scholars of {kingdom} discovered {name}: {about}." if how == "research"
            else f"{kingdom} learned {name} from {other} ({'by trade' if how == 'trade' else 'at the fairs' if how == 'fair' else 'by conquest'}).")
    event = create_event(conn, "Discovery", text, kingdom_id=kingdom_id)
    year, season = current_date(conn, world_id)
    write_entry(conn, kingdom_id, "Discovery", "Discovery Made" if how == "research" else f"Discovery Learned ({how})", year, season,
                {"kingdom": kingdom, "other": other, "discovery": name, "reason": about},
                source_event_id=event["id"] if isinstance(event, dict) else None, seed_parts=(kingdom_id, name))
    refresh(conn, world_id)
    return True


def choose(conn, kingdom, options):
    """What the kingdom studies next: what it needs most, else any (the cheaper likelier)."""
    table = discoveries()
    wants = None
    if (kingdom.get("price_level") or 1.0) >= DEAR_FOOD:
        wants = "farming"
    else:
        people, sick = conn.execute(
            """SELECT COUNT(*), SUM(n.disease IS NOT NULL) FROM npcs n JOIN cities c ON c.id = n.city_id
               WHERE c.kingdom_id = ? AND n.alive = 1""", (kingdom["id"],)).fetchone()
        if people and (sick or 0) / people >= SICK_SHARE:
            wants = "healing"
        elif conn.execute("SELECT 1 FROM campaigns WHERE attacker_kingdom_id = ? AND status != 'Over'", (kingdom["id"],)).fetchone():
            wants = "war"
    pick = [o for o in options if table[o]["kind"] == wants]
    pool = pick or options
    return random.choices(pool, weights=[1 / table[o]["cost"] for o in pool])[0]


def scholars_research(conn, kingdom_id):
    """A month of a kingdom's scholars: research points."""
    from Data.Economy.prosperity import grand_towns
    great = set(grand_towns(conn, "Great Library"))
    total = 0.0
    for npc_id, city_id in conn.execute(
            """SELECT n.id, n.city_id FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1
               AND n.profession = 'Scholar' AND n.workplace_id IS NOT NULL AND n.jailed_until IS NULL""", (kingdom_id,)).fetchall():
        best = conn.execute("SELECT COALESCE(MAX(level), 1) FROM npc_skills WHERE npc_id = ?", (npc_id,)).fetchone()[0]
        points = RESEARCH_PER_SCHOLAR * (1 + best / SKILL_STEP)
        total += points * (GREAT_LIBRARY if city_id in great else 1.0)
    from Data.History.eras import effect                         # an Age of Wonders
    return total * (1 + bonus(kingdom_id, "research")) * effect("research")


def research_month(conn, world_id):
    """Monthly: scholars earn research; a kingdom whose research reaches its study's cost makes the discovery.
    Returns the discoveries made [(kingdom id, name)]."""
    refresh(conn, world_id)
    table = discoveries()
    made = []
    for k in conn.execute("SELECT * FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall():
        k = dict(k)
        points = scholars_research(conn, k["id"])
        studying = k.get("studying")
        options = available(conn, k["id"])
        if not options:
            continue
        if studying not in options:
            studying = choose(conn, k, options)
        research = (k.get("research") or 0.0) + points
        cost = table[studying]["cost"]
        if any(studying in known(conn, p) for p in partners(conn, k["id"])):
            cost *= KNOWN_NEARBY
        if research >= cost:
            learn(conn, world_id, k["id"], studying, "research")
            made.append((k["id"], studying))
            research -= cost
            studying = None
        conn.execute("UPDATE kingdoms SET research = ?, studying = ? WHERE id = ?", (round(research, 2), studying, k["id"]))
    conn.commit()
    return made


def spread_year(conn, world_id):
    """Yearly: discoveries spread to trading partners and to neighbours at peace. Returns [(kingdom, name, how)]."""
    from Data.History.eras import effect as era_effect
    living = [dict(r) for r in conn.execute(
        """SELECT k.id, c.map_x, c.map_y FROM kingdoms k JOIN cities c ON c.id = k.capital_city_id
           WHERE k.world_id = ? AND k.status != 'Fallen'""", (world_id,))]
    knows = {k["id"]: known(conn, k["id"]) for k in living}
    spread = []
    for b in living:
        trade = partners(conn, b["id"])
        for a in living:
            if a["id"] == b["id"]:
                continue
            if a["id"] in trade:
                chance, how = SPREAD_TRADE, "trade"
            elif math.dist((a["map_x"], a["map_y"]), (b["map_x"], b["map_y"])) <= FAIR_REACH and not conn.execute(
                    """SELECT 1 FROM campaigns WHERE status != 'Over' AND ((attacker_kingdom_id = ? AND defender_kingdom_id = ?)
                       OR (attacker_kingdom_id = ? AND defender_kingdom_id = ?))""", (a["id"], b["id"], b["id"], a["id"])).fetchone():
                chance, how = SPREAD_FAIR, "fair"
            else:
                continue
            for name in knows[a["id"]] - knows[b["id"]]:
                needs = discoveries()[name]["needs"]
                if all(n in knows[b["id"]] for n in needs) and random.random() < chance * era_effect("spread"):
                    if learn(conn, world_id, b["id"], name, how, a["id"]):
                        knows[b["id"]].add(name)
                        spread.append((b["id"], name, how))
    conn.commit()
    return spread


def conquest_learn(conn, world_id, attacker_id, defender_id):
    """A town is taken: the conqueror carries off the learning of the conquered. Returns what it learned."""
    have = known(conn, attacker_id)
    out = []
    for name in sorted(known(conn, defender_id) - have):
        if all(n in have for n in discoveries()[name]["needs"]) and random.random() < CONQUEST_LEARN:
            if learn(conn, world_id, attacker_id, name, "conquest", defender_id):
                have.add(name)
                out.append(name)
    return out
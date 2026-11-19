"""
Eras: the world's great ages (Eras.json), and each kingdom's notable periods.

World eras  the world is always in one era. A new world opens in the Age of
            Peace. Each year, once an era has lasted MIN_YEARS, the world looks
            at what has been happening (LOOK_BACK years), measured against
            its size, and asks how strongly it speaks for each era:
              Darkness  deaths to monsters, the road and sickness (DARK_RATE)
              Want      kingdoms starving (HUNGRY_RATE, WANT_SHARE), or bad
                        harvests across the land (BAD_SHARE)
              War       campaigns begun (WAR_PER_REALM)
              Dragons   dragon attacks (DRAGON_DEEDS)
              Wonders   discoveries made (WONDERS_RATE)
              Peace     no campaign for PEACE_YEARS
            A new era begins when its signs are strong enough (1.0) and
            stronger than the present era's own; an era whose signs still
            hold (STAY) is not easily moved, so the ages drift slowly. When
            an age of Darkness or Want fades, the people may turn to the
            gods: an Age of Faith (FAITH_AFTER). No era lasts for ever: after
            MAX_YEARS a quiet one gives way to Peace, Wonders or Faith. Every
            living kingdom writes the new era into its chronicle, and every
            chronicle entry from then on carries the era's name.
Effects     an era changes the world noticeably (Eras.json): how often wars
            begin (Data.Government.diplomacy), harvests (Data.World.harvest),
            plague (Data.NPCS.sickness), monsters' raids and ambushes
            (Data.Monsters), dragons waking (Data.Monsters.dragons), research
            and its spread (Data.Research.discoveries), temple healing
            (Data.NPCS.healing), everyone's contentment (Data.NPCS.contentment)
            and the lords' loyalty (Data.Government.feudal).
Periods     each kingdom also has its own notable periods, one at a time, named
            for its ruler and chronicled when they begin and end:
              the Hungry Years       starvation killed HUNGRY_DEAD or more, or
                                     food is HUNGRY_PRICE times dear
              the Troubles           a rising, a civil war or a disputed
                                     crown, or the lords' loyalty below
                                     TROUBLED_LOYALTY
              the Great Mourning     sickness killed MOURNING_DEAD or more
              the Wars of <ruler>    WARS_FOR_PERIOD campaigns or more, either side
              the Golden Years of <ruler>  thriving towns (GOLDEN_PROSPERITY),
                                     a happy people (GOLDEN_HAPPY), no want
            (the first that holds, in that order). A period has effects too
            (PERIOD_EFFECTS): a golden age gladdens and steadies the realm;
            troubles, hunger and mourning darken it; war rallies the lords.
            A period ends when its cause has passed.
The day's systems read the effects from a cache refreshed each year (and when
a world is loaded): effect(key) for the world's era, added(key, kingdom) for
the era and the kingdom's period together.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path

ERAS_FILE = Path(__file__).resolve().parent / "Eras.json"

MIN_YEARS = 5
MAX_YEARS = 80
LOOK_BACK = 2                  # years the world looks back over
REALM_HOUSEHOLDS = 100         # a kingdom smaller than this (an exile's keep) is not counted
# What is enough to turn the age (strength 1.0). Measured with long_run.py: an ordinary
# world loses about 20 a year for every 1,000 households to monsters, the road and
# sickness, makes about one discovery a kingdom a year, and sees a dragon about once a year.
DARK_RATE = 32                 # deaths to monsters, on the road and to sickness, a year for every 1,000 households
HUNGRY_RATE = 10               # a kingdom starving this many a year for every 1,000 households is hungry...
WANT_SHARE = 0.5               # ...and want is when this share of the kingdoms is
BAD_SHARE = 0.35               # ...or this share of the land has had bad harvests
WAR_PER_REALM = 0.5            # campaigns begun, for every kingdom
DRAGON_DEEDS = 3               # dragon attacks (and dragons slain)
WONDERS_RATE = 1.5             # discoveries made by research, a year for every kingdom
PEACE_YEARS = 8                # no campaign for this long
STAY = 0.6                     # an era whose own signs are this strong holds against weaker ones
FAITH_AFTER = 0.5              # an age of darkness or want that has faded gives way to Faith this often
ORDER = ("Darkness", "Want", "War", "Dragons", "Wonders", "Faith", "Peace")
ADDITIVE = ("happiness", "loyalty")

HUNGRY_DEAD = 30
HUNGRY_PRICE = 1.6
TROUBLED_LOYALTY = 35
MOURNING_DEAD = 40
WARS_FOR_PERIOD = 2
GOLDEN_PROSPERITY = 70
GOLDEN_HAPPY = 75
PERIOD_EFFECTS = {"golden": {"happiness": 3, "loyalty": 5}, "troubles": {"happiness": -3},
                  "hungry": {"happiness": -2}, "mourning": {"happiness": -3}, "wars": {"loyalty": 3}}
PERIOD_NAMES = {"golden": "the Golden Years of {ruler}", "troubles": "the Troubles", "hungry": "the Hungry Years",
                "mourning": "the Great Mourning", "wars": "the Wars of {ruler}"}
ORDINALS = ["", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"]

ERA = {}            # the world's era's effects (one world runs at a time)
PERIOD = {}         # kingdom id -> its period's effects


@lru_cache(maxsize=1)
def eras():
    with open(ERAS_FILE, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def effect(key):
    """The world era's multiplier for `key` (1.0: no change)."""
    return ERA.get(key, 1.0)


def added(key, kingdom_id=None):
    """The era's addition to `key` (happiness, loyalty), with the kingdom's period's."""
    return ERA.get(key, 0.0) + PERIOD.get(kingdom_id, {}).get(key, 0.0)


def _today(conn, world_id):
    from Data.Government.edicts import today
    return today(conn, world_id)


def current(conn, world_id):
    row = conn.execute("SELECT * FROM world_eras WHERE world_id = ? AND ended_day IS NULL ORDER BY id DESC LIMIT 1",
                       (world_id,)).fetchone()
    return dict(row) if row else None


def era_name_of_kingdom(conn, kingdom_id):
    """The name of the era the kingdom's world is in, or None (for the chronicle)."""
    try:
        row = conn.execute("""SELECT e.name FROM world_eras e JOIN kingdoms k ON k.world_id = e.world_id
                              WHERE k.id = ? AND e.ended_day IS NULL ORDER BY e.id DESC LIMIT 1""", (kingdom_id,)).fetchone()
    except Exception:
        return None
    return row[0] if row else None


def refresh(conn, world_id):
    """Fills the cache the day's systems read from."""
    ERA.clear()
    era = current(conn, world_id)
    if era:
        ERA.update(eras()[era["era"]]["effects"])
    PERIOD.clear()
    for r in conn.execute("""SELECT p.kingdom_id, p.kind FROM kingdom_periods p JOIN kingdoms k ON k.id = p.kingdom_id
                             WHERE k.world_id = ? AND p.ended_day IS NULL""", (world_id,)):
        PERIOD[r[0]] = PERIOD_EFFECTS.get(r[1], {})


def _chronicle(conn, world_id, kingdom_id, key, fields, seed):
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    name = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()[0]
    write_entry(conn, kingdom_id, "Era", key, year, season, dict(fields, kingdom=name), seed_parts=seed)


def begin_era(conn, world_id, era, cause=""):
    """The world enters a new era: the old one ends, every living kingdom chronicles it. Returns its name."""
    from Data.Database.repository import create_event
    day = _today(conn, world_id)
    conn.execute("UPDATE world_eras SET ended_day = ? WHERE world_id = ? AND ended_day IS NULL", (day, world_id))
    before = conn.execute("SELECT COUNT(*) FROM world_eras WHERE world_id = ? AND era = ?", (world_id, era)).fetchone()[0]
    base = eras()[era]["name"]
    name = base if before == 0 else base.replace("the ", f"the {ORDINALS[min(before, len(ORDINALS) - 1)]} ", 1)
    conn.execute("INSERT INTO world_eras (world_id, era, name, began_day, cause) VALUES (?, ?, ?, ?, ?)",
                 (world_id, era, name, day, cause or eras()[era]["reason"]))
    refresh(conn, world_id)
    create_event(conn, "Era", f"The world entered {name}: {cause or eras()[era]['reason']}.", day=day)
    for (kid,) in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall():
        _chronicle(conn, world_id, kid, "Era Began", {"era": name, "reason": cause or eras()[era]["reason"]}, (world_id, day))
    return name


def ensure_era(conn, world_id):
    """A world with no era yet (a new one, or one older than eras) opens in the Age of Peace."""
    if not current(conn, world_id):
        begin_era(conn, world_id, "Peace", "the realms were young, and at peace")
    else:
        refresh(conn, world_id)


# ---------------------------------------------------------------- what the world has been through

def signs(conn, world_id, day):
    """What has happened in the LOOK_BACK years before `day`: the counts the triggers read."""
    since = day - LOOK_BACK * 360

    def count(sql, args=()):
        try:
            return conn.execute(sql, args).fetchone()[0] or 0
        except Exception:
            return 0
    realms = conn.execute(
        """SELECT k.id, COALESCE(SUM(c.households), 0) FROM kingdoms k LEFT JOIN cities c ON c.kingdom_id = k.id
           WHERE k.world_id = ? AND k.status != 'Fallen' AND k.type != 'Horde' GROUP BY k.id""", (world_id,)).fetchall()
    realms = {r[0]: r[1] for r in realms if r[1] >= REALM_HOUSEHOLDS}     # a lone exile's keep is not a realm
    households = max(1.0, sum(realms.values()))
    deaths = lambda pattern: count("""SELECT COUNT(*) FROM events WHERE type = 'Death' AND day >= ? AND day < ? AND description LIKE ?""",
                                   (since, day, pattern))
    starved = {r[0]: r[1] for r in conn.execute(
        """SELECT c.kingdom_id, COUNT(*) FROM events e JOIN cities c ON c.id = e.city_id
           WHERE e.type = 'Death' AND e.day >= ? AND e.day < ? AND e.description LIKE '%starvation%' GROUP BY c.kingdom_id""",
        (since, day))}
    hungry = sum(1 for kid, hh in realms.items() if starved.get(kid, 0) / LOOK_BACK / max(1.0, hh) * 1000 >= HUNGRY_RATE)
    harvests = conn.execute("""SELECT h.year, AVG(h.quality = 'Bad') FROM harvests h JOIN regions r ON r.id = h.region_id
                               WHERE r.world_id = ? AND h.year <= ? GROUP BY h.year ORDER BY h.year DESC LIMIT ?""",
                            (world_id, day // 360 + 1, LOOK_BACK)).fetchall()
    return {
        "kingdoms": len(realms),
        "households": households,
        "campaigns": count("""SELECT COUNT(*) FROM campaigns c JOIN kingdoms k ON k.id = c.attacker_kingdom_id
                              WHERE k.world_id = ? AND c.started_day >= ? AND c.started_day < ?""", (world_id, since, day)),
        "last_campaign": count("""SELECT MAX(c.started_day) FROM campaigns c JOIN kingdoms k ON k.id = c.attacker_kingdom_id
                                  WHERE k.world_id = ? AND c.started_day < ?""", (world_id, day)),
        "dark_dead": deaths("%Monster raid%") + deaths("%on the road%") + deaths("%sickness%"),
        "dragon_deeds": count("SELECT COUNT(*) FROM events WHERE type IN ('Dragon Attack', 'Dragon Slain') AND day >= ? AND day < ?",
                              (since, day)),
        "discoveries": count("""SELECT COUNT(*) FROM kingdom_discoveries d JOIN kingdoms k ON k.id = d.kingdom_id
                                WHERE k.world_id = ? AND d.day >= ? AND d.day < ? AND d.how = 'research'""", (world_id, since, day)),
        "bad": sum(r[1] for r in harvests) / len(harvests) if harvests else 0.0,
        "hungry": hungry,
    }


def strengths(s, day):
    """How strongly the world's recent years speak for each era: 1.0 is enough to begin one."""
    years, realms = LOOK_BACK, max(1, s["kingdoms"])
    quiet = (day - (s["last_campaign"] or 0)) / 360
    return {
        "Darkness": s["dark_dead"] / years / s["households"] * 1000 / DARK_RATE,
        "Want": max(s["hungry"] / realms / WANT_SHARE, s["bad"] / BAD_SHARE),
        "War": s["campaigns"] / realms / WAR_PER_REALM,
        "Dragons": s["dragon_deeds"] / DRAGON_DEEDS,
        "Wonders": s["discoveries"] / years / realms / WONDERS_RATE,
        "Peace": 0.0 if s["campaigns"] else min(quiet / PEACE_YEARS, 1.0),   # peace holds, but gives way to any strong sign
    }


def choose_era(s, now_era, day):
    """The era the world turns to, or None. A new one must speak strongly (1.0 or more) and louder than the one in
    force; an age whose signs have faded (below STAY) may give way to Faith (after darkness or want) or to Peace."""
    st = strengths(s, day)
    stay = st.get(now_era, 0.0)
    best = None
    for era in ORDER:                                   # the strongest; on a tie, the first in ORDER
        if era != now_era and st.get(era, 0.0) >= 1.0 and (best is None or st[era] > st[best]):
            best = era
    if best and st[best] > stay:
        return best
    if stay < STAY and now_era in ("Darkness", "Want") and random.random() < FAITH_AFTER:
        return "Faith"
    return None


def eras_year(conn, world_id):
    """Yearly: the world may enter a new era; each kingdom's period begins or ends. Returns what changed."""
    ensure_era(conn, world_id)
    day = _today(conn, world_id)
    era = current(conn, world_id)
    age = (day - era["began_day"]) / 360
    out = {"era": None, "periods": kingdom_periods(conn, world_id, day)}
    if age >= MIN_YEARS:
        turn = choose_era(signs(conn, world_id, day), era["era"], day)
        if turn:
            out["era"] = begin_era(conn, world_id, turn)
        elif age >= MAX_YEARS:
            out["era"] = begin_era(conn, world_id, random.choice([e for e in ("Peace", "Wonders", "Faith") if e != era["era"]]),
                                   "the long age passed, as all ages do")
    refresh(conn, world_id)
    conn.commit()
    return out


# ---------------------------------------------------------------- a kingdom's own periods

def kingdom_signs(conn, k, day):
    since = day - 360
    deaths = lambda pattern: conn.execute(
        """SELECT COUNT(*) FROM events e WHERE e.type = 'Death' AND e.day >= ? AND e.description LIKE ? AND
           (e.kingdom_id = ? OR e.city_id IN (SELECT id FROM cities WHERE kingdom_id = ?))""",
        (since, pattern, k["id"], k["id"])).fetchone()[0]
    troubles = conn.execute(
        """SELECT COUNT(*) FROM events WHERE kingdom_id = ? AND day >= ?
           AND type IN ('Rebellion', 'Civil War', 'Succession Crisis', 'Revolt')""", (k["id"], day - 720)).fetchone()[0]
    loyalty = conn.execute("SELECT AVG(loyalty) FROM provinces WHERE kingdom_id = ?", (k["id"],)).fetchone()[0]
    wars = conn.execute("""SELECT COUNT(*) FROM campaigns WHERE (attacker_kingdom_id = ? OR defender_kingdom_id = ?) AND started_day >= ?""",
                        (k["id"], k["id"], day - 720)).fetchone()[0]
    prosperity = conn.execute("SELECT AVG(prosperity) FROM cities WHERE kingdom_id = ? AND prosperity IS NOT NULL", (k["id"],)).fetchone()[0]
    happy = conn.execute("""SELECT AVG(n.needs_happiness) FROM npcs n JOIN cities c ON c.id = n.city_id
                            WHERE c.kingdom_id = ? AND n.alive = 1""", (k["id"],)).fetchone()[0]
    return {"starved": deaths("%starvation%"), "sick": deaths("%sickness%"), "troubles": troubles, "loyalty": loyalty,
            "wars": wars, "prosperity": prosperity or 0, "happy": happy or 0, "price": k.get("price_level") or 1.0}


def period_kind(s):
    if s["starved"] >= HUNGRY_DEAD or s["price"] >= HUNGRY_PRICE:
        return "hungry"
    if s["troubles"] or (s["loyalty"] is not None and s["loyalty"] < TROUBLED_LOYALTY):
        return "troubles"
    if s["sick"] >= MOURNING_DEAD:
        return "mourning"
    if s["wars"] >= WARS_FOR_PERIOD:
        return "wars"
    if s["prosperity"] >= GOLDEN_PROSPERITY and s["happy"] >= GOLDEN_HAPPY:
        return "golden"
    return None


def kingdom_periods(conn, world_id, day):
    """Each kingdom's period begins, carries on, or ends. Returns [(kingdom id, kind, 'began' | 'ended')]."""
    changes = []
    for k in conn.execute("SELECT * FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall():
        k = dict(k)
        kind = period_kind(kingdom_signs(conn, k, day))
        now = conn.execute("SELECT * FROM kingdom_periods WHERE kingdom_id = ? AND ended_day IS NULL", (k["id"],)).fetchone()
        if now and now["kind"] == kind:
            continue
        if now:
            conn.execute("UPDATE kingdom_periods SET ended_day = ? WHERE id = ?", (day, now["id"]))
            _chronicle(conn, world_id, k["id"], "Period Ended", {"era": now["name"]}, (now["id"],))
            changes.append((k["id"], now["kind"], "ended"))
        if kind:
            ruler = conn.execute("SELECT name, title FROM npcs WHERE id = ?", (k["ruler_id"],)).fetchone() if k["ruler_id"] else None
            who = f"{ruler['title'] or ''} {ruler['name']}".strip() if ruler else "the crown"
            name = PERIOD_NAMES[kind].format(ruler=who)
            cur = conn.execute("INSERT INTO kingdom_periods (kingdom_id, kind, name, began_day) VALUES (?, ?, ?, ?)",
                               (k["id"], kind, name, day))
            _chronicle(conn, world_id, k["id"], f"Period Began ({kind})", {"era": name, "ruler": who}, (cur.lastrowid,))
            changes.append((k["id"], kind, "began"))
    return changes
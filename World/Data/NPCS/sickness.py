"""
Sickness and plague (Diseases.json). People fall sick one by one, and it spreads.

Falling sick   everyday sicknesses (Winter Ague, Bloody Flux) start now and then
               with no one to catch them from, more in their season, among the
               hungry and in the slums. Plagues (Red Pox, the Great Pestilence)
               break out somewhere a few times a century (`outbreak` a year) and
               then only spread.
Spreading      each day a sick person may pass it to everyone in their house and
               their workplace, and to a few others of their town they mingle
               with (CONTACTS; twice as many in the slums). A plague also rides
               the roads: each day a town with plague cases may seed one in every
               town a road joins it to, likelier the more sick it holds -- far
               less under a quarantine (Data.Government.edicts).
Running its course
               each day it costs health: harm x the case's severity, worse for
               the old, small children and the hungry; healers (Healer and
               Priest trades) tend the sickest and soften it, a staffed temple a
               little more. A cleric or druid able to cast Cure Disease (a
               third-level spell: Data.Magic) ends a case outright. Health at 0
               is death; the days run out and they are well -- and immune, for a
               while or for life. The sick do not mend from rest until they are
               well (Data.NPCS.healing).
The dead       are mourned; a villager stands for many households, so a village
               loses its share of households with each one.
Chronicles     a plague's coming to a kingdom and its leaving (with its dead)
               are written, and its coming calls the ruler to a crisis review.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from pathlib import Path

DISEASES_FILE = Path(__file__).with_name("Diseases.json")

SEVERITY = (0.3, 1.5)          # how hard a case falls, against the sickness's own harm
OLD_AGE, OLD_WORSE = 60, 1.5   # the old suffer more...
SMALL_CHILD, CHILD_WORSE = 5, 1.3
HUNGRY_FED = 0.5               # ...and anyone fed below this share of their needs
HOUSE_CONTACTS = 6             # of their house (a slum is one great house: only those they share a corner with)...
WORK_CONTACTS = 4              # ...and of their workplace (a big mine's crew is not one room), each day
CONTACTS = 3                   # townsfolk a sick person mingles with each day, beyond house and work
SLUM_CONTACTS = 2              # times as many in the slums
PATIENTS_PER_HEALER = 5        # the sickest a Healer or Priest can tend each day...
TENDED = 0.6                   # ...whose sickness then does this share of its harm
TEMPLE = 0.85                  # and a staffed temple in town softens every case a little
HEALER_TRADES = ("Healer", "Priest")
ROAD_SPREAD = 0.0015           # each plague case gives this chance a day (at the Great Pestilence's contagion) to seed a road's far end
QUARANTINE_SHARE = 0.2         # a quarantined kingdom's sick towns pass plague along its roads this much as often
NOTICED_AFTER = 2              # a case is only seen for what it is (and cured) after this many days
CHRONICLE_AT = 10              # a plague is chronicled once this many in a kingdom are sick of it at once
REOPEN_WITHIN = 30             # a plague back in a kingdom this soon after it left is the same visitation
FARMSTEADS = 0.7               # a village's households, spread over their farmsteads, die at this share of its named villagers' rate
FEAR_AT = 3                    # a town with this many plague cases is afraid (Data.NPCS.contentment)


@lru_cache(maxsize=1)
def diseases():
    with open(DISEASES_FILE, encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def plagues():
    return [name for name, d in diseases().items() if d["kind"] == "plague"]


def _season(conn, world_id):
    from Data.Simulation.clock import SEASON_BY_MONTH
    month = conn.execute("SELECT clock_month FROM worlds WHERE id = ?", (world_id,)).fetchone()[0]
    return SEASON_BY_MONTH.get(month, "Spring")


def _people(conn, world_id):
    """Every living person: {id: row} with what sickness needs to know."""
    return {r["id"]: r for r in conn.execute(
        """SELECT n.id, n.city_id, n.home_id, n.workplace_id, n.age, n.fed, n.health, n.disease, n.disease_days,
                  n.disease_severity, n.profession, c.kingdom_id, c.type city_type,
                  (b.type = 'Slums') in_slums
           FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           LEFT JOIN buildings b ON b.id = n.home_id
           WHERE k.world_id = ? AND n.alive = 1""", (world_id,))}


def _immune(conn, day):
    return {(r[0], r[1]) for r in conn.execute(
        "SELECT npc_id, disease FROM npc_immunities WHERE until_day IS NULL OR until_day >= ?", (day,))}


def _frailty(p):
    worse = 1.0
    if p["age"] >= OLD_AGE:
        worse *= OLD_WORSE
    elif p["age"] < SMALL_CHILD:
        worse *= CHILD_WORSE
    return worse


def _exposure(disease, p):
    """How much likelier this person is to catch (or start) it: hunger and the slums."""
    worse = disease.get("worse_for", {})
    x = 1.0
    if (p["fed"] if p["fed"] is not None else 1.0) < HUNGRY_FED:
        x *= worse.get("hungry", 1.0)
    if p["in_slums"]:
        x *= worse.get("slums", 1.0)
    return x


def catch(conn, npc_id, name):
    """`npc_id` falls sick of `name`."""
    d = diseases()[name]
    conn.execute("UPDATE npcs SET disease = ?, disease_days = ?, disease_severity = ? WHERE id = ?",
                 (name, random.randint(*d["days"]), round(random.uniform(*SEVERITY), 2), npc_id))


def fall_sick(conn, people, immune, season):
    """Everyday sicknesses starting by themselves. Returns how many fell sick."""
    started = 0
    for name, d in diseases().items():
        if d["kind"] != "common":
            continue
        base = d["onset"] * d["seasons"].get(season, 1.0)
        for p in people.values():
            if p["disease"] is None and (p["id"], name) not in immune and random.random() < base * _exposure(d, p):
                catch(conn, p["id"], name)
                started += 1
    return started


def spread(conn, people, immune, season):
    """The sick pass it on: house, work, and a few townsfolk. Returns {disease: new cases}."""
    by_city, by_home, by_work = {}, {}, {}
    for p in people.values():
        by_city.setdefault(p["city_id"], []).append(p["id"])
        if p["home_id"] is not None:
            by_home.setdefault(p["home_id"], []).append(p["id"])
        if p["workplace_id"] is not None:
            by_work.setdefault(p["workplace_id"], []).append(p["id"])
    sick = [p for p in people.values() if p["disease"]]
    caught = {}
    newly = set()
    for s in sick:
        d = diseases().get(s["disease"])
        if not d:
            continue
        from Data.Research.discoveries import city_bonus             # physicians' learning keeps the sick apart
        chance = d["contagion"] * d["seasons"].get(season, 1.0) * max(0.0, 1 + city_bonus(s["city_id"], "contagion"))
        house = by_home.get(s["home_id"], []) if s["home_id"] is not None else []
        work = by_work.get(s["workplace_id"], []) if s["workplace_id"] is not None else []
        near = set(random.sample(house, min(HOUSE_CONTACTS + 1, len(house)))) | set(random.sample(work, min(WORK_CONTACTS + 1, len(work))))
        town = by_city.get(s["city_id"], [])
        k = CONTACTS * (SLUM_CONTACTS if s["in_slums"] else 1)
        if town:
            near.update(random.sample(town, min(k, len(town))))
        near.discard(s["id"])
        for i in near:
            c = people[i]
            if c["disease"] or i in newly or (i, s["disease"]) in immune:
                continue
            if random.random() < chance * _exposure(d, c):
                catch(conn, i, s["disease"])
                newly.add(i)
                caught[s["disease"]] = caught.get(s["disease"], 0) + 1
    return caught


def road_spread(conn, world_id, people, immune, day):
    """Plague rides the roads: a town's cases may seed one in each town a road joins it to. Returns the towns reached."""
    from Data.Government.edicts import quarantined
    cases = {}
    for p in people.values():
        if p["disease"] in plagues():
            key = (p["city_id"], p["disease"])
            cases[key] = cases.get(key, 0) + 1
    if not cases:
        return []
    roads = {}
    for a, b in conn.execute("SELECT city_a_id, city_b_id FROM roads"):
        roads.setdefault(a, []).append(b)
        roads.setdefault(b, []).append(a)
    kingdom_of = {r[0]: r[1] for r in conn.execute(
        "SELECT c.id, c.kingdom_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ?", (world_id,))}
    shut = {r[0] for r in conn.execute("SELECT id FROM kingdoms WHERE world_id = ?", (world_id,))
            if quarantined(conn, r[0], day)}
    by_city = {}
    for p in people.values():
        by_city.setdefault(p["city_id"], []).append(p)
    reached = []
    worst = diseases()["Great Pestilence"]["contagion"] if "Great Pestilence" in diseases() else 0.035
    for (city, name), n in cases.items():
        per_case = ROAD_SPREAD * diseases()[name]["contagion"] / worst
        for other in roads.get(city, []):
            if any(p["disease"] == name for p in by_city.get(other, [])):
                continue
            chance = 1 - (1 - per_case) ** n
            if kingdom_of.get(city) in shut or kingdom_of.get(other) in shut:
                chance *= QUARANTINE_SHARE
            if random.random() < chance:
                well = [p for p in by_city.get(other, []) if not p["disease"] and (p["id"], name) not in immune]
                if well:
                    first = random.choice(well)
                    catch(conn, first["id"], name)
                    first_row = dict(first)
                    first_row["disease"] = name
                    people[first["id"]] = first_row
                    reached.append((other, name))
    return reached


def cure(conn, world_id, people, away):
    """Clerics and druids who can cast Cure Disease end the worst cases in their towns. Returns how many were cured."""
    from Data.Magic.magic import casters, prepare, MAGIC_TRADES
    fresh = {p["id"] for p in people.values() if p["disease"] and
             (p["disease_days"] or 0) > diseases()[p["disease"]]["days"][0] - NOTICED_AFTER} if people else set()
    sick_by_city = {}
    for p in people.values():
        if p["disease"]:
            sick_by_city.setdefault(p["city_id"], []).append(p)
    if not sick_by_city:
        return 0
    cured = 0
    for c in casters(conn, world_id, ("Cleric", "Druid", "Bard"), city_ids=set(sick_by_city)):
        if c["id"] in away or c["profession"] not in MAGIC_TRADES:
            continue                       # Cure Disease is a learned rite: hedge casters do not have it
        spells = [s for s in prepare(c["class"], c["level"], c["id"], wanted={"cure_disease"}) if s[2]["effect"] == "cure_disease"]
        patients = sorted((p for p in sick_by_city[c["city_id"]] if p["disease"] and p["id"] not in fresh), key=lambda p: p["health"])
        for _ in spells:
            if not patients:
                break
            p = patients.pop(0)
            conn.execute("UPDATE npcs SET disease = NULL, disease_days = 0, disease_severity = NULL WHERE id = ?", (p["id"],))
            row = dict(p)
            row["disease"] = None
            people[p["id"]] = row
            sick_by_city[c["city_id"]] = [x for x in sick_by_city[c["city_id"]] if x["id"] != p["id"]]
            cured += 1
    return cured


def run_course(conn, world_id, people, day, away):
    """Each case does a day's harm, softened by healers and temples; the well become immune.
    Returns ([dead ids], [recovered ids])."""
    sick = [p for p in people.values() if p["disease"]]
    if not sick:
        return [], []
    cities = {p["city_id"] for p in sick}
    marks = ",".join("?" * len(cities))
    healers = {}
    for r in conn.execute(f"""SELECT city_id, COUNT(*) FROM npcs WHERE alive = 1 AND city_id IN ({marks})
                              AND profession IN ('Healer', 'Priest') GROUP BY city_id""", list(cities)):
        healers[r[0]] = r[1]
    temples = {r[0] for r in conn.execute(
        f"""SELECT DISTINCT b.city_id FROM buildings b JOIN npcs n ON n.workplace_id = b.id
            WHERE b.type = 'Temple' AND n.alive = 1 AND b.city_id IN ({marks})""", list(cities))}
    tended = set()
    for city in cities:
        worst = sorted((p for p in sick if p["city_id"] == city), key=lambda p: p["health"])
        tended.update(p["id"] for p in worst[:PATIENTS_PER_HEALER * healers.get(city, 0)])
    dead, well = [], []
    for p in sick:
        d = diseases().get(p["disease"])
        if not d:
            continue
        harm = d["harm"] * (p["disease_severity"] or 1.0) * _frailty(p)
        from Data.Research.discoveries import city_bonus             # herbal lore and physicians' learning
        harm *= max(0.1, 1 + city_bonus(p["city_id"], "sickness"))
        if (p["fed"] if p["fed"] is not None else 1.0) < HUNGRY_FED:
            harm *= d.get("worse_for", {}).get("hungry", 1.0)
        if p["id"] in tended:
            harm *= TENDED
        if p["city_id"] in temples:
            harm *= TEMPLE
        health = (p["health"] or 0) - harm
        days = (p["disease_days"] or 1) - 1
        if health <= 0:
            dead.append((p["id"], p["disease"]))
            continue
        if days <= 0:
            conn.execute("UPDATE npcs SET health = ?, disease = NULL, disease_days = 0, disease_severity = NULL WHERE id = ?",
                         (round(health, 2), p["id"]))
            until = None if d["immune_days"] is None else day + d["immune_days"]
            conn.execute("INSERT INTO npc_immunities (npc_id, disease, until_day) VALUES (?, ?, ?)", (p["id"], p["disease"], until))
            well.append(p["id"])
        else:
            conn.execute("UPDATE npcs SET health = ?, disease_days = ? WHERE id = ?", (round(health, 2), days, p["id"]))
    return dead, well


def bury(conn, dead, people):
    """The sickness takes them: mourned, and a village's households thinned in proportion."""
    from Data.Database.repository import get_npc, save_npc
    from Data.Events.events import death_event
    from Data.Relationships.relationships import apply_grief
    for npc_id, name in dead:
        npc = get_npc(conn, npc_id)
        if not npc or not npc["alive"]:
            continue
        p = people[npc_id]
        if p["city_type"] == "Village":
            named = conn.execute("SELECT COUNT(*) FROM npcs WHERE city_id = ? AND alive = 1", (p["city_id"],)).fetchone()[0]
            if named:
                conn.execute("UPDATE cities SET households = MAX(0, households - households * ? / ?) WHERE id = ?",
                             (FARMSTEADS, named, p["city_id"]))
        npc.update(alive=False, health=0, workplace=None, home=None)
        save_npc(conn, npc)
        conn.execute("UPDATE npcs SET disease = NULL, disease_days = 0 WHERE id = ?", (npc_id,))
        death_event(conn, npc, "sickness", city_id=npc["city_id"])
        apply_grief(conn, npc)
        if name in plagues():
            conn.execute("UPDATE outbreaks SET dead = dead + 1 WHERE kingdom_id = ? AND disease = ? AND ended_day IS NULL",
                         (p["kingdom_id"], name))


def chronicle_outbreaks(conn, world_id, day):
    """A plague's coming to a kingdom and its leaving are written; its coming calls a crisis review.
    Returns (begun, ended): [(kingdom id, disease)]."""
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    present, counts = {}, {}
    for r in conn.execute(
            f"""SELECT c.kingdom_id, n.disease, COUNT(*), MIN(c.name) FROM npcs n JOIN cities c ON c.id = n.city_id
                JOIN kingdoms k ON k.id = c.kingdom_id
                WHERE k.world_id = ? AND n.alive = 1 AND n.disease IN ({','.join('?' * len(plagues()))})
                GROUP BY c.kingdom_id, n.disease""", (world_id, *plagues())):
        present[(r[0], r[1])] = r[3]
        counts[(r[0], r[1])] = r[2]
    active = {(r[1], r[2]): r for r in conn.execute(
        "SELECT id, kingdom_id, disease, dead, chronicled FROM outbreaks WHERE world_id = ? AND ended_day IS NULL", (world_id,))}
    names = {r[0]: r[1] for r in conn.execute("SELECT id, name FROM kingdoms WHERE world_id = ?", (world_id,))}
    begun, ended = [], []
    for (kingdom_id, name), city in present.items():
        row = active.get((kingdom_id, name))
        if row is None:
            recent = conn.execute("""SELECT id, dead, chronicled FROM outbreaks WHERE kingdom_id = ? AND disease = ?
                                     AND ended_day >= ? ORDER BY id DESC LIMIT 1""", (kingdom_id, name, day - REOPEN_WITHIN)).fetchone()
            if recent:                                   # back again so soon: the same visitation
                conn.execute("UPDATE outbreaks SET ended_day = NULL WHERE id = ?", (recent[0],))
                row = (recent[0], kingdom_id, name, recent[1], recent[2])
            else:
                cur = conn.execute("INSERT INTO outbreaks (world_id, kingdom_id, disease, started_day, dead) VALUES (?, ?, ?, ?, 0)",
                                   (world_id, kingdom_id, name, day))
                row = (cur.lastrowid, kingdom_id, name, 0, 0)
        if not row[4] and counts[(kingdom_id, name)] >= CHRONICLE_AT:     # grown into a plague worth the chronicle
            conn.execute("UPDATE outbreaks SET chronicled = 1 WHERE id = ?", (row[0],))
            write_entry(conn, kingdom_id, "Plague", "Plague Began", year, season,
                        {"kingdom": names.get(kingdom_id, "?"), "disease": name, "city": city}, seed_parts=(kingdom_id, day))
            begun.append((kingdom_id, name))
    for (kingdom_id, name), row in active.items():
        if (kingdom_id, name) not in present:
            conn.execute("UPDATE outbreaks SET ended_day = ? WHERE id = ?", (day, row[0]))
            if kingdom_id in names and row[4]:
                write_entry(conn, kingdom_id, "Plague", "Plague Ended", year, season,
                            {"kingdom": names[kingdom_id], "disease": name, "dead": row[3]}, seed_parts=(kingdom_id, day))
                ended.append((kingdom_id, name))
    return begun, ended


def sickness_day(conn, world_id, day=None):
    """Daily: sickness starts, spreads, is cured and tended, kills and passes. Returns a report."""
    from Data.Government.edicts import today, review
    from Data.Infrastructure.travel import get_traveling_npc_ids
    from Data.Government.armies import get_army_npc_ids
    day = today(conn, world_id) if day is None else day
    season = _season(conn, world_id)
    people = _people(conn, world_id)
    immune = _immune(conn, day)
    away = set(get_traveling_npc_ids(conn, world_id)) | set(get_army_npc_ids(conn, world_id))
    started = fall_sick(conn, people, immune, season)
    people = _people(conn, world_id)
    caught = spread(conn, people, immune, season)
    people = _people(conn, world_id)
    reached = road_spread(conn, world_id, people, immune, day)
    people = _people(conn, world_id)
    cured = cure(conn, world_id, people, away)
    dead, well = run_course(conn, world_id, people, day, away)
    bury(conn, dead, people)
    begun, ended = chronicle_outbreaks(conn, world_id, day)
    conn.commit()
    if begun:
        review(conn, world_id, kingdom_ids={k for k, _ in begun}, crisis=True)   # the ruler hears of it at once
    return {"started": started, "caught": caught, "reached": reached, "cured": cured, "dead": len(dead),
            "well": len(well), "begun": begun, "ended": ended}


def outbreak(conn, world_id, name, city_id=None, cases=3):
    """A plague breaks out: `cases` fall sick in one town (a busy one, by its people), or in `city_id`."""
    if city_id is None:
        towns = conn.execute(
            """SELECT c.id, COUNT(n.id) FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
               JOIN npcs n ON n.city_id = c.id AND n.alive = 1
               WHERE k.world_id = ? AND k.status != 'Fallen' AND c.type != 'Village' GROUP BY c.id""", (world_id,)).fetchall()
        if not towns:
            return None
        city_id = random.choices([t[0] for t in towns], weights=[t[1] for t in towns])[0]
    well = [r[0] for r in conn.execute("SELECT id FROM npcs WHERE city_id = ? AND alive = 1 AND disease IS NULL", (city_id,))]
    for i in random.sample(well, min(cases, len(well))):
        catch(conn, i, name)
    conn.commit()
    return city_id


def plagues_break_out(conn, world_id):
    """Monthly: each plague may break out somewhere (its `outbreak` chance a year). Returns [(disease, city id)]."""
    out = []
    for name in plagues():
        from Data.History.eras import effect                     # an Age of Darkness, or of Faith
        if random.random() < diseases()[name]["outbreak"] / 12 * effect("plague"):
            city = outbreak(conn, world_id, name)
            if city:
                out.append((name, city))
    return out


def plague_towns(conn, world_id=None, at_least=1):
    """{city id: plague cases} for towns with at least `at_least`."""
    marks = ",".join("?" * len(plagues()))
    return {r[0]: r[1] for r in conn.execute(
        f"""SELECT city_id, COUNT(*) FROM npcs WHERE alive = 1 AND disease IN ({marks})
            GROUP BY city_id HAVING COUNT(*) >= ?""", (*plagues(), at_least))}
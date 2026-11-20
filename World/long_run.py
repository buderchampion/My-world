"""
The long test run: fast-forwards a world for years and reports on it, year by
year, so slow drifts show before they matter -- the population shrinking or
swelling, food running out, gold piling up, one cause of death dwarfing the rest.

    python long_run.py                       a new world, 30 years
    python long_run.py --years 50 --seed 4242 --kingdoms 6
    python long_run.py --resume --years 10   carry on the same run for 10 more years

It runs on its own world file (LongRun/long_run.db), never your real world.db,
and writes its report to LongRun/report.txt as it goes (and the screen).

Fast-forward: whole days, not hours. The hourly round of needs (where people
go, what they eat at each meal) is skipped; instead each day a person's hunger
follows how well they ate that day (`fed`), so going without food still leads
to starvation as it would. Everything else -- work, wages, markets, harvests,
sickness, wars, raids, crime, festivals -- runs exactly as in the real thing.
A year takes several minutes on a big world.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "LongRun"
DB_FILE = OUT_DIR / "long_run.db"
REPORT = OUT_DIR / "report.txt"

HUNGER_PER_DAY = 40          # fast-forward hunger: fed 1.0 -> +10 a day, fed 0 -> -30 a day (starving within days)
HUNGER_EVEN_AT = 0.75


def setup(resume, seed, kingdoms):
    import Data.Database.database as D
    import Data.Simulation.logger as L
    OUT_DIR.mkdir(exist_ok=True)
    D.DB_FILE = DB_FILE
    L.LOG_DIR = OUT_DIR / "Logs"
    L.LOG_FILE = L.LOG_DIR / "simulation.log"
    if not resume and DB_FILE.exists():
        DB_FILE.unlink()
    if resume and DB_FILE.exists():
        D.init_database()
        conn = D.get_connection()
        return conn, conn.execute("SELECT id FROM worlds ORDER BY id DESC LIMIT 1").fetchone()[0]
    from Data.World.world_generator import generate_world
    world = generate_world(seed=seed, kingdoms=kingdoms)
    return D.get_connection(), world["id"]


def day_number(clock):
    return (clock.year - 1) * 360 + (clock.month - 1) * 30 + (clock.day - 1)


def advance_day(manager):
    """One whole day: the day's work, then the calendar turns (month, season and year as they come)."""
    from Data.Simulation.simulation_manager import save_clock
    conn = manager.conn
    manager.on_day()
    conn.execute(
        """UPDATE npcs SET needs_hunger = MAX(0, MIN(100, needs_hunger + ? * (COALESCE(fed, 1) - ?)))
           WHERE alive = 1""", (HUNGER_PER_DAY, HUNGER_EVEN_AT))
    c = manager.clock
    season = c.season
    c.day += 1
    month = year = False
    if c.day > 30:
        c.day, c.month, month = 1, c.month + 1, True
        if c.month > 12:
            c.month, c.year, year = 1, c.year + 1, True
    save_clock(conn, manager.world_id, c)
    conn.commit()
    if month:
        manager.on_month()
        if c.season != season:
            manager.on_season()
    if year:
        manager.on_year()


def snapshot(conn, world_id):
    """The state of the world now: who lives where, what is stored, what stands."""
    s = {}
    s["named"] = conn.execute("""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
                                 WHERE k.world_id = ? AND n.alive = 1""", (world_id,)).fetchone()[0]
    s["households"] = conn.execute("""SELECT COALESCE(SUM(c.households), 0) FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                                      WHERE k.world_id = ?""", (world_id,)).fetchone()[0]
    s["kingdoms"] = [dict(r) for r in conn.execute(
        """SELECT k.id, k.name, k.race, k.status, k.treasury, k.price_level,
                  (SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = k.id AND n.alive = 1) named,
                  (SELECT COALESCE(SUM(households), 0) FROM cities c WHERE c.kingdom_id = k.id) households,
                  (SELECT COUNT(*) FROM cities c WHERE c.kingdom_id = k.id) settlements,
                  (SELECT AVG(n.needs_happiness) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = k.id AND n.alive = 1) happy,
                  (SELECT AVG(n.fed) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = k.id AND n.alive = 1) fed
           FROM kingdoms k WHERE k.world_id = ? ORDER BY k.id""", (world_id,))]
    s["peoples"] = dict(conn.execute(
        """SELECT COALESCE(n.race, 'Human'), COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND n.alive = 1 GROUP BY 1 ORDER BY 2 DESC""", (world_id,)).fetchall())
    s["children"] = conn.execute("""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
                                    WHERE k.world_id = ? AND n.alive = 1 AND n.age < 16""", (world_id,)).fetchone()[0]
    s["laborers"] = conn.execute("""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
                                    WHERE k.world_id = ? AND n.alive = 1 AND n.profession = 'Laborer'""", (world_id,)).fetchone()[0]
    food = conn.execute("""SELECT COALESCE(SUM(r.amount), 0) FROM city_resources r JOIN cities c ON c.id = r.city_id
                           JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND r.resource_name = 'Food'""", (world_id,)).fetchone()[0]
    store = conn.execute("""SELECT COALESCE(SUM(c.household_store), 0) FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                            WHERE k.world_id = ?""", (world_id,)).fetchone()[0]
    s["food"], s["store"] = food, store
    s["gold_people"] = conn.execute("""SELECT COALESCE(SUM(n.gold), 0) FROM npcs n JOIN cities c ON c.id = n.city_id
                                       JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND n.alive = 1""", (world_id,)).fetchone()[0]
    s["gold_towns"] = conn.execute("""SELECT COALESCE(SUM(c.wealth), 0) FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
                                      WHERE k.world_id = ?""", (world_id,)).fetchone()[0]
    s["gold_crowns"] = sum(k["treasury"] or 0 for k in s["kingdoms"])
    s["prosperity"] = conn.execute("""SELECT AVG(c.prosperity), MIN(c.prosperity), MAX(c.prosperity) FROM cities c
                                      JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND c.prosperity IS NOT NULL""",
                                   (world_id,)).fetchone()
    s["max_id"] = conn.execute("SELECT COALESCE(MAX(id), 0) FROM npcs").fetchone()[0]
    s["grand"] = conn.execute("SELECT COUNT(*) FROM buildings WHERE category = 'Grand'").fetchone()[0]
    s["guilds"] = conn.execute("SELECT COUNT(*) FROM thieves_guilds WHERE world_id = ? AND status = 'Active'", (world_id,)).fetchone()[0]
    s["lairs"] = conn.execute("SELECT COUNT(*), COALESCE(SUM(count), 0) FROM monster_lairs WHERE world_id = ? AND status = 'Active'",
                              (world_id,)).fetchone()
    s["goods"] = dict(conn.execute("""SELECT r.resource_name, SUM(r.amount) FROM city_resources r JOIN cities c ON c.id = r.city_id
                                      JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND r.resource_name != 'Food'
                                      GROUP BY 1 ORDER BY 1""", (world_id,)).fetchall())
    s["dragons"] = conn.execute("""SELECT COALESCE(SUM(good = 0), 0), COALESCE(SUM(good = 1), 0), COALESCE(SUM(hoard), 0)
                                   FROM dragons WHERE world_id = ? AND status = 'Alive'""", (world_id,)).fetchone()
    s["discoveries"] = conn.execute("""SELECT COUNT(*) FROM kingdom_discoveries d JOIN kingdoms k ON k.id = d.kingdom_id
                                       WHERE k.world_id = ?""", (world_id,)).fetchone()[0]
    return s


DEATH = re.compile(r"died \((.*)\)\.$")


def year_events(conn, world_id, first_day, last_day, before_max_id):
    """What happened between these days: deaths by cause, births, the chronicle's stories, crimes, plagues."""
    e = {}
    deaths = Counter()
    for (text,) in conn.execute("""SELECT description FROM events WHERE type = 'Death' AND day BETWEEN ? AND ?""", (first_day, last_day)):
        m = DEATH.search(text or "")
        cause = m.group(1) if m else "?"
        if cause.startswith("slain by"):
            cause = "beasts on the road"
        deaths[cause] += 1
    e["deaths"] = deaths
    e["born"] = conn.execute("""SELECT COUNT(*) FROM npcs WHERE id > ? AND parent_a_id IS NOT NULL AND age <= 1""",
                             (before_max_id,)).fetchone()[0]
    e["new_named"] = conn.execute("SELECT COUNT(*) FROM npcs WHERE id > ?", (before_max_id,)).fetchone()[0]
    e["chronicle"] = Counter(dict(conn.execute(
        """SELECT ce.event_type, COUNT(*) FROM chronicle_entries ce JOIN kingdoms k ON k.id = ce.kingdom_id
           WHERE k.world_id = ? AND ce.id > ? GROUP BY ce.event_type""", (world_id, 0)).fetchall()))
    e["events"] = Counter(dict(conn.execute(
        "SELECT type, COUNT(*) FROM events WHERE day BETWEEN ? AND ? GROUP BY type", (first_day, last_day)).fetchall()))
    crimes = conn.execute("""SELECT COUNT(*), COALESCE(SUM(caught), 0), COALESCE(SUM(punishment = 'gallows'), 0)
                             FROM crimes WHERE world_id = ? AND kind = 'Theft' AND day BETWEEN ? AND ?""",
                          (world_id, first_day, last_day)).fetchone()
    e["thefts"], e["caught"], e["hanged"] = crimes
    e["smuggled"] = conn.execute("""SELECT COUNT(*), COALESCE(SUM(caught), 0) FROM crimes WHERE world_id = ? AND kind = 'Smuggling'
                                    AND day BETWEEN ? AND ?""", (world_id, first_day, last_day)).fetchone()
    e["plagues"] = conn.execute("""SELECT disease, COUNT(*), COALESCE(SUM(dead), 0) FROM outbreaks WHERE world_id = ?
                                   AND started_day BETWEEN ? AND ? GROUP BY disease""", (world_id, first_day, last_day)).fetchall()
    try:
        e["quests"] = conn.execute("""SELECT q.type, q.status, COUNT(*) FROM quests q JOIN cities c ON c.id = q.city_id
                                      JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND q.done_day BETWEEN ? AND ?
                                      GROUP BY 1, 2""", (world_id, first_day, last_day)).fetchall()
        e["adventurers"] = conn.execute("""SELECT COUNT(*), COALESCE(SUM(renown >= 10), 0) FROM npcs n JOIN cities c ON c.id = n.city_id
                                           JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND n.alive = 1
                                           AND n.profession = 'Adventurer'""", (world_id,)).fetchone()
        e["captives"] = conn.execute("""SELECT kind, status, COUNT(*) FROM captives WHERE world_id = ? AND taken_day BETWEEN ? AND ?
                                        GROUP BY 1, 2""", (world_id, first_day, last_day)).fetchall()
        e["era"] = conn.execute("SELECT name FROM world_eras WHERE world_id = ? AND ended_day IS NULL", (world_id,)).fetchone()
    except Exception:
        e["quests"], e["adventurers"], e["captives"], e["era"] = [], (0, 0), [], None
    return e


def write(lines, fh):
    text = "\n".join(lines)
    print(text)
    fh.write(text + "\n")
    fh.flush()


def report_year(year, s, prev, e, chronicle_before, seconds, fh):
    births, deaths = e["born"], sum(e["deaths"].values())
    named_change = s["named"] - prev["named"]
    hh_change = s["households"] - prev["households"]
    lines = [f"",
             f"=== Year {year} ({seconds / 60:.1f} min) ===",
             f"People   {s['named']:,} named ({named_change:+,}), {s['children']:,} children, {s['laborers']:,} Laborers; "
             f"{s['households']:,.0f} village households ({hh_change:+,.0f})",
             f"         born {births:,}, died {deaths:,}, new named from the countryside and elsewhere {e['new_named'] - births:,}",
             f"Deaths   " + (", ".join(f"{c} {n}" for c, n in e["deaths"].most_common()) or "none"),
             f"Peoples  " + ", ".join(f"{r} {n:,}" for r, n in s["peoples"].items()),
             f"Food     {s['food']:,.0f} in town stores ({s['food'] - prev['food']:+,.0f}), "
             f"{s['store']:,.0f} in village stores ({s['store'] - prev['store']:+,.0f})",
             f"Gold     people {s['gold_people']:,.0f}, town purses {s['gold_towns']:,.0f}, crowns {s['gold_crowns']:,.0f}",
             f"Goods    " + ", ".join(f"{g} {v:,.0f} ({v - prev.get('goods', {}).get(g, 0):+,.0f})" for g, v in s.get("goods", {}).items())]
    p = s["prosperity"]
    if p and p[0] is not None:
        lines.append(f"Towns    prosperity avg {p[0]:.0f} (from {p[1]:.0f} to {p[2]:.0f}); {s['grand']} grand buildings; "
                     f"{s['guilds']} thieves' guilds; thefts {e['thefts']}, caught {e['caught']}, hanged {e['hanged']}; "
                     f"smuggling runs {e['smuggled'][0]}, caught {e['smuggled'][1]}")
    if e["plagues"]:
        lines.append("Plagues  " + ", ".join(f"{d} in {n} kingdom(s) ({dead} dead)" for d, n, dead in e["plagues"]))
    ev = e["events"]
    notable = [(k, ev[k]) for k in ("Fire", "Flood", "Earthquake", "Locusts", "Monster Raid", "Beast Attack", "Raid", "Famine",
                                    "Famine Flight", "Turned Bandit", "Wedding", "Coronation", "Election", "Tournament", "Fair",
                                    "Grand Building", "Ennobled", "Edict", "Assassination", "Dragon Attack", "Dragon Slain",
                                    "Dragon Hunt", "Succession Crisis", "Discovery", "Quest", "Renown") if ev.get(k)]
    if notable:
        lines.append("Events   " + ", ".join(f"{k} {n}" for k, n in notable))
    new_chronicle = e["chronicle"] - chronicle_before
    if new_chronicle:
        lines.append("Chronicle " + ", ".join(f"{k} {n}" for k, n in new_chronicle.most_common()))
    lines.append(f"Monsters {s['lairs'][0]} lairs, {s['lairs'][1]:,} monsters; dragons {s['dragons'][0]} evil, {s['dragons'][1]} good, "
                 f"hoards {s['dragons'][2]:,.0f} gold; {s['discoveries']} discoveries known")
    if e.get("quests") or e.get("adventurers", (0, 0))[0]:
        done = {}
        for kind, status, n in e["quests"]:
            done.setdefault(kind, {})[status] = n
        lines.append(f"Quests   {e['adventurers'][0]} adventurers ({e['adventurers'][1]} champions); "
                     + ", ".join(f"{k} " + "/".join(f"{st.lower()} {n}" for st, n in v.items()) for k, v in sorted(done.items())))
        if e.get("captives"):
            lines.append("Captives " + ", ".join(f"{k} {st.lower()} {n}" for k, st, n in e["captives"]))
    if e.get("era"):
        lines.append(f"Era      {e['era'][0]}")
    lines.append("Kingdoms")
    for k in s["kingdoms"]:
        if k["status"] == "Fallen":
            lines.append(f"  {k['name']} ({k['race']}): fallen")
            continue
        lines.append(f"  {k['name']:<14} {k['race']:<10} {k['named']:>6,} named, {k['households']:>7,.0f} households, "
                     f"{k['settlements']:>3} places; happy {k['happy'] or 0:.0f}, fed {k['fed'] or 0:.2f}, "
                     f"prices x{k['price_level'] or 1:.2f}, treasury {k['treasury'] or 0:,.0f}")
    write(lines, fh)


def main():
    parser = argparse.ArgumentParser(description="Fast-forward a world for years and report on it.")
    parser.add_argument("--years", type=int, default=30)
    parser.add_argument("--seed", default=None)
    parser.add_argument("--kingdoms", type=int, default=None)
    parser.add_argument("--resume", action="store_true", help="carry on the last long run instead of starting a new world")
    args = parser.parse_args()

    from Data.Infrastructure.seeds import parse_seed
    seed = parse_seed(args.seed) if args.seed else None
    started = time.time()
    conn, world_id = setup(args.resume, seed, args.kingdoms)
    from Data.Simulation.simulation_manager import SimulationManager
    manager = SimulationManager(conn, world_id)

    with open(REPORT, "a" if args.resume else "w", encoding="utf-8") as fh:
        world_seed = conn.execute("SELECT seed FROM worlds WHERE id = ?", (world_id,)).fetchone()[0]
        write([f"Long run: world {world_id}, seed {world_seed}, from year {manager.clock.year}, {args.years} years "
               f"(world made in {time.time() - started:.0f}s)"], fh)
        prev = snapshot(conn, world_id)
        write([f"Start    {prev['named']:,} named, {prev['households']:,.0f} village households, "
               f"{len(prev['kingdoms'])} kingdoms: " + ", ".join(f"{k['name']} ({k['race']})" for k in prev["kingdoms"])], fh)
        for _ in range(args.years):
            t = time.time()
            year = manager.clock.year
            first_day = day_number(manager.clock)
            chronicle_before = Counter(dict(conn.execute(
                """SELECT ce.event_type, COUNT(*) FROM chronicle_entries ce JOIN kingdoms k ON k.id = ce.kingdom_id
                   WHERE k.world_id = ? GROUP BY ce.event_type""", (world_id,)).fetchall()))
            while manager.clock.year == year:
                advance_day(manager)
            s = snapshot(conn, world_id)
            e = year_events(conn, world_id, first_day, first_day + 359, prev["max_id"])
            report_year(year, s, prev, e, chronicle_before, time.time() - t, fh)
            prev = s
            alive = [k for k in s["kingdoms"] if k["status"] != "Fallen"]
            if not alive:
                write(["Every kingdom has fallen."], fh)
                break


if __name__ == "__main__":
    main()
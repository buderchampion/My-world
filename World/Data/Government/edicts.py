# Edicts. Every season -- and at once when the realm falls into crisis
# (Data.Government.health) -- the ruler reviews the realm and may issue one
# edict. During a regency the regent decides.
#
# Each edict gets a NEED from the kingdom's situation (0-1): famine and dear
# bread call for the granary, an export ban and lower taxes; unrest for a
# festival, lower taxes or martial law; an empty treasury for higher taxes;
# collapsing stability for martial law. In calm times a ruler is also tempted
# by their own nature: the greedy to squeeze, the generous to give, the bold
# to celebrate. That need is weighed by the ruler's PERSONALITY
# (Data.NPCS.personality), and the best-scoring edict is issued if it scores
# at least ACT_THRESHOLD. The same edict is not repeated within a year,
# except in a crisis.
#
#   lower taxes       tax rate -5% (not below 2%): families keep more of
#                     their wages; lords approve; happiness +5
#   raise taxes       tax rate +5% (not above 30%): more income; lords
#                     resent it; happiness -5
#   festival          happiness +15, stress -15; costs 1 gold a person and
#                     half a day's food a person from the capital
#   open the granary  up to half the capital's Food goes to the hungry
#   ban food exports  for a season, no Food is sold abroad; every other
#                     kingdom's favor -5
#   martial law       for a season, risings and civil war a quarter as likely;
#                     stress +10, happiness -10; costs 2 gold a soldier
#
# Every edict is logged; tax changes, the granary and martial law are
# chronicled.

EDICTS = ("lower taxes", "raise taxes", "festival", "open the granary", "ban food exports", "martial law", "public works")

ACT_THRESHOLD = 0.35
SEASON_DAYS = 90
REPEAT_AFTER_DAYS = 360
REPEAT_AFTER = {"public works": 90}     # building can go on season after season

TAX_STEP, TAX_MIN, TAX_MAX = 0.05, 0.02, 0.30
TAX_MOOD = 5
FESTIVAL_GOLD_PER_PERSON, FESTIVAL_FOOD_PER_PERSON, FESTIVAL_HAPPINESS, FESTIVAL_CALM = 1.0, 0.5, 15, 15
GRANARY_SHARE = 0.5
GRANARY_DAYS_FOR_HUNGRY = 30         # each hungry village is sent up to this many days of its households' food
EXPORT_BAN_FAVOR = -5
MARTIAL_LAW_GOLD_PER_SOLDIER, MARTIAL_LAW_STRESS, MARTIAL_LAW_UNHAPPINESS = 2.0, 10, 10
MARTIAL_LAW_FACTOR = 0.25            # risings and civil war this likely while it lasts
RESERVE_PER_PERSON = 5.0             # a treasury below this much a head feels empty
CALM_STABILITY = 70                  # the realm is calm enough for a ruler's own inclinations
TEMPTATION = 0.25                    # in calm times a ruler's nature alone can push toward an edict: a trait of about 50 or more acts on it

# how each trait (generosity G, mercy M, boldness B: -1..+1) leans the ruler toward an edict
LEANS = {
    "lower taxes":      {"generosity": 0.6},
    "raise taxes":      {"generosity": -0.8},
    "festival":         {"generosity": 0.4, "boldness": 0.4},
    "open the granary": {"generosity": 0.6},
    "ban food exports": {"boldness": -0.6},
    "martial law":      {"mercy": -0.6, "boldness": 0.3},
    "public works":     {"boldness": 0.4, "generosity": 0.3},     # builders: bold, and open-handed
}

CHRONICLED = {"public works": "Edict (public works)", "lower taxes": "Edict (lower taxes)", "raise taxes": "Edict (raise taxes)",
              "open the granary": "Edict (open the granary)", "martial law": "Edict (martial law)"}


def today(conn, world_id):
    clock = conn.execute("SELECT clock_year, clock_month, clock_day FROM worlds WHERE id = ?", (world_id,)).fetchone()
    return (clock["clock_year"] - 1) * 360 + (clock["clock_month"] - 1) * 30 + (clock["clock_day"] - 1)


def martial_law_active(kingdom, day):
    return (kingdom.get("martial_law_until") or -1) >= day


def exports_banned(kingdom, day):
    return (kingdom.get("export_ban_until") or -1) >= day


def lean(ruler, edict):
    total = sum(weight * (ruler.get(trait) or 0) / 100 for trait, weight in LEANS[edict].items())
    return max(-0.8, min(0.8, total))


def situation(conn, kingdom):
    """Everything a ruler weighs: the kingdom's health and its raw signs."""
    from Data.Buildings.upgrades import works_need
    from Data.Combat.combat import DEFENDER_PROFESSIONS
    from Data.Government.feudal import RESTLESS_BELOW

    people = conn.execute(
        "SELECT COUNT(*) n, AVG(n.fed) fed FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1",
        (kingdom["id"],)).fetchone()
    marks = ",".join("?" * len(DEFENDER_PROFESSIONS))
    soldiers = conn.execute(
        f"""SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE c.kingdom_id = ? AND n.alive = 1 AND n.profession IN ({marks})""", (kingdom["id"], *DEFENDER_PROFESSIONS)).fetchone()[0]
    villages = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN hungry_days > 0 THEN 1 ELSE 0 END) hungry FROM cities WHERE kingdom_id = ? AND households > 0",
        (kingdom["id"],)).fetchone()
    lords = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN p.loyalty < ? THEN 1 ELSE 0 END) restless FROM provinces p WHERE p.kingdom_id = ?",
        (RESTLESS_BELOW, kingdom["id"])).fetchone()
    capital_food = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM city_resources WHERE city_id = ? AND resource_name = 'Food'",
        (kingdom["capital_city_id"],)).fetchone()[0]
    return {
        "people": people["n"], "fed": people["fed"] if people["fed"] is not None else 1.0, "soldiers": soldiers,
        "hungry_share": (villages["hungry"] or 0) / villages["n"] if villages["n"] else 0.0,
        "restless_share": (lords["restless"] or 0) / lords["n"] if lords["n"] else 0.0,
        "capital_food": capital_food,
        "works_need": works_need(conn, kingdom),
        "unrest": kingdom["health_unrest"], "stability": kingdom["health_stability"], "loyalty": kingdom["health_loyalty"],
    }


def needs(kingdom, s, day):
    """How much the realm calls for each edict, 0-1 (0 where it cannot be done)."""
    reserve = RESERVE_PER_PERSON * max(1, s["people"])
    calm = s["stability"] >= CALM_STABILITY
    festival_gold = FESTIVAL_GOLD_PER_PERSON * s["people"]
    n = {}
    n["lower taxes"] = 0.0 if kingdom["tax_rate"] - TAX_STEP < TAX_MIN - 1e-9 else max(
        s["unrest"] / 100, max(0.0, (60 - s["loyalty"]) / 60),
        0.5 * s["hungry_share"],          # lower taxes help the hungry only indirectly; the granary feeds them
        TEMPTATION if calm and kingdom["treasury"] >= 2 * reserve else 0.0)
    n["raise taxes"] = 0.0 if kingdom["tax_rate"] + TAX_STEP > TAX_MAX + 1e-9 else max(
        max(0.0, 1 - kingdom["treasury"] / reserve), TEMPTATION if calm else 0.0)
    n["festival"] = 0.0 if kingdom["treasury"] < 2 * festival_gold else min(1.0,
        0.8 * s["unrest"] / 100 + (TEMPTATION if calm and kingdom["treasury"] >= 4 * festival_gold else 0.0))
    n["open the granary"] = 0.0 if s["capital_food"] < s["people"] else min(1.0, s["hungry_share"] + (1 - s["fed"]))
    n["ban food exports"] = 0.0 if exports_banned(kingdom, day) else min(1.0,
        max(0.0, (kingdom["price_level"] or 1) - 1) / 1.5 + 0.5 * s["hungry_share"])
    n["martial law"] = 0.0 if martial_law_active(kingdom, day) or kingdom["treasury"] < MARTIAL_LAW_GOLD_PER_SOLDIER * s["soldiers"] \
        else min(1.0, max(0.0, (50 - s["stability"]) / 50) + 0.5 * s["restless_share"])
    n["public works"] = min(1.0, s.get("works_need", 0.0))      # the share of towns bursting, and able to pay
    return n


def choose(conn, kingdom, ruler, day, crisis=False):
    """(edict, score, all scores) -- the edict the ruler would issue now, or None."""
    s = situation(conn, kingdom)
    scores = {}
    for edict, need in needs(kingdom, s, day).items():
        if not crisis:
            last = conn.execute("SELECT MAX(day) FROM kingdom_edicts WHERE kingdom_id = ? AND edict = ?",
                                (kingdom["id"], edict)).fetchone()[0]
            if last is not None and day - last < REPEAT_AFTER.get(edict, REPEAT_AFTER_DAYS):
                continue
        scores[edict] = round(need * (1 + lean(ruler, edict)), 3)
    best = max(scores, key=scores.get) if scores else None
    if best is None or scores[best] < ACT_THRESHOLD:
        return None, 0.0, scores
    return best, scores[best], scores


# ------------------------------------------------------------------ effects

def _mood(conn, kingdom_id, happiness=0, stress=0):
    conn.execute(
        """UPDATE npcs SET needs_happiness = MAX(0, MIN(100, needs_happiness + ?)), needs_stress = MAX(0, MIN(100, needs_stress + ?))
           WHERE alive = 1 AND city_id IN (SELECT id FROM cities WHERE kingdom_id = ?)""", (happiness, stress, kingdom_id))


def _add_food(conn, city_id, amount):
    conn.execute(
        """INSERT INTO city_resources (city_id, resource_name, amount) VALUES (?, 'Food', ?)
           ON CONFLICT(city_id, resource_name) DO UPDATE SET amount = city_resources.amount + excluded.amount""",
        (city_id, round(amount, 2)))


def apply_edict(conn, kingdom, edict, day):
    """Carries out an edict. Returns a short description of what it did."""

    from Data.Database.repository import spend_treasury, adjust_kingdom_favor

    kid = kingdom["id"]
    s = situation(conn, kingdom)

    if edict in ("lower taxes", "raise taxes"):
        step = -TAX_STEP if edict == "lower taxes" else TAX_STEP
        rate = round(min(TAX_MAX, max(TAX_MIN, kingdom["tax_rate"] + step)), 2)
        conn.execute("UPDATE kingdoms SET tax_rate = ? WHERE id = ?", (rate, kid))
        _mood(conn, kid, happiness=TAX_MOOD if step < 0 else -TAX_MOOD)
        return f"taxes now {rate:.0%}"

    if edict == "festival":
        spend_treasury(conn, kid, FESTIVAL_GOLD_PER_PERSON * s["people"])
        food = min(s["capital_food"], FESTIVAL_FOOD_PER_PERSON * s["people"])
        _add_food(conn, kingdom["capital_city_id"], -food)
        _mood(conn, kid, happiness=FESTIVAL_HAPPINESS, stress=-FESTIVAL_CALM)
        return f"{FESTIVAL_GOLD_PER_PERSON * s['people']:.0f} gold and {food:.0f} Food spent"

    if edict == "open the granary":
        from Data.World.countryside import FAMILY_SIZE
        from Data.Economy.economy import get_target_stock
        from Data.Database.repository import get_city
        wants = []
        for v in conn.execute("SELECT id, households FROM cities WHERE kingdom_id = ? AND households > 0 AND hungry_days > 0", (kid,)):
            wants.append(("store", v["id"], v["households"] * FAMILY_SIZE * GRANARY_DAYS_FOR_HUNGRY))
        for t in conn.execute("SELECT id FROM cities WHERE kingdom_id = ? AND type != 'Village' AND id != ?", (kid, kingdom["capital_city_id"])):
            city = get_city(conn, t["id"])
            short = get_target_stock("Food", city) - city["resources"].get("Food", 0)
            if short > 0:
                wants.append(("market", t["id"], short))
        total = sum(w for _, _, w in wants)
        given = min(GRANARY_SHARE * s["capital_food"], total)
        for kind, city_id, want in wants:
            share = given * want / total if total else 0
            if kind == "store":
                conn.execute("UPDATE cities SET household_store = household_store + ?, hungry_days = 0 WHERE id = ?", (round(share, 2), city_id))
            else:
                _add_food(conn, city_id, share)
        _add_food(conn, kingdom["capital_city_id"], -given)
        return f"{given:.0f} Food sent to {len(wants)} hungry places"

    if edict == "ban food exports":
        conn.execute("UPDATE kingdoms SET export_ban_until = ? WHERE id = ?", (day + SEASON_DAYS, kid))
        for other in conn.execute("SELECT id FROM kingdoms WHERE world_id = ? AND id != ?", (kingdom["world_id"], kid)):
            adjust_kingdom_favor(conn, other["id"], kid, EXPORT_BAN_FAVOR)
        return "no Food sold abroad this season"

    if edict == "public works":
        from Data.Buildings.upgrades import public_works
        done = public_works(conn, kingdom)
        loan = f", {done['borrowed']:.0f} gold lent by the crown" if done["borrowed"] else ""
        return f"{done['upgraded']} buildings enlarged and {done['built']} built in {done['towns']} towns{loan}"

    if edict == "martial law":
        spend_treasury(conn, kid, MARTIAL_LAW_GOLD_PER_SOLDIER * s["soldiers"])
        conn.execute("UPDATE kingdoms SET martial_law_until = ? WHERE id = ?", (day + SEASON_DAYS, kid))
        _mood(conn, kid, happiness=-MARTIAL_LAW_UNHAPPINESS, stress=MARTIAL_LAW_STRESS)
        return "soldiers in every street this season"

    raise ValueError(edict)


def review(conn, world_id, kingdom_ids=None, crisis=False):
    """
    The ruler (or regent) of each kingdom -- or of just `kingdom_ids` -- reviews
    the realm and may issue one edict. Returns [(kingdom name, edict, what it did)].
    """

    from Data.Database.repository import get_npc, create_event
    from Data.NPCS.personality import describe
    from Data.History.chronicle import write_entry, current_date

    day = today(conn, world_id)
    year, season = current_date(conn, world_id)
    issued = []

    for kingdom in conn.execute("SELECT * FROM kingdoms WHERE world_id = ? AND status != 'Fallen'", (world_id,)).fetchall():
        kingdom = dict(kingdom)
        if kingdom_ids is not None and kingdom["id"] not in kingdom_ids:
            continue
        decider = get_npc(conn, kingdom["regent_id"] or kingdom["ruler_id"]) if (kingdom["regent_id"] or kingdom["ruler_id"]) else None
        if not decider or not decider["alive"]:
            continue

        edict, score, _ = choose(conn, kingdom, decider, day, crisis)
        if edict is None:
            continue

        what = apply_edict(conn, kingdom, edict, day)
        conn.execute("INSERT INTO kingdom_edicts (kingdom_id, edict, day, ruler_id) VALUES (?, ?, ?, ?)",
                     (kingdom["id"], edict, day, decider["id"]))
        nature = describe(decider)
        title = decider["title"] or ("Regent" if kingdom["regent_id"] else "ruler")
        create_event(conn, "Edict", f"{title} {decider['name']} ({nature}) of {kingdom['name']} decreed: {edict} ({what}).",
                     kingdom_id=kingdom["id"])
        if edict in CHRONICLED:
            write_entry(conn, kingdom["id"], "Edict", CHRONICLED[edict], year, season,
                        {"kingdom": kingdom["name"], "ruler": decider["name"], "title": title,
                         "nature": nature if nature != "even-tempered" else "steady"}, seed_parts=(kingdom["id"], day))
        issued.append((kingdom["name"], edict, what))

    conn.commit()
    return issued
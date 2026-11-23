import json
import math
import random

# Deciding (Decision Engine piece 2c). One way for anyone to make a choice, which every later piece uses.
#
# decide(conn, npc, kind, options, about=None) scores each option by adding small pulls:
#
#   traits      {"boldness": 0.3}: a +100 bold person gets +0.3, a -100 cautious one -0.3
#   needs       {"money": -0.5}: weight x need, each need 0 (fine) to 1 (dire) -- see needs()
#   liking      weight x liking toward whoever the choice is about (-1 to 1)
#   trust       weight x trust toward them (-1 to 1, Data.NPCS.regard)
#   grievance   weight x grievance toward them (0 to 1: a full MAX_GRIEVANCE)
#   goals       {"avenge": 0.15}: a bonus if the option serves their goal (Data.NPCS.goals)
#   extra       [(reason, value)]: the situation, worked out by the caller ("no match for them in a fight", 0.6)
#   base        where the option starts
#
# then picks mostly in character: each option is weighted by e^(score / PICKINESS) -- a lead of 0.15
# makes an option about three times likelier, a lead of 0.5 about thirty times. A notable's decision is
# logged (decisions) with its three weightiest reasons; the log keeps LOG_YEARS of decisions.

PICKINESS = 0.15
LOG_YEARS = 10
REASONS = 3

NEEDS = ("hunger", "safety", "money", "home", "health")
SAFE_GOLD = 100            # gold enough that money is no worry
SAFETY = {"war": 0.4, "afraid": 0.6, "besieged": 1.0}
SICK = 0.3


def needs(conn, npc):
    """{need: 0 (fine) .. 1 (dire)} for this person now."""
    from Data.Government.edicts import today
    city = conn.execute("SELECT c.*, k.world_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE c.id = ?",
                        (npc["city_id"],)).fetchone()
    safety = 0.0
    if city:
        day = today(conn, city["world_id"])
        if (city["afraid_until"] or 0) > day:
            safety = max(safety, SAFETY["afraid"])
        if conn.execute("SELECT 1 FROM campaigns WHERE target_city_id = ? AND status = 'Besieging' LIMIT 1",
                        (city["id"],)).fetchone():
            safety = SAFETY["besieged"]
        if conn.execute("""SELECT 1 FROM campaigns WHERE (attacker_kingdom_id = ? OR defender_kingdom_id = ?)
                           AND status NOT IN ('Over', 'Returning') LIMIT 1""", (city["kingdom_id"], city["kingdom_id"])).fetchone():
            safety = max(safety, SAFETY["war"])
    fed = npc["fed"] if npc["fed"] is not None else 1.0
    health = npc["health"] if npc["health"] is not None else 100
    return {
        "hunger": max(0.0, min(1.0, 1 - fed)),
        "safety": safety,
        "money": max(0.0, 1 - (npc["gold"] or 0) / SAFE_GOLD),
        "home": 0.0 if npc["home_id"] else 1.0,
        "health": min(1.0, max(0.0, 1 - health / 100) + (SICK if npc["disease"] else 0)),
    }


NEED_WORDS = {"hunger": "hungry", "safety": "afraid for their safety", "money": "short of money",
              "home": "without a home", "health": "in poor health"}


def _trait_word(trait, value):
    from Data.NPCS.personality import WORDS
    return WORDS[trait][0 if value >= 0 else 1]


def _name_of(conn, about):
    kind, target = about
    if kind == "npc":
        row = conn.execute("SELECT name FROM npcs WHERE id = ?", (target,)).fetchone()
    elif kind == "kingdom":
        row = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (target,)).fetchone()
    else:
        from Data.NPCS.notables import house_name
        return house_name(conn, target)
    return row["name"] if row else "someone"


def pulls(conn, npc, option, about=None, need=None, regard=None):
    """[(reason, value)] -- every pull on this option for this person."""
    out = []
    if option.get("base"):
        out.append(("", option["base"]))
    for trait, weight in (option.get("traits") or {}).items():
        value = (npc[trait] or 0) / 100 * weight
        if value:
            out.append((_trait_word(trait, npc[trait] or 0), value))
    if option.get("needs"):
        need = need if need is not None else needs(conn, npc)
        for key, weight in option["needs"].items():
            value = need[key] * weight
            if value:
                out.append((NEED_WORDS[key], value))
    if about and any(option.get(k) for k in ("liking", "trust", "grievance")):
        regard = regard if regard is not None else regard_of(conn, npc["id"], about)
        name = _name_of(conn, about)
        if option.get("liking") and regard["liking"]:
            out.append((f"{'fond of' if regard['liking'] > 0 else 'dislikes'} {name}", option["liking"] * regard["liking"]))
        if option.get("trust") and regard["trust"]:
            out.append((f"{'trusts' if regard['trust'] > 0 else 'distrusts'} {name}", option["trust"] * regard["trust"]))
        if option.get("grievance") and regard["grievance"]:
            out.append((f"grievance {regard['grievance'] * 200:.0f} against {name}", option["grievance"] * regard["grievance"]))
    goal = npc["goal"] if "goal" in npc.keys() else None
    if goal and goal in (option.get("goals") or {}):
        from Data.NPCS.goals import GOALS
        out.append((f"goal: {GOALS[goal]}", option["goals"][goal]))
    out.extend(option.get("extra") or [])
    return out


def regard_of(conn, npc_id, about):
    """{"liking", "trust", "grievance"} toward whoever a choice is about, each scaled to -1..1 (grievance 0..1)."""
    from Data.NPCS.regard import trust, grievance
    from Data.Combat.nemesis import MAX_GRIEVANCE
    kind, target = about
    liking = 0.0
    if kind == "npc":
        a, b = sorted((npc_id, target))
        row = conn.execute("SELECT reputation FROM relationships WHERE npc_a_id = ? AND npc_b_id = ?", (a, b)).fetchone()
        liking = (row["reputation"] / 100) if row else 0.0
    return {"liking": liking, "trust": trust(conn, npc_id, kind, target) / 100,
            "grievance": min(1.0, grievance(conn, npc_id, kind, target) / MAX_GRIEVANCE)}


def pick(scored):
    """{name: score} -> the name picked, mostly in character."""
    names = list(scored)
    top = max(scored.values())
    return random.choices(names, weights=[math.exp((scored[n] - top) / PICKINESS) for n in names])[0]


def top_reasons(contributions, n=REASONS):
    return sorted(((r, v) for r, v in contributions if r), key=lambda rv: -abs(rv[1]))[:n]


def decide(conn, npc, kind, options, about=None, log=None, words=None):
    """`npc` (a row) chooses among `options` ({name: option}, see above). Returns (name, reasons). A notable's
    choice is logged unless log=False; `words` ({name: "Hired a blade"}) is how the log tells each choice."""
    need = needs(conn, npc) if any(o.get("needs") for o in options.values()) else None
    regard = regard_of(conn, npc["id"], about) if about and any(
        o.get(k) for o in options.values() for k in ("liking", "trust", "grievance")) else None
    contributions = {name: pulls(conn, npc, option, about, need, regard) for name, option in options.items()}
    scored = {name: sum(v for _, v in parts) for name, parts in contributions.items()}
    choice = pick(scored)
    reasons = top_reasons(contributions[choice])
    if log is not False:
        record(conn, npc, kind, (words or {}).get(choice, choice), reasons, about, len(options), log)
    return choice, reasons


def record(conn, npc, kind, choice, reasons, about=None, count=0, force=None):
    """Log a decision -- a notable's only, unless force is True."""
    from Data.NPCS.notables import is_notable
    if force is not True and not is_notable(conn, npc["id"]):
        return
    from Data.Government.edicts import today
    world = conn.execute("""SELECT k.world_id FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id WHERE c.id = ?""",
                         (npc["city_id"],)).fetchone()
    if not world:
        return
    about_text = _name_of(conn, about) if about else None
    conn.execute("""INSERT INTO decisions (world_id, npc_id, day, kind, about, choice, reasons, options)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                 (world[0], npc["id"], today(conn, world[0]), kind, about_text, choice,
                  json.dumps([[r, round(v, 2)] for r, v in reasons]), count))


def describe(reasons):
    """'deceitful (+0.40), cautious (+0.28), no match for them in a fight (+0.25)'"""
    return ", ".join(f"{r} ({v:+.2f})" for r, v in reasons)


def prune(conn, world_id):
    """Yearly: forget decisions older than LOG_YEARS. Returns how many were dropped."""
    from Data.Government.edicts import today
    return conn.execute("DELETE FROM decisions WHERE world_id = ? AND day < ?",
                        (world_id, today(conn, world_id) - LOG_YEARS * 360)).rowcount
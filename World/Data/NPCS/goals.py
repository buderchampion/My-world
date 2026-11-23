# Goals (Decision Engine piece 2b). Every notable (Data.NPCS.notables) holds one main goal at a time:
#
#   rise     Rise in rank             the ambitious without a title             achieved: a title
#   throne   Take the throne          the very ambitious lords and later heirs  achieved: a crown
#   keep     Keep what I have         content or cautious rulers and lords      lifelong
#   glory    Win glory                the bold and ambitious with a sword       achieved: renown GLORY_RENOWN
#   rich     Grow rich                the greedy and ambitious                  achieved: RICH_GOLD gold
#   master   Master my craft or arms  the diligent                              achieved: a skill at MASTER_LEVEL
#   avenge   Avenge                   the vengeful with a grievance of GRUDGE+  achieved: the target dead or ruined,
#                                                                                         or the grievance gone
#   justice  See justice done         the honourable and merciful, aggrieved    achieved: the wrongdoer jailed or dead
#   spouse   Find a spouse            the unmarried, affectionate, sociable     achieved: married
#   house    Secure my house          the married without a grown heir          achieved: a child of theirs grown
#   faith    Serve the gods           the pious                                 lifelong
#
# Each goal a person may hold is scored by how well it fits their traits and their situation, and the best
# fit usually wins, but not always (Data.NPCS.decide.PICKINESS); each choice is logged with its reasons. Goals are sticky: kept until achieved, made impossible, or a
# big moment strikes (MOMENTS: kin slain, betrayed, a title or home lost), when the person chooses again.
# A notable without a goal chooses at the next season's review. When a notable dies, a blood feud (avenge
# against a house or kingdom) passes to their eldest grown child who is not forgiving, with half the
# grievance; a claim to the throne passes to their eldest grown child who is ambitious. Other goals die
# with them. In 2b goals only exist (set, kept, achieved, inherited, shown by the why tool); the later
# pieces of the Decision Engine give them actions. Older worlds have no goals.

GOALS = {
    "rise": "Rise in rank", "throne": "Take the throne", "keep": "Keep what I have", "glory": "Win glory",
    "rich": "Grow rich", "master": "Master my craft or arms", "avenge": "Avenge", "justice": "See justice done",
    "spouse": "Find a spouse", "house": "Secure my house", "faith": "Serve the gods",
}
LIFELONG = ("keep", "faith")
INHERITED = ("avenge", "throne")
MOMENTS = ("kin_slain", "betrayed", "loss")

THRONE_AMBITION = 50
FAITH_PIETY = 40
GRUDGE = 60
GLORY_RENOWN = 30
RICH_GOLD = 2000
GROWN = 16
FLOOR = 0.05             # every goal a person may hold has at least this much pull
SWORDS = ("Knight", "Captain", "Soldier", "Adventurer", "Guard")


def _up(value):
    return max(0, value or 0) / 100


def _down(value):
    return max(0, -(value or 0)) / 100


def _worst_grievance(conn, npc_id):
    """(kind, target id, amount) of this person's heaviest open grievance -- against a person, a house or a kingdom."""
    best = (None, None, 0.0)
    row = conn.execute("""SELECT g.target_id, g.amount FROM grievances g JOIN npcs t ON t.id = g.target_id
                          WHERE g.holder_id = ? AND g.settled = 0 AND t.alive = 1 ORDER BY g.amount DESC LIMIT 1""",
                       (npc_id,)).fetchone()
    if row:
        best = ("npc", row["target_id"], row["amount"])
    row = conn.execute("""SELECT target_kind, target_id, grievance FROM regard WHERE holder_id = ? AND grievance > 0
                          ORDER BY grievance DESC LIMIT 1""", (npc_id,)).fetchone()
    if row and row["grievance"] > best[2]:
        best = (row["target_kind"], row["target_id"], row["grievance"])
    return best


def _best_skill(conn, npc_id):
    row = conn.execute("SELECT MAX(level) FROM npc_skills WHERE npc_id = ?", (npc_id,)).fetchone()
    return (row[0] or 0) if row else 0


def _heir_apparent(conn, monarch_id):
    from Data.Government.succession import choose_heir
    heir, _ = choose_heir(conn, monarch_id, for_crown=True)
    return heir["id"] if heir else None


def _monarch_parent(conn, npc):
    from Data.NPCS.notables import MONARCH_TITLES
    parents = [p for p in (npc["parent_a_id"], npc["parent_b_id"]) if p]
    if not parents:
        return None
    row = conn.execute(f"""SELECT id FROM npcs WHERE id IN ({','.join('?' * len(parents))}) AND alive = 1
                           AND title IN ({','.join('?' * len(MONARCH_TITLES))})""", (*parents, *MONARCH_TITLES)).fetchone()
    return row["id"] if row else None


def _grown_child(conn, npc_id):
    return conn.execute("""SELECT 1 FROM npcs WHERE (parent_a_id = ? OR parent_b_id = ?) AND alive = 1 AND age >= ?""",
                        (npc_id, npc_id, GROWN)).fetchone() is not None


def _may_claim(conn, npc):
    """Could this person press a claim to the throne: a lord, or a monarch's child who is not first in line?"""
    from Data.NPCS.notables import LORD_TITLES
    if npc["title"] in LORD_TITLES:
        return True
    monarch = _monarch_parent(conn, npc)
    return monarch is not None and _heir_apparent(conn, monarch) != npc["id"]


def _trait(npc, trait, low=False, scale=1.0):
    """(word, pull): how far one trait pulls toward a goal -- its high end, or with low=True its low end."""
    from Data.NPCS.personality import WORDS
    value = (_down if low else _up)(npc[trait]) * scale
    return (WORDS[trait][1 if low else 0], value)


def reasons_for(conn, npc):
    """{goal: ([(reason, pull)], target_kind, target_id)} for every goal this person may hold now."""
    from Data.NPCS.notables import NOBLE_TITLES, MONARCH_TITLES
    from Data.NPCS.personality import MASTER_LEVEL
    out = {}
    titled = npc["title"] is not None
    sword = npc["profession"] in SWORDS
    if not titled:
        born = _monarch_parent(conn, npc)
        out["rise"] = ([_trait(npc, "ambition")] + ([("a sword-bearer" if sword else "born to a throne", 0.2)] if sword or born else []),
                       None, None)
    if (npc["ambition"] or 0) >= THRONE_AMBITION and npc["title"] not in MONARCH_TITLES and _may_claim(conn, npc):
        out["throne"] = ([_trait(npc, "ambition"), _trait(npc, "honour", low=True, scale=0.5)], None, None)
    if npc["title"] in NOBLE_TITLES:
        out["keep"] = ([_trait(npc, "ambition", low=True), _trait(npc, "boldness", low=True, scale=0.5),
                        (f"holds the title of {npc['title']}", 0.3)], None, None)
    if (npc["renown"] or 0) < GLORY_RENOWN:
        out["glory"] = ([_trait(npc, "boldness", scale=0.5), _trait(npc, "ambition", scale=0.5)]
                        + ([("a sword-bearer", 0.2)] if sword else []), None, None)
    if (npc["gold"] or 0) < RICH_GOLD:
        out["rich"] = ([_trait(npc, "generosity", low=True, scale=0.5), _trait(npc, "ambition", scale=0.5)], None, None)
    if _best_skill(conn, npc["id"]) < MASTER_LEVEL:
        out["master"] = ([_trait(npc, "diligence")], None, None)
    kind, target, amount = _worst_grievance(conn, npc["id"])
    if amount >= GRUDGE:
        from Data.NPCS.decide import _name_of
        wrong = (f"grievance {amount:.0f} against {_name_of(conn, (kind, target))}", amount / 200)
        out["avenge"] = ([_trait(npc, "vengefulness"), wrong], kind, target)
        if kind == "npc":
            out["justice"] = ([_trait(npc, "honour", scale=0.5), _trait(npc, "mercy", scale=0.5), (wrong[0], amount / 400)],
                              kind, target)
    if npc["spouse_id"] is None and (npc["age"] or 0) >= GROWN:
        out["spouse"] = ([_trait(npc, "affection", scale=0.5), _trait(npc, "sociability", scale=0.5), ("unmarried", 0.1)], None, None)
    if npc["spouse_id"] is not None and not _grown_child(conn, npc["id"]):
        out["house"] = ([_trait(npc, "affection")] + ([("a house to carry on", 0.2)] if titled else []), None, None)
    if (npc["piety"] or 0) >= FAITH_PIETY:
        out["faith"] = ([_trait(npc, "piety")], None, None)
    return {goal: ([p for p in parts if p[1]] + [("", FLOOR)], kind, target) for goal, (parts, kind, target) in out.items()}


def options(conn, npc):
    """{goal: (score, target_kind, target_id)} for every goal this person may hold now."""
    return {goal: (sum(v for _, v in parts), kind, target) for goal, (parts, kind, target) in reasons_for(conn, npc).items()}


def choose(conn, npc, log=True):
    """The goal this person picks now: (goal, target_kind, target_id) -- the best fit usually, not always. The
    choice is logged with its reasons (Data.NPCS.decide)."""
    from Data.NPCS.decide import pick, record, top_reasons
    choices = reasons_for(conn, npc)
    if not choices:
        return ("rich", None, None)
    goal = pick({g: sum(v for _, v in parts) for g, (parts, _, _) in choices.items()})
    parts, kind, target = choices[goal]
    if log:
        record(conn, npc, "goal", GOALS[goal], top_reasons(parts), (kind, target) if target is not None else None, len(choices))
    return (goal, kind, target)


def _today(conn, npc_id):
    from Data.Government.edicts import today
    row = conn.execute("""SELECT k.world_id FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
                          WHERE n.id = ?""", (npc_id,)).fetchone()
    return today(conn, row[0]) if row else 0


def set_goal(conn, npc_id, goal, kind=None, target=None, day=None):
    conn.execute("UPDATE npcs SET goal = ?, goal_kind = ?, goal_target = ?, goal_since = ? WHERE id = ?",
                 (goal, kind, target, _today(conn, npc_id) if day is None else day, npc_id))


def achieved(conn, npc):
    """True if this person's goal is done."""
    from Data.NPCS.notables import MONARCH_TITLES, NOBLE_TITLES
    from Data.NPCS.personality import MASTER_LEVEL
    goal = npc["goal"]
    if goal == "rise":
        return npc["title"] is not None
    if goal == "throne":
        return npc["title"] in MONARCH_TITLES
    if goal == "glory":
        return (npc["renown"] or 0) >= GLORY_RENOWN
    if goal == "rich":
        return (npc["gold"] or 0) >= RICH_GOLD
    if goal == "master":
        return _best_skill(conn, npc["id"]) >= MASTER_LEVEL
    if goal == "spouse":
        return npc["spouse_id"] is not None
    if goal == "house":
        return _grown_child(conn, npc["id"])
    if goal in ("avenge", "justice"):
        from Data.NPCS.regard import grievance
        kind, target = npc["goal_kind"], npc["goal_target"]
        if grievance(conn, npc["id"], kind, target) <= 0:
            return True                                   # the grievance faded away, or was settled
        if kind == "npc":
            row = conn.execute("SELECT alive, jailed_until FROM npcs WHERE id = ?", (target,)).fetchone()
            return not row or not row["alive"] or (goal == "justice" and row["jailed_until"] is not None)
        if kind == "house":                               # ruined: no one of the house holds a crown or a county
            return conn.execute(f"""SELECT 1 FROM npcs WHERE family_id = ? AND alive = 1
                                    AND title IN ({','.join('?' * len(NOBLE_TITLES))}) LIMIT 1""",
                                (target, *NOBLE_TITLES)).fetchone() is None
        if kind == "kingdom":
            return conn.execute("SELECT 1 FROM cities WHERE kingdom_id = ? LIMIT 1", (target,)).fetchone() is None
    return False


def impossible(conn, npc):
    """True if this person's goal can no longer be reached."""
    if npc["goal"] == "throne":
        return not _may_claim(conn, npc) and npc["title"] is None
    if npc["goal"] == "keep":
        from Data.NPCS.notables import NOBLE_TITLES
        return npc["title"] not in NOBLE_TITLES
    return False


def goals_season(conn, world_id):
    """Each season, in a world with the Decision Engine: notables without a goal choose one; a goal achieved
    or made impossible gives way to a new one; those who are no longer notables let their goal go.
    Returns {"chosen", "achieved", "abandoned"}."""
    from Data.NPCS.personality import has_engine
    from Data.NPCS.notables import notable_ids
    counts = {"chosen": 0, "achieved": 0, "abandoned": 0}
    if not has_engine(conn, world_id):
        return counts
    notables = set(notable_ids(conn, world_id))
    conn.execute(f"""UPDATE npcs SET goal = NULL, goal_kind = NULL, goal_target = NULL, goal_since = NULL
                     WHERE goal IS NOT NULL AND (alive = 0 OR id NOT IN ({','.join('?' * len(notables)) or 'NULL'}))""",
                 tuple(notables))
    for npc_id in notables:
        npc = conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()
        if npc["goal"]:
            if npc["goal"] not in LIFELONG and achieved(conn, npc):
                counts["achieved"] += 1
            elif impossible(conn, npc):
                counts["abandoned"] += 1
            else:
                continue                                   # sticky: they hold to it
        set_goal(conn, npc_id, *choose(conn, npc))
        counts["chosen"] += 1
    return counts


def moment(conn, npc_ids, event):
    """A big moment (MOMENTS) makes a notable weigh their goal again -- they may well keep it."""
    if event not in MOMENTS:
        return 0
    from Data.NPCS.notables import is_notable
    changed = 0
    for npc_id in npc_ids:
        if not is_notable(conn, npc_id):
            continue
        npc = conn.execute("SELECT * FROM npcs WHERE id = ?", (npc_id,)).fetchone()
        set_goal(conn, npc_id, *choose(conn, npc))
        changed += 1
    return changed


def inherit(conn, dead_id):
    """A notable has died: a blood feud passes to their eldest grown child who is not forgiving (with half the
    grievance), a claim to the throne to their eldest grown child who is ambitious."""
    dead = conn.execute("SELECT * FROM npcs WHERE id = ?", (dead_id,)).fetchone()
    if not dead or dead["goal"] not in INHERITED:
        return None
    if dead["goal"] == "avenge" and dead["goal_kind"] not in ("house", "kingdom"):
        return None                                        # a grudge against one person dies with the one who held it
    for child in conn.execute("""SELECT * FROM npcs WHERE (parent_a_id = ? OR parent_b_id = ?) AND alive = 1 AND age >= ?
                                 ORDER BY age DESC, id""", (dead_id, dead_id, GROWN)).fetchall():
        if child["loyalty"] is None:
            continue
        if dead["goal"] == "avenge" and (child["vengefulness"] or 0) >= 0:
            from Data.NPCS.regard import grievance, hold
            amount = grievance(conn, dead_id, dead["goal_kind"], dead["goal_target"])
            day = _today(conn, child["id"])
            if amount > 0:
                hold(conn, child["id"], dead["goal_kind"], dead["goal_target"], amount / 2,
                     f"the feud of their parent, {dead['name']}", day)
            set_goal(conn, child["id"], "avenge", dead["goal_kind"], dead["goal_target"], day)
            return child["id"]
        if dead["goal"] == "throne" and (child["ambition"] or 0) >= 0:
            set_goal(conn, child["id"], "throne")
            return child["id"]
    return None


def describe(conn, npc):
    """'Avenge House Reed (since year 3)' -- for the why tool."""
    if not npc["goal"]:
        return "none"
    text = GOALS.get(npc["goal"], npc["goal"])
    if npc["goal_target"] is not None:
        if npc["goal_kind"] == "npc":
            row = conn.execute("SELECT name FROM npcs WHERE id = ?", (npc["goal_target"],)).fetchone()
            text += f" on {row['name']}" if row else ""
        elif npc["goal_kind"] == "house":
            from Data.NPCS.notables import house_name
            text += f" on {house_name(conn, npc['goal_target'])}"
        elif npc["goal_kind"] == "kingdom":
            row = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (npc["goal_target"],)).fetchone()
            text += f" on {row['name']}" if row else ""
    if npc["goal_since"] is not None:
        text += f" (since year {npc['goal_since'] // 360 + 1})"
    return text
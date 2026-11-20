import random

# Personality. Every NPC has twelve traits, each from -100 to +100:
#
#   generosity    +100 generous ........ -100 greedy
#   mercy         +100 merciful ........ -100 harsh
#   boldness      +100 bold ............ -100 cautious
#   ambition      +100 ambitious ....... -100 content
#   diligence     +100 diligent ........ -100 idle
#   curiosity     +100 curious ......... -100 traditional
#   vengefulness  +100 vengeful ........ -100 forgiving
#   sociability   +100 sociable ........ -100 solitary
#   affection     +100 affectionate .... -100 cold
#   honour        +100 honourable ...... -100 deceitful
#   piety         +100 pious ........... -100 doubting
#   loyalty       +100 loyal ........... -100 fickle
#
# Most people sit near the middle; a few are extreme. Each people leans a
# little (Races.json "leanings": dwarves a little more loyal, elves a little
# more curious) -- every individual still varies widely. A child takes after
# both parents -- their average, plus their own variation -- so a greedy line
# tends to stay greedy without being locked into it.
#
# Traits are hidden: they work behind the scenes and are never shown in words
# (describe() is for the tools that explain a person's choices, not the world).
# They are the heart of the Decision Engine. Rulers already weigh them when
# they issue edicts (Data.Government.edicts).
#
# The first three came first; a world made before the other nine (people
# version below PEOPLE_VERSION) keeps running the old way -- start a new world
# to get the Decision Engine.

TRAITS = ("generosity", "mercy", "boldness", "ambition", "diligence", "curiosity",
          "vengefulness", "sociability", "affection", "honour", "piety", "loyalty")
FIRST_TRAITS = TRAITS[:3]
PEOPLE_VERSION = 2        # 1: the twelve traits; 2: and ability scores (Data.NPCS.abilities)

SPREAD = 40               # how far people usually stray from their people's middle
CHILD_VARIATION = 25      # how far a child strays from their parents' average
STRONG = 40               # a trait this far from 0 is part of how the tools describe someone

WORDS = {
    "generosity": ("generous", "greedy"),
    "mercy": ("merciful", "harsh"),
    "boldness": ("bold", "cautious"),
    "ambition": ("ambitious", "content"),
    "diligence": ("diligent", "idle"),
    "curiosity": ("curious", "traditional"),
    "vengefulness": ("vengeful", "forgiving"),
    "sociability": ("sociable", "solitary"),
    "affection": ("affectionate", "cold"),
    "honour": ("honourable", "deceitful"),
    "piety": ("pious", "doubting"),
    "loyalty": ("loyal", "fickle"),
}


# Callings: a person's traits make a fitting trade or class a little likelier at generation (and when they
# come of age or are recruited) -- at most CALLING_PULL more for a perfect fit (+100 in every trait listed;
# -1 means the low end: the deceitful to thieving). Everyone else is as likely as before.
CALLING_PULL = 0.5
TRADE_TILT = {
    "Priest": {"piety": 1}, "Healer": {"mercy": 1},
    "Scholar": {"curiosity": 1}, "Mage": {"curiosity": 1}, "Alchemist": {"curiosity": 1},
    "Soldier": {"boldness": 1}, "Captain": {"boldness": 1}, "Knight": {"boldness": 1, "loyalty": 1}, "Guard": {"loyalty": 1},
    "Thief": {"honour": -1}, "Smuggler": {"honour": -1}, "Bandit": {"honour": -1},
    "Merchant": {"ambition": 1, "sociability": 1}, "innkeeper": {"sociability": 1},
    "Hunter": {"sociability": -1}, "Fisherman": {"sociability": -1},
    "Blacksmith": {"diligence": 1}, "Armorer": {"diligence": 1}, "Miner": {"diligence": 1}, "Farmer": {"diligence": 1},
}
CLASS_TILT = {
    "Cleric": {"piety": 1}, "Paladin": {"piety": 1, "honour": 1}, "Druid": {"piety": 1},
    "Wizard": {"curiosity": 1}, "Barbarian": {"boldness": 1}, "Rogue": {"honour": -1},
    "Warlock": {"ambition": 1}, "Bard": {"sociability": 1}, "Ranger": {"sociability": -1}, "Monk": {"diligence": 1},
}


def calling(traits, name, kind="trade"):
    """How much likelier this person is to take up this trade (kind 'trade') or class ('class'): 1.0 (no pull)
    to 1 + CALLING_PULL, by how well their traits fit it. 1.0 for anyone without traits."""
    tilt = (TRADE_TILT if kind == "trade" else CLASS_TILT).get(name)
    if not tilt or not traits:
        return 1.0
    fit = sum(max(0, sign * (traits.get(t) or 0)) for t, sign in tilt.items()) / (100 * len(tilt))
    return 1.0 + CALLING_PULL * fit


def _clamp(value):
    return max(-100, min(100, int(round(value))))


# Life nudges. What happens to a person moves their traits a little (step 1d). Each kind of event nudges a
# person at most once a year (a hard year of war hardens a soldier a little; years of war make a veteran),
# except kin slain, which always counts; becoming a master counts once in a life. Childhood shapes people
# most: under CHILDHOOD every nudge counts CHILD_NUDGE times. No trait ever drifts more than DRIFT_CAP from
# where it was at birth. Each person's record (npcs.trait_drift) keeps how far each trait has moved and the
# year each kind last nudged them. Nudges are silent: the tools that explain a person's choices show them.
DRIFT_CAP = 30
CHILDHOOD = 16
CHILD_NUDGE = 2
NUDGES = {
    "battle":    {"mercy": -2, "boldness": 2},              # fought in a battle
    "glory":     {"ambition": 3, "boldness": 3},            # won a duel, a tournament, a great quest
    "beaten":    {"boldness": -3, "ambition": -2},          # beaten, captured or humiliated
    "kin_slain": {"vengefulness": 10, "mercy": -5},         # spouse, parent, child or sibling slain
    "grief":     {"piety": "faith", "sociability": -3},     # spouse or child lost to sickness, age or mishap
    "hardship":  None,                                      # survived plague or famine (see _shifts)
    "helped":    {"generosity": 3, "sociability": 2},       # freed, found, cured
    "betrayed":  {"loyalty": -5, "sociability": -3},
    "master":    {"diligence": 2, "ambition": 2},           # a trade mastered (skill level MASTER_LEVEL)
    "loss":      {"ambition": -3},                          # lost a title, a home or a fortune
}
EVERY_TIME = {"kin_slain"}
ONCE_A_LIFE = {"master"}
FAITH = 5                  # grief: the pious grow more pious, doubters more doubtful
PIOUS_HARDSHIP = 5         # hardship: the pious (piety STRONG or more) turn to the gods...
HARD_HEARTED = -3          # ...everyone else holds tighter to what they have (generosity)


def _shifts(event, row):
    """{trait: change} this event makes in this person."""
    if event == "hardship":
        return {"piety": PIOUS_HARDSHIP} if (row["piety"] or 0) >= STRONG else {"generosity": HARD_HEARTED}
    shifts = {}
    for trait, d in NUDGES[event].items():
        if d == "faith":
            piety = row["piety"] or 0
            d = FAITH if piety > 0 else -FAITH if piety < 0 else 0
        if d:
            shifts[trait] = d
    return shifts


def drift_of(row):
    """A person's nudge record: {"traits": {trait: how far it has moved}, "seen": {event: year last nudged}}."""
    import json
    try:
        record = json.loads(row["trait_drift"] or "{}")
    except (KeyError, IndexError, TypeError, ValueError):
        record = {}
    record.setdefault("traits", {})
    record.setdefault("seen", {})
    return record


def nudge(conn, npc_ids, event):
    """Life nudges `event` (NUDGES) the traits of these people -- the living with the twelve traits (older
    worlds: no one). Returns how many were nudged."""
    import json
    from Data.Government.edicts import today
    ids = list(dict.fromkeys(i for i in npc_ids if i and i > 0))
    nudged, year = 0, None
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        rows = conn.execute(
            f"""SELECT n.id, n.age, n.trait_drift, {', '.join('n.' + t for t in TRAITS)}, k.world_id
                FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
                WHERE n.id IN ({','.join('?' * len(part))}) AND n.alive = 1 AND n.loyalty IS NOT NULL""", part).fetchall()
        for row in rows:
            if year is None:
                year = today(conn, row["world_id"]) // 360
            record = drift_of(row)
            seen = record["seen"]
            if event in ONCE_A_LIFE and event in seen:
                continue
            if event not in EVERY_TIME and seen.get(event) == year:
                continue
            seen[event] = year
            scale = CHILD_NUDGE if (row["age"] or 0) < CHILDHOOD else 1
            sets = {}
            for trait, d in _shifts(event, row).items():
                moved = record["traits"].get(trait, 0)
                wanted = max(-DRIFT_CAP, min(DRIFT_CAP, moved + d * scale))       # never past DRIFT_CAP from birth
                value = _clamp(row[trait] + wanted - moved)
                if value != row[trait]:
                    record["traits"][trait] = moved + value - row[trait]
                    sets[trait] = value
            columns = "".join(f"{t} = ?, " for t in sets)
            conn.execute(f"UPDATE npcs SET {columns}trait_drift = ? WHERE id = ?", (*sets.values(), json.dumps(record), row["id"]))
            nudged += 1
    return nudged


def kin_of(conn, npc_id, close=False):
    """Living kin of a person: spouse, parents, children and (unless `close`) brothers and sisters.
    `close`: only those for whom this is the loss of a spouse or a child (their spouse and their parents)."""
    row = conn.execute("SELECT spouse_id, parent_a_id, parent_b_id FROM npcs WHERE id = ?", (npc_id,)).fetchone()
    if not row:
        return []
    kin = {row["spouse_id"], row["parent_a_id"], row["parent_b_id"]}
    kin |= {r[0] for r in conn.execute("SELECT id FROM npcs WHERE spouse_id = ? AND alive = 1", (npc_id,))}
    if not close:
        kin |= {r[0] for r in conn.execute("SELECT id FROM npcs WHERE (parent_a_id = ? OR parent_b_id = ?) AND alive = 1",
                                           (npc_id, npc_id))}
        parents = [p for p in (row["parent_a_id"], row["parent_b_id"]) if p]
        if parents:
            marks = ",".join("?" * len(parents))
            kin |= {r[0] for r in conn.execute(
                f"SELECT id FROM npcs WHERE (parent_a_id IN ({marks}) OR parent_b_id IN ({marks})) AND alive = 1 AND id != ?",
                (*parents, *parents, npc_id))}
    kin.discard(None)
    kin.discard(npc_id)
    return list(kin)


GRIEF_CAUSES = {"old age", "sickness", "starvation", "fire", "flood", "earthquake", "lost in the wilds"}


def mourn(conn, npc_id, cause):
    """A death moves the kin: a violent one breeds vengeance in spouse, parents, children, brothers and sisters;
    a death by sickness, age or mishap grieves a spouse or parent."""
    if cause in GRIEF_CAUSES:
        return nudge(conn, kin_of(conn, npc_id, close=True), "grief")
    return nudge(conn, kin_of(conn, npc_id), "kin_slain")


MASTER_LEVEL = 7           # a skill at this level is mastered (a master bard's song is twice as strong: Data.Magic.magic)


def masters_year(conn, world_id):
    """Yearly: those who have mastered a skill feel it (once in a life). Returns how many."""
    ids = [r[0] for r in conn.execute(
        """SELECT DISTINCT s.npc_id FROM npc_skills s JOIN npcs n ON n.id = s.npc_id JOIN cities c ON c.id = n.city_id
           JOIN kingdoms k ON k.id = c.kingdom_id
           WHERE k.world_id = ? AND n.alive = 1 AND n.loyalty IS NOT NULL AND s.level >= ?
           AND (n.trait_drift IS NULL OR n.trait_drift NOT LIKE '%"master"%')""", (world_id, MASTER_LEVEL))]
    return nudge(conn, ids, "master")


def leanings(race):
    """How far a people's average moves on each trait (Races.json)."""
    from Data.NPCS.races import race_of
    if not race:
        return {}
    return race_of(race).get("leanings") or {}


def random_traits(race=None):
    """A person's own traits: around their people's middle, most within SPREAD of it."""
    lean = leanings(race)
    return {trait: _clamp(random.gauss(lean.get(trait, 0), SPREAD)) for trait in TRAITS}


def inherited_traits(parent_a, parent_b, race=None):
    """A child's traits: their parents' average, plus their own variation. A trait neither parent has
    (an older parent, from before it existed) comes from the child's people, as anyone's would."""
    lean = leanings(race)
    traits = {}
    for trait in TRAITS:
        a = parent_a.get(trait) if parent_a else None
        b = parent_b.get(trait) if parent_b else None
        known = [v for v in (a, b) if v is not None]
        middle = sum(known) / len(known) if known else lean.get(trait, 0)
        traits[trait] = _clamp(middle + random.gauss(0, CHILD_VARIATION if known else SPREAD))
    return traits


def people_version(conn, world_id):
    """Which generation of people a world has (0: made before the twelve traits)."""
    try:
        row = conn.execute("SELECT people_version FROM worlds WHERE id = ?", (world_id,)).fetchone()
    except Exception:
        return 0
    return (row[0] or 0) if row else 0


def has_engine(conn, world_id):
    """True if the world's people have everything the Decision Engine reads."""
    return people_version(conn, world_id) >= PEOPLE_VERSION


def describe(npc):
    """'generous, harsh' -- the traits strong enough to stand out; 'even-tempered' if none. For the tools that
    explain a person's choices only: the world never shows a person's character in words."""
    words = []
    for trait in TRAITS:
        value = npc.get(trait) or 0
        if value >= STRONG:
            words.append(WORDS[trait][0])
        elif value <= -STRONG:
            words.append(WORDS[trait][1])
    return ", ".join(words) if words else "even-tempered"


def ensure_personalities(conn, world_id):
    """
    A world made before personalities gives everyone who lacks one the first
    three traits (FIRST_TRAITS) -- children taking after their parents where
    they are known. The other nine come only with a new world (PEOPLE_VERSION).
    Safe to call every start. Returns how many NPCs were given traits.
    """

    rows = conn.execute(
        """SELECT n.id, n.parent_a_id, n.parent_b_id FROM npcs n JOIN cities c ON c.id = n.city_id
           JOIN kingdoms k ON k.id = c.kingdom_id WHERE k.world_id = ? AND n.generosity IS NULL
           ORDER BY n.age DESC, n.id""", (world_id,)
    ).fetchall()

    def traits_of(npc_id):
        row = conn.execute("SELECT generosity, mercy, boldness FROM npcs WHERE id = ?", (npc_id,)).fetchone() if npc_id else None
        return dict(row) if row and row["generosity"] is not None else None

    for row in rows:                      # eldest first, so parents are done before their children
        a, b = traits_of(row["parent_a_id"]), traits_of(row["parent_b_id"])
        traits = inherited_traits(a, b) if (a or b) else random_traits()       # the first three only (FIRST_TRAITS)
        conn.execute("UPDATE npcs SET generosity = ?, mercy = ?, boldness = ? WHERE id = ?",
                     (traits["generosity"], traits["mercy"], traits["boldness"], row["id"]))

    conn.commit()
    return len(rows)
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
PEOPLE_VERSION = 1        # 1: the twelve traits

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


def _clamp(value):
    return max(-100, min(100, int(round(value))))


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
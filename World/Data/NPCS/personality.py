import random

# Personality. Every NPC has three traits, each from -100 to +100:
#
#   generosity   +100 generous ........ -100 greedy
#   mercy        +100 merciful ........ -100 harsh
#   boldness     +100 bold ............ -100 cautious
#
# Most people sit near the middle; a few are extreme. A child takes after
# both parents -- their average, plus their own variation -- so a greedy line
# tends to stay greedy without being locked into it. Traits are set once, at
# birth or creation, and never change. Rulers weigh them when they issue
# edicts (Data.Government.edicts); everyone else will use them later.

TRAITS = ("generosity", "mercy", "boldness")

SPREAD = 40               # how far people usually stray from the middle
CHILD_VARIATION = 25      # how far a child strays from their parents' average
STRONG = 40               # a trait this far from 0 shows in how someone is described

WORDS = {
    "generosity": ("generous", "greedy"),
    "mercy": ("merciful", "harsh"),
    "boldness": ("bold", "cautious"),
}


def _clamp(value):
    return max(-100, min(100, int(round(value))))


def random_traits():
    return {trait: _clamp(random.gauss(0, SPREAD)) for trait in TRAITS}


def inherited_traits(parent_a, parent_b):
    """A child's traits: their parents' average, plus their own variation."""
    traits = {}
    for trait in TRAITS:
        a = parent_a.get(trait) if parent_a else None
        b = parent_b.get(trait) if parent_b else None
        known = [v for v in (a, b) if v is not None]
        middle = sum(known) / len(known) if known else 0
        traits[trait] = _clamp(middle + random.gauss(0, CHILD_VARIATION if known else SPREAD))
    return traits


def describe(npc):
    """'generous, harsh' -- the traits strong enough to show; 'even-tempered' if none."""
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
    A world made before personalities gives everyone who lacks one a
    personality -- children taking after their parents where they are known.
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
        traits = inherited_traits(a, b) if (a or b) else random_traits()
        conn.execute("UPDATE npcs SET generosity = ?, mercy = ?, boldness = ? WHERE id = ?",
                     (traits["generosity"], traits["mercy"], traits["boldness"], row["id"]))

    conn.commit()
    return len(rows)
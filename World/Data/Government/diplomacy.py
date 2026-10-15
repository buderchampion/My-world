import math
import random
from itertools import combinations

# ---------------------------------------------------------------------------
# Alliances
# ---------------------------------------------------------------------------

# How much each kingdom must like the other before an alliance can form.
# BOTH directions have to clear it -- favor is directional, so one kingdom
# adoring a neighbor that does not return the feeling is not an alliance.
ALLIANCE_FAVOR_THRESHOLD = 60.0

# An alliance ends when EITHER kingdom's favor toward the other falls below
# this. Deliberately lower than the threshold to form one, so a friendship
# that dips a little does not flicker on and off.
ALLIANCE_BREAK_THRESHOLD = 40.0

# Cap on how many allies one kingdom can have. Without a cap, kingdoms that
# all get along would ally with everyone, and since war target selection
# skips allies, wars would stop happening entirely.
MAX_ALLIES_PER_KINGDOM = 2


# ---------------------------------------------------------------------------
# Where favor comes from
# ---------------------------------------------------------------------------

# Each year two kingdoms spend at peace, each one's favor toward the other
# grows by a random amount in this range (rolled separately for each
# direction), scaled down the warmer the relationship already is -- close
# to nothing near 100. That slowing matters: with a flat dividend, half of
# all relationships in a long-running world ended up pinned at exactly 100
# and every pair of kingdoms looked identical. Now "friendly" comes quickly
# and "devoted" takes a generation, so closeness actually differs.
PEACE_DIVIDEND_RANGE = (3.0, 10.0)

# When a new monarch takes the throne, their kingdom keeps this fraction of
# its old feelings toward each neighbor and re-rolls the rest at random
# (a new king does not automatically share his father's friends). A ruler
# deposed by civil war is a harsher break from the old regime.
SUCCESSION_KEEP = 0.5
SUCCESSION_KEEP_OVERTHROWN = 0.25
SUCCESSION_FRESH_RANGE = (-40.0, 60.0)

# What a war does to feelings. The invaded kingdom resents it far more than
# the invader regrets it; taking a city adds to the injury.
WAR_FAVOR_DEFENDER = -30.0
WAR_FAVOR_ATTACKER = -10.0
WAR_FAVOR_CAPTURED_EXTRA = -20.0


# ---------------------------------------------------------------------------
# Choosing wars
# ---------------------------------------------------------------------------

# A kingdom with no enemies rarely goes to war; one with a bitter grievance
# against a neighbor is far more likely to. The chance climbs from the base
# to base + grievance as its worst feeling toward any possible target falls
# from neutral to WAR_GRIEVANCE_FULL_AT below zero.
WAR_BASE_CHANCE = 0.04
WAR_GRIEVANCE_CHANCE = 0.08
WAR_GRIEVANCE_FULL_AT = 60.0

# Among possible targets, one the attacker resents is picked more often and
# one it likes less often, on top of the existing preference for near
# neighbors. At these settings a hated kingdom is up to 4x as likely a
# target, and a loved one a quarter as likely.
WAR_GRUDGE_SCALE = 40.0
WAR_GRUDGE_FACTOR_RANGE = (0.25, 4.0)


def _world_kingdoms(conn, world_id):
    """Just each kingdom's id and name. Diplomacy needs nothing more, and
    get_world_kingdoms builds a full record per kingdom (about 400 ms each
    call) that would be thrown away."""

    rows = conn.execute("SELECT id, name FROM kingdoms WHERE world_id = ? ORDER BY id", (world_id,)).fetchall()

    return [dict(row) for row in rows]


def _treaty_active(relation, current_year):
    return bool(
        relation["status"] == "Peace Treaty"
        and relation["peace_until_year"]
        and current_year < relation["peace_until_year"]
    )


def apply_peace_dividend(conn, world_id, current_year):
    """
    Yearly: every pair of kingdoms that is not under an active Peace Treaty
    (that is, has not recently been at war) grows a little fonder of each
    other. This is how kingdoms that never trade -- or never fight -- can
    still become friends; trade only warms a pair when each side happens to
    need what the other has.

    The gain shrinks as favor rises (see PEACE_DIVIDEND_RANGE). A recent war
    pauses the dividend for as long as its Peace Treaty lasts. Allied pairs
    keep growing closer, slowly. Returns how many pairs were warmed.
    """

    from Data.Database.repository import get_kingdom_relation, get_kingdom_favor, adjust_kingdom_favor

    kingdoms = _world_kingdoms(conn, world_id)

    warmed = 0

    for kingdom_a, kingdom_b in combinations(kingdoms, 2):

        relation = get_kingdom_relation(conn, kingdom_a["id"], kingdom_b["id"])

        if _treaty_active(relation, current_year):
            continue

        for source, target in ((kingdom_a, kingdom_b), (kingdom_b, kingdom_a)):

            current = get_kingdom_favor(conn, source["id"], target["id"])
            gain = random.uniform(*PEACE_DIVIDEND_RANGE) * (1.0 - max(0.0, current) / 100.0)

            adjust_kingdom_favor(conn, source["id"], target["id"], gain)

        warmed += 1

    conn.commit()

    return warmed


def get_allied_kingdom_ids(conn, kingdom_id):
    """The ids of every kingdom currently allied with this one."""

    rows = conn.execute(
        """
        SELECT kingdom_a_id, kingdom_b_id FROM kingdom_relations
        WHERE status = 'Allied' AND (kingdom_a_id = ? OR kingdom_b_id = ?)
        """,
        (kingdom_id, kingdom_id)
    ).fetchall()

    return [
        row["kingdom_b_id"] if row["kingdom_a_id"] == kingdom_id else row["kingdom_a_id"]
        for row in rows
    ]


def end_failed_alliances(conn, world_id, current_year):
    """
    Yearly: an alliance ends when either kingdom's favor toward the other
    has fallen below ALLIANCE_BREAK_THRESHOLD -- usually because a new
    ruler took the throne, or something worse happened between them.

    The pair goes back to Neutral (their favor is left exactly as it is, so
    they can become allies again if it recovers) and an Alliance Ended event
    is logged. Run this BEFORE check_alliances so a freed-up ally slot can be
    used in the same year. Returns a list describing each alliance ended,
    including which kingdom's regard fell the furthest ("cooler").
    """

    from Data.Database.repository import get_kingdom_relation, set_kingdom_relation, get_kingdom_favor
    from Data.Events.events import alliance_ended_event

    kingdoms = _world_kingdoms(conn, world_id)

    ended = []

    for kingdom_a, kingdom_b in combinations(kingdoms, 2):

        relation = get_kingdom_relation(conn, kingdom_a["id"], kingdom_b["id"])

        if relation["status"] != "Allied":
            continue

        favor_a_to_b = get_kingdom_favor(conn, kingdom_a["id"], kingdom_b["id"])
        favor_b_to_a = get_kingdom_favor(conn, kingdom_b["id"], kingdom_a["id"])

        if min(favor_a_to_b, favor_b_to_a) >= ALLIANCE_BREAK_THRESHOLD:
            continue

        set_kingdom_relation(conn, kingdom_a["id"], kingdom_b["id"], "Neutral")
        alliance_ended_event(conn, kingdom_a, kingdom_b)

        ended.append({
            "kingdom_a": kingdom_a,
            "kingdom_b": kingdom_b,
            "year": current_year,
            "favor_a_to_b": favor_a_to_b,
            "favor_b_to_a": favor_b_to_a,
            "cooler": kingdom_a if favor_a_to_b < favor_b_to_a else kingdom_b,
        })

    conn.commit()

    return ended


def check_alliances(conn, world_id, current_year):
    """
    Yearly: any two kingdoms that have come to genuinely like each other
    -- each one's favor toward the other at or above ALLIANCE_FAVOR_THRESHOLD
    -- forge an alliance.

    A pair is skipped if it is already allied, or if it is still inside a
    Peace Treaty from a recent war (old enemies get a cooling-off period
    before they can become friends). When several pairs qualify at once the
    warmest friendships are served first, and a kingdom stops accepting
    alliances once it has MAX_ALLIES_PER_KINGDOM.

    Each new alliance sets the pair's relation to Allied and logs an
    Alliance event. Returns a list describing the alliances formed this
    year (empty if none), so later systems can react to them.
    """

    from Data.Database.repository import get_kingdom_relation, set_kingdom_relation, get_kingdom_favor
    from Data.Events.events import alliance_event

    kingdoms = _world_kingdoms(conn, world_id)

    candidates = []

    for kingdom_a, kingdom_b in combinations(kingdoms, 2):

        relation = get_kingdom_relation(conn, kingdom_a["id"], kingdom_b["id"])

        if relation["status"] == "Allied":
            continue

        if _treaty_active(relation, current_year):
            continue

        favor_a_to_b = get_kingdom_favor(conn, kingdom_a["id"], kingdom_b["id"])
        favor_b_to_a = get_kingdom_favor(conn, kingdom_b["id"], kingdom_a["id"])

        strength = min(favor_a_to_b, favor_b_to_a)

        if strength < ALLIANCE_FAVOR_THRESHOLD:
            continue

        candidates.append((strength, kingdom_a, kingdom_b, favor_a_to_b, favor_b_to_a))

    # Warmest friendships first. Shuffling beforehand makes ties (common when
    # several pairs have been warmed by the same peaceful years) break at
    # random, rather than always favoring whichever kingdoms have low ids.
    random.shuffle(candidates)
    candidates.sort(key=lambda c: -c[0])

    ally_counts = {k["id"]: len(get_allied_kingdom_ids(conn, k["id"])) for k in kingdoms}

    formed = []

    for strength, kingdom_a, kingdom_b, favor_a_to_b, favor_b_to_a in candidates:

        if (
            ally_counts[kingdom_a["id"]] >= MAX_ALLIES_PER_KINGDOM
            or ally_counts[kingdom_b["id"]] >= MAX_ALLIES_PER_KINGDOM
        ):
            continue

        set_kingdom_relation(conn, kingdom_a["id"], kingdom_b["id"], "Allied")
        alliance_event(conn, kingdom_a, kingdom_b)

        ally_counts[kingdom_a["id"]] += 1
        ally_counts[kingdom_b["id"]] += 1

        formed.append({
            "kingdom_a": kingdom_a,
            "kingdom_b": kingdom_b,
            "year": current_year,
            "favor_a_to_b": favor_a_to_b,
            "favor_b_to_a": favor_b_to_a,
        })

    conn.commit()

    return formed


def apply_succession_shock(conn, kingdom_id, overthrown=False):
    """
    A new monarch has taken this kingdom's throne. Its feelings toward every
    other kingdom are partly re-rolled: it keeps SUCCESSION_KEEP of the old
    favor and the rest becomes a fresh random attitude. Only THIS kingdom's
    feelings change -- how the neighbors feel about it is unaffected, since
    they have not changed rulers.

    Pass overthrown=True when the old ruler was deposed (a civil war the
    rebels won) rather than dying; that repudiates the old regime's friends
    more thoroughly. Returns how many relationships were shaken.
    """

    from Data.Database.repository import get_kingdom_favor, adjust_kingdom_favor

    keep = SUCCESSION_KEEP_OVERTHROWN if overthrown else SUCCESSION_KEEP

    others = conn.execute(
        """
        SELECT id FROM kingdoms
        WHERE id != ? AND world_id = (SELECT world_id FROM kingdoms WHERE id = ?)
        """,
        (kingdom_id, kingdom_id)
    ).fetchall()

    for other in others:

        current = get_kingdom_favor(conn, kingdom_id, other["id"])
        fresh = random.uniform(*SUCCESSION_FRESH_RANGE)

        adjust_kingdom_favor(conn, kingdom_id, other["id"], keep * current + (1.0 - keep) * fresh - current)

    conn.commit()

    return len(others)


def apply_war_favor(conn, attacker_id, defender_id, captured_city=False):
    """
    A war has just been fought. The defender's favor toward the attacker
    drops a lot (more if a city was taken); the attacker's toward the
    defender drops a little. Does not commit -- called from inside the
    war's own transaction.
    """

    from Data.Database.repository import adjust_kingdom_favor

    defender_hit = WAR_FAVOR_DEFENDER + (WAR_FAVOR_CAPTURED_EXTRA if captured_city else 0.0)

    adjust_kingdom_favor(conn, defender_id, attacker_id, defender_hit)
    adjust_kingdom_favor(conn, attacker_id, defender_id, WAR_FAVOR_ATTACKER)


def war_chance(favors):
    """
    The chance this kingdom goes to war this year, given its favor toward
    each kingdom it could attack. Driven by its single worst grievance:
    WAR_BASE_CHANCE with no enemies, rising to WAR_BASE_CHANCE +
    WAR_GRIEVANCE_CHANCE once some neighbor is WAR_GRIEVANCE_FULL_AT or more
    below neutral. Liking a neighbor never lowers it below the base.
    """

    if not favors:
        return 0.0

    grievance = max(0.0, min(1.0, -min(favors) / WAR_GRIEVANCE_FULL_AT))

    return WAR_BASE_CHANCE + WAR_GRIEVANCE_CHANCE * grievance


def grudge_factor(favor):
    """How much more (or less) likely a target is, given the attacker's
    favor toward it: 1.0 at neutral, up for resented kingdoms, down for
    liked ones, within WAR_GRUDGE_FACTOR_RANGE."""

    low, high = WAR_GRUDGE_FACTOR_RANGE

    return max(low, min(high, math.exp(-favor / WAR_GRUDGE_SCALE)))


def war_candidates(conn, kingdom, trade_routes, current_year):
    """
    Every kingdom this one could go to war with this year: the kingdoms
    whose capitals it has a Trade Route to, minus its allies and anyone it
    is still under a Peace Treaty with. Returns a list of
    (route, other_kingdom_id).
    """

    from Data.Database.repository import get_kingdom_relation

    capital_id = kingdom.get("capital")

    if capital_id is None:
        return []

    candidates = []

    for route in trade_routes:

        if capital_id not in (route["city_a_id"], route["city_b_id"]):
            continue

        other_capital_id = route["city_b_id"] if route["city_a_id"] == capital_id else route["city_a_id"]

        row = conn.execute("SELECT kingdom_id FROM cities WHERE id = ?", (other_capital_id,)).fetchone()

        # (a capital that has since been captured can belong to this very
        # kingdom -- a kingdom cannot go to war with itself)
        if row is None or row["kingdom_id"] == kingdom["id"]:
            continue

        relation = get_kingdom_relation(conn, kingdom["id"], row["kingdom_id"])

        if relation["status"] == "Allied" or _treaty_active(relation, current_year):
            continue

        candidates.append((route, row["kingdom_id"]))

    return candidates


def choose_war_target(conn, kingdom_id, candidates):
    """
    Decides whether this kingdom goes to war this year and, if so, against
    whom. candidates is war_candidates()'s list of (route, other_kingdom_id).
    Returns the chosen (route, other_kingdom_id), or None for no war.

    Whether to fight at all depends on its worst grievance (war_chance);
    who to fight favors near neighbors (1 / distance, as before) and now
    also the ones it resents (grudge_factor).
    """

    from Data.Database.repository import get_kingdom_favor

    if not candidates:
        return None

    favors = [get_kingdom_favor(conn, kingdom_id, other_id) for _, other_id in candidates]

    if random.random() >= war_chance(favors):
        return None

    weights = [
        (1.0 / max(1.0, route["distance"])) * grudge_factor(favor)
        for (route, _), favor in zip(candidates, favors)
    ]

    return random.choices(candidates, weights=weights, k=1)[0]
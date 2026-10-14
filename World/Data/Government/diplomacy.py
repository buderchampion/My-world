from itertools import combinations

# How much each kingdom must like the other before an alliance can form.
# BOTH directions have to clear it -- favor is directional, so one kingdom
# adoring a neighbor that does not return the feeling is not an alliance.
ALLIANCE_FAVOR_THRESHOLD = 60.0

# Cap on how many allies one kingdom can have. Without a cap, kingdoms that
# all trade well would slowly ally with everyone, and since war target
# selection skips allies, wars would stop happening entirely.
MAX_ALLIES_PER_KINGDOM = 2


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

    from Data.Database.repository import (
        get_world_kingdoms, get_kingdom_relation, set_kingdom_relation, get_kingdom_favor,
    )
    from Data.Events.events import alliance_event

    kingdoms = get_world_kingdoms(conn, world_id)

    candidates = []

    for kingdom_a, kingdom_b in combinations(kingdoms, 2):

        relation = get_kingdom_relation(conn, kingdom_a["id"], kingdom_b["id"])

        if relation["status"] == "Allied":
            continue

        if (
            relation["status"] == "Peace Treaty"
            and relation["peace_until_year"]
            and current_year < relation["peace_until_year"]
        ):
            continue

        favor_a_to_b = get_kingdom_favor(conn, kingdom_a["id"], kingdom_b["id"])
        favor_b_to_a = get_kingdom_favor(conn, kingdom_b["id"], kingdom_a["id"])

        strength = min(favor_a_to_b, favor_b_to_a)

        if strength < ALLIANCE_FAVOR_THRESHOLD:
            continue

        candidates.append((strength, kingdom_a, kingdom_b, favor_a_to_b, favor_b_to_a))

    candidates.sort(key=lambda c: (-c[0], c[1]["id"], c[2]["id"]))

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
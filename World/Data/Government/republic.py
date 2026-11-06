import random

# Merchant republics -- Venice, Genoa, the Hanseatic towns. A kingdom's type
# is "Crown" (a hereditary monarch, counties and lords: everything built so
# far) or "Republic":
#
#   * its head is the Doge, elected for life by the Great Council -- the
#     capital's wealthiest adults -- from among its notables. Not hereditary,
#     so no regencies; elections are chronicled in their own wording.
#   * it has no hereditary lords: its towns are run by appointed governors
#     (Mayors), so there are no counties and no lords' rebellions -- though
#     popular unrest and civil war remain.
#   * it is poorer in land and richer in trade: half its people live in the
#     countryside rather than RURAL_SHARE (Data.World.countryside), and its
#     economy runs on tariffs and a grain office (part 2).
#
# About a quarter of a new world's kingdoms are republics.

REPUBLIC_SHARE = 0.25
REPUBLIC_RURAL_SHARE = 0.5
COUNCIL_SIZE = 20            # the Great Council: the capital's wealthiest adults
CANDIDATES = 5               # its richest notables stand for election
DOGE = "Doge"
ADULT_AGE = 16

# how a councillor weighs a candidate: their wealth (as a share of the richest
# candidate's), the councillor's feeling toward them where there is one, and chance
WEALTH_WEIGHT, FEELING_WEIGHT, CHANCE_WEIGHT = 0.5, 1.0, 0.4


def republic_count(number_of_kingdoms):
    """How many of a new world's kingdoms are republics: a quarter, rounded (at least one in a world of two or more)."""
    return max(1 if number_of_kingdoms >= 2 else 0, int(number_of_kingdoms * REPUBLIC_SHARE + 0.5))


def is_republic(conn, kingdom_id):
    row = conn.execute("SELECT type FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone()
    return bool(row) and row["type"] == "Republic"


def elect_doge(conn, capital_city):
    """
    The Great Council elects a Doge for life from the capital's notables.
    Titles the winner, makes them the capital's ruler, and logs the vote.
    Returns the new Doge (None if no one can stand).
    """

    from Data.Government.government import RULER_CANDIDATE_PROFESSIONS
    from Data.Database.repository import get_npc, save_npc, create_event

    adults = [dict(row) for row in conn.execute(
        "SELECT id, name, gold, profession, title FROM npcs WHERE city_id = ? AND alive = 1 AND age >= ? ORDER BY gold DESC, id",
        (capital_city["id"], ADULT_AGE))]
    council = adults[:COUNCIL_SIZE]
    hopefuls = [a for a in adults if a["title"] is None and a["profession"] in RULER_CANDIDATE_PROFESSIONS][:CANDIDATES] \
        or [a for a in adults if a["title"] is None][:CANDIDATES]
    if not hopefuls:
        return None

    richest = max(1.0, max(c["gold"] for c in hopefuls))
    candidate_ids = {c["id"] for c in hopefuls}
    votes = {c["id"]: 0 for c in hopefuls}

    for member in council:
        if member["id"] in candidate_ids:
            votes[member["id"]] += 1          # a candidate votes for themselves
            continue
        def weight(candidate):
            rel = conn.execute(
                "SELECT reputation FROM relationships WHERE (npc_a_id = ? AND npc_b_id = ?) OR (npc_a_id = ? AND npc_b_id = ?)",
                (member["id"], candidate["id"], candidate["id"], member["id"])).fetchone()
            feeling = (rel["reputation"] - 50) / 100 if rel else 0.0
            return (WEALTH_WEIGHT * max(0.0, candidate["gold"]) / richest + FEELING_WEIGHT * feeling
                    + CHANCE_WEIGHT * random.random())
        votes[max(hopefuls, key=weight)["id"]] += 1

    by_wealth = {c["id"]: c["gold"] for c in hopefuls}
    winner_id = max(votes, key=lambda i: (votes[i], by_wealth[i]))

    doge = get_npc(conn, winner_id)
    doge["title"] = DOGE
    save_npc(conn, doge)
    conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (doge["id"], capital_city["id"]))
    # the kingdom's record names its new Doge at once (as with crowns: nothing may see the old one in between)
    conn.execute("UPDATE kingdoms SET ruler_id = ?, regent_id = NULL WHERE id = ?", (doge["id"], capital_city["kingdom"]))

    kingdom = conn.execute("SELECT name FROM kingdoms WHERE id = ?", (capital_city["kingdom"],)).fetchone()
    create_event(conn, "Election",
                 f"The Great Council of {kingdom['name']} ({len(council)} members) elected {doge['name']} as Doge, "
                 f"with {votes[winner_id]} votes of {len(council)}.",
                 city_id=capital_city["id"], kingdom_id=capital_city["kingdom"])
    conn.commit()
    return doge
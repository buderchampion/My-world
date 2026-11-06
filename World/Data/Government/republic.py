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
#     economy runs on tariffs and a grain office:
#       - tariffs, paid by foreigners: a kingdom buying from a republic pays a
#         10% export duty on top; a republic buying abroad keeps a 5% import
#         duty from the price (the foreign seller receives 95%);
#       - the grain office: each month it tops up the capital's grain reserve
#         toward 60 days of food for the republic's townsfolk, buying from the
#         foreign kingdoms with the biggest surplus (never one that has banned
#         food exports), spending at most a quarter of the treasury a month.
#         The grain comes by caravan -- and can be raided on the way.
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

EXPORT_DUTY = 0.10           # a foreign buyer pays this much on top when buying from a republic
IMPORT_DUTY = 0.05           # a republic keeps this much of the price when buying abroad
GRAIN_RESERVE_DAYS = 60      # the grain office keeps this many days of food for the townsfolk
GRAIN_BUDGET_SHARE = 0.25    # and spends at most this share of the treasury in a month


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


def duty_rate(seller_kingdom, buyer_kingdom):
    """What a buyer pays per gold of the seller's price, after republics'
    customs duties -- unrounded, for working out quantities."""
    rate = 1.0
    if seller_kingdom.get("type") == "Republic":
        rate *= 1 + EXPORT_DUTY                  # the export duty, on top, to the republic
    if buyer_kingdom.get("type") == "Republic":
        rate *= 1 - IMPORT_DUTY                  # the import duty, kept back by the republic
    return rate


def duties(seller_kingdom, buyer_kingdom, cost):
    """
    (what the buyer pays, what the seller receives) for a cross-kingdom sale
    of `cost` at the seller's price, after republics' customs duties. (The
    duties go to the republic involved, so the two are always equal.)
    """
    paid = round(cost * duty_rate(seller_kingdom, buyer_kingdom), 2)
    return paid, paid


def run_grain_offices(conn, world_id):
    """
    Monthly: each republic's grain office tops up the capital's reserve
    toward GRAIN_RESERVE_DAYS of food for its townsfolk, buying abroad from
    the kingdoms with the biggest Food surplus. Returns [(republic, seller, amount, cost)].
    """

    from Data.Database.repository import (get_city, save_city, get_kingdom_cities, spend_treasury, add_treasury,
                                          adjust_kingdom_favor, create_event)
    from Data.Economy.economy import get_price, get_target_stock, TRADE_FAVOR_GAIN, TRADE_FAVOR_LOSS
    from Data.Government.edicts import exports_banned, today
    from Data.Infrastructure.caravans import dispatch_caravan

    day = today(conn, world_id)
    kingdoms = [dict(k) for k in conn.execute("SELECT * FROM kingdoms WHERE world_id = ?", (world_id,))]
    bought = []

    for republic in (k for k in kingdoms if k["type"] == "Republic"):
        if not republic["capital_city_id"]:
            continue
        townsfolk = conn.execute(
            "SELECT COUNT(*) FROM npcs n JOIN cities c ON c.id = n.city_id WHERE c.kingdom_id = ? AND n.alive = 1",
            (republic["id"],)).fetchone()[0]
        capital = get_city(conn, republic["capital_city_id"])
        wanted = townsfolk * GRAIN_RESERVE_DAYS - capital["resources"].get("Food", 0)
        budget = republic["treasury"] * GRAIN_BUDGET_SHARE
        if wanted <= 0 or budget <= 0:
            continue

        # who can sell: foreign capitals holding more Food than their own needs, biggest first
        sellers = []
        for other in kingdoms:
            if other["id"] == republic["id"] or not other["capital_city_id"] or exports_banned(other, day):
                continue
            seat = get_city(conn, other["capital_city_id"])
            spare = seat["resources"].get("Food", 0) - get_target_stock("Food", seat)
            if spare > 0:
                sellers.append((spare, other, seat))
        sellers.sort(key=lambda entry: -entry[0])

        for spare, seller, seat in sellers:
            if wanted <= 0 or budget <= 0:
                break
            price = get_price("Food", seat)
            if price <= 0:
                continue
            amount = int(min(wanted, spare, budget / (price * duty_rate(seller, republic))) * 100) / 100   # never over budget
            if amount <= 0:
                continue
            paid, received = duties(seller, republic, amount * price)
            if not spend_treasury(conn, republic["id"], paid):
                break
            add_treasury(conn, seller["id"], received)
            seat["resources"]["Food"] = round(seat["resources"]["Food"] - amount, 2)
            save_city(conn, seat)
            adjust_kingdom_favor(conn, republic["id"], seller["id"], TRADE_FAVOR_GAIN)
            adjust_kingdom_favor(conn, seller["id"], republic["id"], TRADE_FAVOR_LOSS)
            dispatch_caravan(conn, seat["id"], republic["capital_city_id"], "Food", amount, republic["id"], seller["id"])
            create_event(conn, "Grain Office",
                         f"The grain office of {republic['name']} bought {amount:.0f} Food from {seller['name']} "
                         f"for {paid:.0f} gold, to fill the republic's granaries.",
                         kingdom_id=republic["id"])
            bought.append((republic["name"], seller["name"], amount, paid))
            wanted -= amount
            budget -= paid

    conn.commit()
    return bought
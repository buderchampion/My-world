# Feudal obligations. Every county's taxes -- the coin its towns and villages
# pay, and the grain its villages give at harvest -- go first to its lord,
# who keeps LORD_SHARE and sends the rest up to the crown. The crown's own
# lands (the capital and the villages nearest it) pay the crown directly.
#
# Each lord has a loyalty to the crown, 0-100. Every month it moves a tenth of
# the way toward a target set by how the lord is being treated:
#
#   55 to begin with
#   +20 if the lord is of the monarch's own family
#   -10 to +10 for their personal feeling toward the monarch, if they have one
#   +5 if the crown won a war in the last two years, -10 if it lost one
#   up to -20 for famine in the county (the share of its villages going hungry)
#   -10 for each step bread prices stand above normal in the kingdom
#   and the tax burden: 0 at the standard share, more or less if the crown
#   ever takes more or less (edicts, later)
#
# A lord whose loyalty falls below RESTLESS_BELOW is restless -- the stuff
# rebellions are made of (part 3).

LORD_SHARE = 0.30            # of their county's taxes, the lord keeps this much
STANDARD_LORD_SHARE = 0.30   # the share lords expect; keeping less angers them
LOYALTY_START = 70           # a new lord's loyalty
LORD_SPENDING = 0.10         # each month a lord spends this share of their purse on household, castle and retinue
                             # (so savings settle at about ten months' income -- enough for mercenaries, not an empire)
LOYALTY_DRIFT = 0.10         # each month, this share of the way toward the target
RESTLESS_BELOW = 30

BASE = 55
KIN_BONUS = 20
WAR_WON, WAR_LOST, WAR_MEMORY_YEARS = 5, -10, 2
FAMINE_MAX = 20
BREAD_PER_STEP = 10
TAX_PER_SHARE = 100          # each 1% of share kept below the standard costs 1 point
STANDARD_TAX_RATE = 0.10     # the crown's usual tax on the people; lords like it lower
TAX_RATE_PER_POINT = 100     # each 1% the crown taxes above (or below) the usual costs (or earns) 1 point


def lord_cut(amount):
    return amount * LORD_SHARE


def pay_lords(conn, takings):
    """Adds each province's takings (province id -> gold) to its lord's purse."""
    conn.executemany("UPDATE provinces SET treasury = treasury + ? WHERE id = ?",
                     [(round(gold, 2), pid) for pid, gold in takings.items() if gold > 0])


def loyalty_target(conn, province, kingdom, year):
    """What this lord's loyalty is drifting toward, and why (for testing and reports)."""

    from Data.Government.succession import line_of_succession

    reasons = {"base": BASE}
    lord_id, monarch_id = province["lord_id"], kingdom["ruler_id"]

    if monarch_id and lord_id in line_of_succession(conn, monarch_id, monarch_id):
        reasons["kin of the monarch"] = KIN_BONUS

    if monarch_id:
        rel = conn.execute(
            """SELECT reputation FROM relationships WHERE (npc_a_id = ? AND npc_b_id = ?) OR (npc_a_id = ? AND npc_b_id = ?)""",
            (lord_id, monarch_id, monarch_id, lord_id)
        ).fetchone()
        if rel:
            reasons["feeling toward the monarch"] = round((rel["reputation"] - 50) / 5, 1)

    if kingdom["last_war_year"] is not None and year - kingdom["last_war_year"] < WAR_MEMORY_YEARS:
        reasons["the crown's last war"] = WAR_WON if kingdom["last_war_won"] else WAR_LOST

    villages = conn.execute(
        "SELECT COUNT(*) n, SUM(CASE WHEN hungry_days > 0 THEN 1 ELSE 0 END) hungry FROM cities WHERE province_id = ? AND households > 0",
        (province["id"],)
    ).fetchone()
    if villages["n"] and villages["hungry"]:
        reasons["famine"] = -round(FAMINE_MAX * villages["hungry"] / villages["n"], 1)

    if kingdom["price_level"] > 1.0:
        reasons["bread prices"] = -round(BREAD_PER_STEP * (kingdom["price_level"] - 1.0), 1)

    rate = kingdom["tax_rate"] if kingdom["tax_rate"] is not None else STANDARD_TAX_RATE
    if abs(rate - STANDARD_TAX_RATE) > 1e-9:
        reasons["the crown's taxes"] = round((STANDARD_TAX_RATE - rate) * TAX_RATE_PER_POINT, 1)

    if LORD_SHARE != STANDARD_LORD_SHARE:
        reasons["taxes"] = round((LORD_SHARE - STANDARD_LORD_SHARE) * TAX_PER_SHARE, 1)

    return max(0.0, min(100.0, sum(reasons.values()))), reasons


def update_loyalty(conn, world_id):
    """
    Monthly: every lord spends a share of their purse, and their loyalty
    drifts toward its target; a lord who turns restless, or recovers, is
    logged. Returns {province id: new loyalty}.
    """

    from Data.Database.repository import create_event
    from Data.History.chronicle import current_date

    year, _ = current_date(conn, world_id)
    result = {}

    # lords live on their income: a share of each purse is spent every month
    conn.execute(
        "UPDATE provinces SET treasury = treasury * ? WHERE kingdom_id IN (SELECT id FROM kingdoms WHERE world_id = ?)",
        (1 - LORD_SPENDING, world_id))

    for province in conn.execute(
        """SELECT p.*, n.name lord_name FROM provinces p JOIN kingdoms k ON k.id = p.kingdom_id
           JOIN npcs n ON n.id = p.lord_id WHERE k.world_id = ? AND n.alive = 1""", (world_id,)
    ).fetchall():
        kingdom = conn.execute("SELECT * FROM kingdoms WHERE id = ?", (province["kingdom_id"],)).fetchone()
        target, _ = loyalty_target(conn, province, kingdom, year)
        old = province["loyalty"]
        new = round(old + (target - old) * LOYALTY_DRIFT, 2)
        conn.execute("UPDATE provinces SET loyalty = ? WHERE id = ?", (new, province["id"]))
        result[province["id"]] = new

        if old >= RESTLESS_BELOW > new:
            create_event(conn, "Restless Lord",
                         f"{province['lord_name']} of the {province['name']} grows restless under the crown.",
                         city_id=province["seat_city_id"], kingdom_id=province["kingdom_id"])
        elif old < RESTLESS_BELOW <= new:
            create_event(conn, "Lord Reconciled",
                         f"{province['lord_name']} of the {province['name']} has made peace with the crown.",
                         city_id=province["seat_city_id"], kingdom_id=province["kingdom_id"])

    conn.commit()
    return result
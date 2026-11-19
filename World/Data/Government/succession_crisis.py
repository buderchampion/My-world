"""
Succession crises: a crown that passes to a weak claim may be disputed.

When a crown passes (Data.Government.succession.inherit_crown) -- or, its
line run dry, falls to someone the capital's people choose (a new house) --
the new monarch's claim may be weak, and a rival may contest it. Republics
elect their Doges and hordes raise the strongest; neither is disputed here.

How weak    WEAKNESS points: a child on the throne (a regent rules), a claim
            that came sideways (a sibling, the spouse) or from far down the
            line (other kin), or no claim at all (a new house). A monarch's
            own child of age has a sound claim: no dispute.

The rival   the boldest, least loyal great lord of the realm (Count or
            Countess: Data.Government.feudal), stronger still with blood of
            the old line -- or, when the heir is a child, the first grown
            kin of the line who was passed over. Ambition is boldness, and a
            lord's discontent (loyalty below the middle).

The chance  CRISIS_PER_POINT for each point of weakness, more for a bold rival
            and less for a timid one.

By circumstance:
  Politics  (weakness below WAR_AT) the realm's lords take sides: each backs
            the crown if loyal enough (SIDE_LOYALTY), else the claimant, and
            weighs by its county's households (the crown's own lands stand
            with the crown). If the crown's side outweighs
            the claimant's, the claim fails -- the claimant withdraws, or (if
            bold) goes into exile in a neighbouring realm, losing their
            county to their heir, or (if bold and merciless) has the young
            monarch murdered (PLOT_CHANCE) and the crown passes on as it
            would. If the claimant's side outweighs, the lords set the heir
            aside: the claimant is crowned.
  War       (WAR_AT or more: no claim at all, or a child of the far line) the
            claimant raises their county and every disloyal lord, hires
            mercenaries with their purses, and fights the crown's soldiers
            (the odds follow strength, as in a rising: Data.Government.
            rebellion). If the claimant wins, they are crowned, and the
            deposed monarch is executed or exiled by their mercy; if the crown
            wins, the claimant and their lords are executed and their counties
            go to new houses.
Every dispute and its end is chronicled. A lord who backed the loser is
less loyal for it (LOSER_LOYALTY).
"""

from __future__ import annotations

import random

WEAKNESS = {"child": 2, "sibling": 1, "spouse": 1, "kin": 2, "none": 3}   # what makes a claim weak (they add up)
CRISIS_PER_POINT = 0.12        # the chance of a dispute, for each point of weakness...
BOLD_SWAY = 0.5                # ...times (1 + this * the rival's boldness / 100)
WAR_AT = 3                     # weakness this great: the dispute is fought out
SIDE_LOYALTY = 50              # a lord at least this loyal backs the crown
EXILE_BOLDNESS = 20            # a failed claimant this bold goes into exile rather than bow
PLOT_CHANCE = 0.3              # ...and if also merciless (mercy below PLOT_MERCY), may have the monarch murdered instead
PLOT_MERCY = -20
EXECUTE_MERCY = 0              # a victorious claimant with mercy below this executes the deposed monarch (else exiles them)
LOSER_LOYALTY = 15             # loyalty lost by a lord who backed the losing side
MERCENARY_COST = 500
DISPUTED_KINDS = ("Crown", "Empire", "Horse Kingdom")


def weakness(conn, heir, regent, previous, relation=None):
    """How weak the new monarch's claim is (0: sound). relation: from Data.Government.succession.relation_word,
    or 'none' for a new house."""
    from Data.Government.succession import relation_word
    if relation is None:
        relation = relation_word(conn, heir["id"], previous["id"]) if previous else "none"
    points = {"parent": 0, "child": WEAKNESS["kin"], "sibling": WEAKNESS["sibling"], "spouse": WEAKNESS["spouse"],
              "kin": WEAKNESS["kin"], "none": WEAKNESS["none"]}.get(relation, WEAKNESS["kin"])
    if regent:
        points += WEAKNESS["child"]
    return points, relation


def _lords(conn, kingdom_id):
    return [dict(r) for r in conn.execute(
        """SELECT p.*, n.name AS lord_name, n.boldness, n.mercy, n.gender,
                  (SELECT COALESCE(SUM(households), 0) FROM cities c WHERE c.province_id = p.id) AS households
           FROM provinces p JOIN npcs n ON n.id = p.lord_id WHERE p.kingdom_id = ? AND n.alive = 1 AND n.age >= 16""",
        (kingdom_id,))]


def rival(conn, kingdom_id, heir, regent, previous):
    """The likeliest claimant: (npc, province or None), or (None, None)."""
    from Data.Government.succession import line_of_succession, ADULT_AGE
    from Data.Database.repository import get_npc
    line = set(line_of_succession(conn, previous["id"], previous["id"])) if previous else set()
    best, best_score = (None, None), 0.0
    for p in _lords(conn, kingdom_id):
        if p["lord_id"] == heir["id"] or (regent and p["lord_id"] == regent["id"]):
            continue
        score = (p["boldness"] or 0) / 100 + (SIDE_LOYALTY - p["loyalty"]) / 50 + (1.0 if p["lord_id"] in line else 0) + 0.5
        if score > best_score:
            best, best_score = (get_npc(conn, p["lord_id"]), p), score
    if regent and previous:                                        # a grown kinsman passed over for a child
        for npc_id in line_of_succession(conn, previous["id"], previous["id"]):
            npc = get_npc(conn, npc_id)
            if npc and npc["alive"] and npc["id"] not in (heir["id"], regent["id"]) and npc["age"] >= ADULT_AGE \
                    and npc["title"] not in ("King", "Queen", "Emperor", "Empress", "Khan", "Khanum", "Doge"):
                score = (npc["boldness"] or 0) / 100 + 1.0
                if score > best_score:
                    best, best_score = (npc, None), score
                break
    return best


def _chronicle(conn, world_id, kingdom, key, fields, seed):
    from Data.History.chronicle import write_entry, current_date
    year, season = current_date(conn, world_id)
    write_entry(conn, kingdom["id"], "Succession", key, year, season, dict(fields, kingdom=kingdom["name"]), seed_parts=seed)


def _event(conn, text, kingdom_id, city_id=None):
    from Data.Database.repository import create_event
    create_event(conn, "Succession Crisis", text, city_id=city_id, kingdom_id=kingdom_id)


def crown(conn, kingdom, claimant, deposed):
    """The claimant takes the throne; the deposed monarch is set aside (their title gone). Returns the new monarch."""
    from Data.Government.succession import monarch_title, vacate_seats
    from Data.Infrastructure.migration import relocate_npc
    from Data.Database.repository import get_npc, save_npc
    from Data.Events.events import coronation_event
    capital = kingdom["capital_city_id"]
    conn.execute("UPDATE npcs SET title = NULL WHERE id = ?", (deposed["id"],))
    vacate_seats(conn, claimant, capital)
    conn.execute("UPDATE provinces SET lord_id = NULL, regent_id = NULL WHERE lord_id = ?", (claimant["id"],))
    if claimant["city_id"] != capital:
        relocate_npc(conn, claimant["id"], capital)
    claimant = get_npc(conn, claimant["id"])
    claimant["title"] = monarch_title(conn, kingdom["id"], claimant["gender"])
    save_npc(conn, claimant)
    conn.execute("UPDATE cities SET ruler_id = ? WHERE id = ?", (claimant["id"], capital))
    conn.execute("UPDATE kingdoms SET ruler_id = ?, regent_id = NULL WHERE id = ?", (claimant["id"], kingdom["id"]))
    from Data.Database.repository import get_city
    coronation_event(conn, claimant, get_city(conn, capital))
    return claimant


def exile(conn, kingdom, npc):
    """Into exile in the nearest other realm; a lord's county passes down their line. Returns where they went, or None."""
    from Data.Infrastructure.migration import relocate_npc
    from Data.Government.succession import inherit_county
    from Data.Database.repository import get_npc, get_city
    seat = conn.execute("SELECT map_x, map_y FROM cities WHERE id = ?", (kingdom["capital_city_id"],)).fetchone()
    refuge = conn.execute(
        """SELECT c.id, c.name, k.name AS realm FROM kingdoms k JOIN cities c ON c.id = k.capital_city_id
           WHERE k.world_id = ? AND k.id != ? AND k.status != 'Fallen' AND k.type != 'Horde'
           ORDER BY (c.map_x - ?) * (c.map_x - ?) + (c.map_y - ?) * (c.map_y - ?) LIMIT 1""",
        (kingdom["world_id"], kingdom["id"], seat[0], seat[0], seat[1], seat[1])).fetchone()
    province = conn.execute("SELECT * FROM provinces WHERE lord_id = ?", (npc["id"],)).fetchone()
    conn.execute("UPDATE npcs SET title = NULL WHERE id = ?", (npc["id"],))
    if province:
        conn.execute("UPDATE provinces SET lord_id = NULL, regent_id = NULL WHERE id = ?", (province["id"],))
        conn.execute("UPDATE cities SET ruler_id = NULL WHERE id = ?", (province["seat_city_id"],))
        inherit_county(conn, dict(province), get_city(conn, province["seat_city_id"]), get_npc(conn, npc["id"]))
    if refuge:
        relocate_npc(conn, npc["id"], refuge["id"])
        return refuge["realm"]
    return None


def execute(conn, kingdom, npc):
    from Data.Events.events import death_event
    from Data.Database.repository import get_npc
    conn.execute("UPDATE npcs SET alive = 0, title = NULL, workplace_id = NULL, home_id = NULL WHERE id = ?", (npc["id"],))
    death_event(conn, get_npc(conn, npc["id"]), "Executed", kingdom_id=kingdom["id"])


def politics(conn, kingdom, heir, claimant, claim_province):
    """The lords take sides. Returns 'withdrew', 'exiled', 'murdered' or 'crowned'."""
    lords = [p for p in _lords(conn, kingdom["id"]) if p["lord_id"] not in (heir["id"], claimant["id"])]
    demesne = conn.execute("SELECT COALESCE(SUM(households), 0) FROM cities WHERE kingdom_id = ? AND province_id IS NULL",
                           (kingdom["id"],)).fetchone()[0]                   # the crown's own lands stand by the crown
    for_crown = demesne + sum(max(1.0, p["households"]) for p in lords if p["loyalty"] >= SIDE_LOYALTY)
    for_claim = sum(max(1.0, p["households"]) for p in lords if p["loyalty"] < SIDE_LOYALTY)
    if claim_province:
        for_claim += max(1.0, conn.execute("SELECT COALESCE(SUM(households), 0) FROM cities WHERE province_id = ?",
                                           (claim_province["id"],)).fetchone()[0])
    losers = [p for p in lords if (p["loyalty"] >= SIDE_LOYALTY) == (for_claim > for_crown)]
    for p in losers:
        conn.execute("UPDATE provinces SET loyalty = MAX(0, loyalty - ?) WHERE id = ?", (LOSER_LOYALTY, p["id"]))
    if for_claim > for_crown:
        crown(conn, kingdom, claimant, heir)
        return "crowned"
    bold, mercy = claimant["boldness"] or 0, claimant["mercy"] or 0
    if bold >= EXILE_BOLDNESS and mercy < PLOT_MERCY and random.random() < PLOT_CHANCE:
        from Data.Events.events import death_event
        from Data.Database.repository import get_npc
        conn.execute("UPDATE npcs SET alive = 0 WHERE id = ?", (heir["id"],))
        death_event(conn, get_npc(conn, heir["id"]), "murdered in a plot", kingdom_id=kingdom["id"])
        return "murdered"
    if bold >= EXILE_BOLDNESS:
        exile(conn, kingdom, claimant)
        return "exiled"
    return "withdrew"


def war(conn, kingdom, heir, claimant, claim_province):
    """The war of succession. Returns 'claimant won' or 'crown won'."""
    from Data.Combat.combat import get_combat_power, apply_casualties
    from Data.Events.events import death_event
    from Data.Government.rebellion import sides, crush
    from Data.Combat.nemesis import record_battle
    lords = [p for p in _lords(conn, kingdom["id"]) if p["lord_id"] != heir["id"]]
    rebels = [p for p in lords if p["loyalty"] < SIDE_LOYALTY or p["lord_id"] == claimant["id"]]
    if claim_province and claim_province["id"] not in {p["id"] for p in rebels}:
        rebels.append(dict(claim_province))
    if rebels:
        rebel_soldiers, crown_soldiers, _, mercenaries = sides(conn, rebels)
    else:
        rebel_soldiers, mercenaries = [], 0
        from Data.Government.rebellion import _soldiers
        from Data.Government.armies import get_army_npc_ids
        crown_soldiers = _soldiers(conn, "c.kingdom_id = ?", (kingdom["id"],), get_army_npc_ids(conn, kingdom["world_id"]))
    for p in rebels:
        conn.execute("UPDATE provinces SET treasury = treasury - ? WHERE id = ?",
                     (int(p["treasury"] // MERCENARY_COST) * MERCENARY_COST, p["id"]))
    crown_power = sum(get_combat_power(n) for n in crown_soldiers)
    average = crown_power / len(crown_soldiers) if crown_soldiers else 1
    rebel_power = sum(get_combat_power(n) for n in rebel_soldiers) + mercenaries * average + get_combat_power(claimant)
    claimant_wins = rebel_power > 0 and (crown_power <= 0 or random.random() < rebel_power / (rebel_power + crown_power))
    rebel_dead = apply_casualties(conn, rebel_soldiers, won=claimant_wins)
    crown_dead = apply_casualties(conn, crown_soldiers, won=not claimant_wins)
    record_battle(conn, rebel_soldiers, rebel_dead, crown_soldiers, crown_dead, context="civil war", kingdom_id=kingdom["id"])
    for npc in rebel_dead + crown_dead:
        death_event(conn, npc, "War of Succession", kingdom_id=kingdom["id"])
    if claimant_wins:
        crown(conn, kingdom, claimant, heir)
        for p in lords:
            if p not in rebels:
                conn.execute("UPDATE provinces SET loyalty = MAX(0, loyalty - ?) WHERE id = ?", (LOSER_LOYALTY, p["id"]))
        if (claimant["mercy"] or 0) < EXECUTE_MERCY:
            execute(conn, kingdom, heir)
        else:
            exile(conn, kingdom, heir)
        return "claimant won"
    from Data.Database.repository import get_npc
    for p in rebels:
        lord = get_npc(conn, p["lord_id"]) if p["lord_id"] else None
        if lord and lord["id"] != claimant["id"]:
            crush(conn, p, kingdom, lord)
    if claim_province:
        crush(conn, claim_province, kingdom, claimant)
    else:
        execute(conn, kingdom, claimant)
    return "crown won"


def contest(conn, kingdom_id, heir, regent=None, previous=None, relation=None):
    """A crown has just passed to `heir`: is it disputed, and how does it end? Returns a report, or None."""
    from Data.Database.repository import get_npc
    kingdom = dict(conn.execute("SELECT * FROM kingdoms WHERE id = ?", (kingdom_id,)).fetchone())
    if kingdom.get("type") not in DISPUTED_KINDS or not heir:
        return None
    points, relation = weakness(conn, heir, regent, previous, relation)
    if points <= 0:
        return None
    claimant, claim_province = rival(conn, kingdom_id, heir, regent, previous)
    if not claimant:
        return None
    chance = CRISIS_PER_POINT * points * (1 + BOLD_SWAY * (claimant["boldness"] or 0) / 100)
    if random.random() >= chance:
        return None
    heir = get_npc(conn, heir["id"])
    world_id = kingdom["world_id"]
    fields = {"ruler": heir["name"], "rival": claimant["name"],
              "county": claim_province["name"] if claim_province else "no county"}
    _event(conn, f"{claimant['name']} disputed the crown of {kingdom['name']} with {heir['name']}.", kingdom_id)
    _chronicle(conn, world_id, kingdom, "Succession Disputed", fields, (heir["id"], claimant["id"]))
    if points >= WAR_AT:
        outcome = war(conn, kingdom, heir, claimant, claim_province)
        key = "Succession War (claimant won)" if outcome == "claimant won" else "Succession War (crown won)"
    else:
        outcome = politics(conn, kingdom, heir, claimant, claim_province)
        key = {"crowned": "Claimant Crowned", "exiled": "Claimant Exiled", "murdered": "Heir Murdered",
               "withdrew": "Claim Withdrawn"}[outcome]
    _event(conn, {"claimant won": f"{claimant['name']} won the war of succession and took the crown of {kingdom['name']}.",
                  "crown won": f"The war of succession in {kingdom['name']} ended with {claimant['name']}'s fall.",
                  "crowned": f"The lords of {kingdom['name']} set {heir['name']} aside and crowned {claimant['name']}.",
                  "exiled": f"{claimant['name']} lost the dispute for the crown of {kingdom['name']} and went into exile.",
                  "murdered": f"{heir['name']} was murdered in a plot as {claimant['name']}'s claim failed.",
                  "withdrew": f"{claimant['name']} bowed to {heir['name']} and withdrew the claim."}[outcome], kingdom_id)
    _chronicle(conn, world_id, kingdom, key, fields, (heir["id"], claimant["id"], outcome))
    conn.commit()
    return {"kingdom": kingdom_id, "heir": heir["id"], "claimant": claimant["id"], "weakness": points,
            "relation": relation, "war": points >= WAR_AT, "outcome": outcome}
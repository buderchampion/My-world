import random
from collections import defaultdict
from Data.Database.repository import get_city_npcs, set_relationship

FRIEND_CHANCE = 0.4
RIVAL_CHANCE = 0.15
# anything left over becomes a neutral "Acquaintance"

# How much a spouse's death shifts the survivor's stress/happiness --
# deliberately much larger than an ordinary combat morale impact (see
# Data.Combat.combat.MORALE_IMPACT), since losing someone you married
# should outweigh a nearby raid or a battle you weren't even in.
GRIEF_STRESS_IMPACT = 25
GRIEF_HAPPINESS_IMPACT = -30


def apply_grief(conn, deceased_npc):
    """
    If the NPC who just died had a living spouse, the survivor takes
    a real, lasting stress/happiness hit, and is freed to eventually
    remarry (nothing previously cleared spouse_id on death, which
    silently left every widow/widower permanently "married" to a
    dead person and unable to remarry -- form_families requires
    spouse is None).

    Safe to call for every death, not just combat ones -- does
    nothing if the deceased had no spouse, or if the spouse is
    already dead too. Returns True if grief was actually applied.
    """

    spouse_id = deceased_npc.get("spouse")

    if not spouse_id:
        return False

    from Data.Database.repository import get_npc, save_npc

    spouse = get_npc(conn, spouse_id)

    if not spouse["alive"]:
        return False

    spouse["needs"]["stress"] = max(0, min(100, spouse["needs"]["stress"] + GRIEF_STRESS_IMPACT))
    spouse["needs"]["happiness"] = max(0, min(100, spouse["needs"]["happiness"] + GRIEF_HAPPINESS_IMPACT))
    spouse["spouse"] = None

    save_npc(conn, spouse)

    return True


def roll_relationship():

    roll = random.random()

    if roll < FRIEND_CHANCE:
        return "Friend", random.randint(40, 90)

    if roll < FRIEND_CHANCE + RIVAL_CHANCE:
        return "Rival", random.randint(-80, -30)

    return "Acquaintance", random.randint(-10, 10)


# Reputation shift for an EXISTING relationship whose both people
# fought on the SAME side of a battle -- shared danger deepens a
# bond, whether or not either of them actually survived it.
BATTLE_SAME_SIDE_SHIFT = 12

# Reputation shift for two people who already knew each other but
# ended up on OPPOSITE sides of the same battle -- a real rupture.
# Bigger in magnitude than the same-side bonus, since discovering
# someone you know took up arms against you cuts deeper than simply
# not having fought beside them.
BATTLE_OPPOSITE_SIDE_SHIFT = -25


def classify_relationship_type(reputation):
    """
    The reputation bands a non-Spouse relationship's TYPE gets
    reclassified into after something shifts its number -- mirrors
    roll_relationship's own bands, so a relationship that drifts far
    enough actually changes what it's called (an Acquaintance who
    fights beside you enough times becomes a real Friend; a Friend
    who ends up your enemy in a civil war can curdle into a Rival).
    """

    if reputation >= 40:
        return "Friend"

    if reputation <= -30:
        return "Rival"

    return "Acquaintance"


def evolve_relationships_from_battle(conn, side_a, side_b, same_side_origin, opposite_side_origin):
    """
    After a battle, any EXISTING relationship where both people were
    combatants gets a real shift -- strengthened if they fought on
    the same side, damaged if they ended up opposed. Only touches
    relationships that already existed; never creates new ones.

    Efficient even for a battle with hundreds of combatants: rather
    than checking every possible pair (which would explode --
    hundreds of soldiers is tens of thousands of pairs), this checks
    each combatant's OWN existing relationships (already small,
    already loaded on their npc dict by get_npc) against who else
    fought in this same battle. A person typically has a handful of
    relationships, not hundreds, so this stays cheap regardless of
    how large the battle itself is.

    side_a and side_b are lists of NPC dicts -- whichever roles make
    sense for the caller (attackers/defenders, the two kingdoms'
    militaries, loyalists/rebels). Spouse relationships shift in
    reputation like anything else but never get reclassified to a
    different type -- one battle shouldn't unravel a marriage.

    Returns how many relationships were actually touched.
    """

    side_of = {npc["id"]: "a" for npc in side_a}
    side_of.update({npc["id"]: "b" for npc in side_b})

    processed_pairs = set()

    for npc in side_a + side_b:

        for other_id_str, rel_info in npc.get("relationships", {}).items():

            other_id = int(other_id_str)

            if other_id not in side_of:
                continue

            pair = tuple(sorted((npc["id"], other_id)))

            if pair in processed_pairs:
                continue

            processed_pairs.add(pair)

            same_side = side_of[npc["id"]] == side_of[other_id]

            shift = BATTLE_SAME_SIDE_SHIFT if same_side else BATTLE_OPPOSITE_SIDE_SHIFT
            origin = same_side_origin if same_side else opposite_side_origin

            new_reputation = max(-100, min(100, rel_info["reputation"] + shift))

            new_type = (
                rel_info["type"] if rel_info["type"] == "Spouse"
                else classify_relationship_type(new_reputation)
            )

            set_relationship(conn, pair[0], pair[1], new_type, new_reputation, origin=origin)

    return len(processed_pairs)

BATTLE_COMPANIONS_SAMPLED = 2
BATTLE_NEW_BOND_CHANCE = 0.25
BATTLE_BOND_FRIEND_CHANCE = 0.75
BATTLE_BOND_RIVAL_CHANCE = 0.10

def roll_battle_bond():

    roll = random.random()

    if roll < BATTLE_BOND_FRIEND_CHANCE:
        return "Friend", random.randint(40, 90)

    if roll < BATTLE_BOND_FRIEND_CHANCE + BATTLE_BOND_RIVAL_CHANCE:
        return "Rival", random.randint(-80, -30)

    return "Acquaintance", random.randint(-10, 10)


def form_relationships_from_battle(conn, side_a, side_b, origin):
    """
    New relationships that can form purely from having fought
    alongside each other -- people who didn't already know each
    other, coming out of a battle with a real bond a meaningful
    fraction of the time. This is the counterpart to
    evolve_relationships_from_battle: that function only ever
    changes relationships that already existed, this one only ever
    creates ones that didn't.

    Only within a side, never across -- you don't make a new friend
    out of a stranger who was actively trying to kill you. New
    negative feelings toward total strangers on the other side are
    what the population-wide morale hit already covers (see
    MORALE_IMPACT) -- this function isn't the place for that.

    Efficient regardless of battle size: each combatant considers
    only a small, fixed number of random companions from their own
    side (BATTLE_COMPANIONS_SAMPLED) rather than every other
    combatant, so this stays linear even for hundreds of soldiers.

    Returns how many new relationships were actually formed.
    """

    formed = 0

    for side in (side_a, side_b):

        if len(side) < 2:
            continue

        for npc in side:

            existing = npc.get("relationships", {})

            sample_size = min(BATTLE_COMPANIONS_SAMPLED, len(side) - 1)
            companions = random.sample([other for other in side if other["id"] != npc["id"]], sample_size)

            for companion in companions:

                if str(companion["id"]) in existing:
                    continue

                if npc["id"] > companion["id"]:
                    # only process each unordered pair once, from
                    # whichever side happens to sample it with the
                    # lower id first
                    continue

                if random.random() >= BATTLE_NEW_BOND_CHANCE:
                    continue

                rel_type, reputation = roll_battle_bond()

                set_relationship(conn, npc["id"], companion["id"], rel_type, reputation, origin=origin)

                formed += 1

    return formed

def link(conn, npc_a, npc_b):

    # Already linked -- leave it alone. Without this check, calling
    # form_relationships more than once (e.g. every month, once wired
    # into the clock) would re-roll and could flip an existing
    # friendship into a rivalry for no reason.
    if str(npc_b["id"]) in npc_a.get("relationships", {}):
        return

    rel_type, reputation = roll_relationship()

    set_relationship(conn, npc_a["id"], npc_b["id"], rel_type, reputation, origin="became coworkers")


def form_relationships(conn, city):
    """
    Loads every NPC in this city and forms a relationship between every
    pair of coworkers (same workplace). Not called automatically.
    """

    npcs = get_city_npcs(conn, city["id"])

    by_workplace = defaultdict(list)

    for npc in npcs:

        if npc["workplace"] is not None:
            by_workplace[npc["workplace"]].append(npc)

    for coworkers in by_workplace.values():

        for i in range(len(coworkers)):
            for j in range(i + 1, len(coworkers)):
                link(conn, coworkers[i], coworkers[j])

    conn.commit()

    return city
def marriage_event(conn, npc_a, npc_b):

    from Data.Database.repository import create_event

    return create_event(
        conn,
        "Wedding",
        f"{npc_a['name']} and {npc_b['name']} were married.",
        participants=[npc_a["id"], npc_b["id"]],
        city_id=npc_a.get("city_id")
    )


def death_event(conn, npc, cause, city_id=None, kingdom_id=None):

    from Data.Database.repository import create_event

    return create_event(
        conn,
        "Death",
        f"{npc['name']} died ({cause}).",
        participants=[npc["id"]],
        city_id=city_id if city_id is not None else npc.get("city_id"),
        kingdom_id=kingdom_id
    )


def coronation_event(conn, ruler, city):

    from Data.Database.repository import create_event

    return create_event(
        conn,
        "Coronation",
        f"{ruler['name']} was crowned {ruler['title']} of {city['name']}.",
        participants=[ruler["id"]],
        city_id=city["id"]
    )


def raid_event(conn, city, result, participants=None):

    from Data.Database.repository import create_event

    if result["outcome"] == "attackers_win":
        description = f"Bandits raided {city['name']} and made off with plunder."
    elif result["outcome"] == "defenders_win":
        description = f"{city['name']}'s defenders repelled a bandit raid."
    else:
        description = f"A planned raid on {city['name']} fizzled -- no one showed up to fight."

    return create_event(conn, "Raid", description, participants=participants, city_id=city["id"])

def caravan_raided_event(conn, caravan, origin_name, destination_name):

    from Data.Database.repository import create_event

    description = (
        f"A caravan carrying {caravan['amount']:g} {caravan['resource']} from "
        f"{origin_name} to {destination_name} was lost to raiders."
    )

    return create_event(conn, "Caravan Raided", description, kingdom_id=caravan["buyer_kingdom_id"])

def civil_war_event(conn, kingdom, result, participants=None):

    from Data.Database.repository import create_event

    if result["outcome"] == "rebels_win":
        description = (
            f"Civil war erupted in {kingdom['name']} -- years of hardship boiled over, and the "
            f"uprising overthrew the ruling government."
        )
    else:
        description = (
            f"Civil war erupted in {kingdom['name']} -- the uprising was crushed, but the "
            f"unrest that caused it remains."
        )

    return create_event(conn, "Civil War", description, participants=participants, kingdom_id=kingdom["id"])

def war_event(conn, attacker_kingdom, defender_kingdom, result, participants=None):

    from Data.Database.repository import create_event

    if result["outcome"] == "attacker_wins":

        description = f"{attacker_kingdom['name']} defeated {defender_kingdom['name']} in battle"

        if result.get("captured_city"):
            description += f" and captured {result['captured_city']}"
            if result.get("treasury_spoils"):
                description += f", seizing {result['treasury_spoils']:g} gold in spoils."
            else:
                description += "."
        else:
            description += "."

    elif result["outcome"] == "defender_wins":
        description = f"{defender_kingdom['name']} repelled an invasion from {attacker_kingdom['name']}."

    else:
        description = f"{attacker_kingdom['name']} and {defender_kingdom['name']} mobilized but never engaged."

    return create_event(
        conn,
        "War",
        description,
        participants=participants,
        kingdom_id=attacker_kingdom["id"]
    )

def marriage_event(conn, npc_a, npc_b):

    from Data.Database.repository import create_event

    return create_event(
        conn,
        "Wedding",
        f"{npc_a['name']} and {npc_b['name']} were married.",
        participants=[npc_a["id"], npc_b["id"]],
        city_id=npc_a.get("city_id")
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


def raid_event(conn, city, result):

    from Data.Database.repository import create_event

    if result["outcome"] == "attackers_win":
        description = f"Bandits raided {city['name']} and made off with plunder."
    elif result["outcome"] == "defenders_win":
        description = f"{city['name']}'s defenders repelled a bandit raid."
    else:
        description = f"A planned raid on {city['name']} fizzled -- no one showed up to fight."

    return create_event(conn, "Raid", description, city_id=city["id"])


def war_event(conn, attacker_kingdom, defender_kingdom, result):

    from Data.Database.repository import create_event

    if result["outcome"] == "attacker_wins":

        description = f"{attacker_kingdom['name']} defeated {defender_kingdom['name']} in battle"

        if result.get("captured_city"):
            description += f" and captured {result['captured_city']}."
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
        participants=[],
        kingdom_id=attacker_kingdom["id"]
    )

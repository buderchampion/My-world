def get_next_event_id():

    from Data.counters import next_id

    return next_id("event")


def create_event(event_type, description, participants=None, location=None):

    return {
        "id": get_next_event_id(),
        "type": event_type,
        "description": description,
        "participants": participants or [],
        "location": location,
        "day": None
    }


def marriage_event(npc_a, npc_b):

    return create_event(
        "Wedding",
        f"{npc_a['name']} and {npc_b['name']} were married.",
        participants=[npc_a["id"], npc_b["id"]],
        location=npc_a.get("home")
    )


def coronation_event(ruler, city):

    return create_event(
        "Coronation",
        f"{ruler['name']} was crowned {ruler['title']} of {city['name']}.",
        participants=[ruler["id"]],
        location=city["id"]
    )


def raid_event(city, result):

    if result["outcome"] == "attackers_win":
        description = f"Bandits raided {city['name']} and made off with plunder."
    elif result["outcome"] == "defenders_win":
        description = f"{city['name']}'s defenders repelled a bandit raid."
    else:
        description = f"A planned raid on {city['name']} fizzled -- no one showed up to fight."

    return create_event("Raid", description, location=city["id"])


def war_event(attacker_kingdom, defender_kingdom, result):

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
        "War",
        description,
        participants=[attacker_kingdom["id"], defender_kingdom["id"]]
    )
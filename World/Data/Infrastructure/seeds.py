import random
import secrets

# The Seed Manager. Every world has a seed: the one it was created with, or
# one chosen for it and recorded. The same seed generates the same world --
# its map, kingdoms, towns and people -- so a world can be shared or
# rebuilt from its number alone. (What happens after generation stays
# unpredictable: the seed guarantees the starting world, not its history.)

SEED_DIGITS = 6


def new_seed():
    """A fresh seed, chosen from the operating system's randomness so it never repeats the last one."""
    return secrets.randbelow(9 * 10 ** (SEED_DIGITS - 1)) + 10 ** (SEED_DIGITS - 1)


def parse_seed(text):
    """A seed from what someone typed: digits are used as they are; any other text is turned into a number
    the same way every time ("Middle-earth" is always the same world)."""
    text = str(text).strip()
    if text.isdigit():
        return int(text)
    total = 0
    for ch in text:                                   # a stable hash (Python's own hash() changes between runs)
        total = (total * 131 + ord(ch)) % (10 ** 12)
    return total


def apply_seed(seed):
    """Seeds the random generator every part of the world draws from."""
    random.seed(seed)
    return seed


def world_seed(conn, world_id):
    row = conn.execute("SELECT seed FROM worlds WHERE id = ?", (world_id,)).fetchone()
    return row["seed"] if row else None
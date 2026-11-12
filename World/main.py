import sys

from Data.Infrastructure.seeds import parse_seed
from Data.World.world_generator import generate_world

# python main.py           a new world from a random seed (recorded)
# python main.py 4417      the world of seed 4417 -- the same every time (a word works too)
world = generate_world(parse_seed(" ".join(sys.argv[1:])) if len(sys.argv) > 1 else None)
print(f"Generated '{world['name']}' from seed {world['seed']}.")
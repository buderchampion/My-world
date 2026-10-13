import json
from pathlib import Path

COUNTERS_FILE = Path(__file__).resolve().parent / "Counters.json"

def _load():

    if not COUNTERS_FILE.exists():
        return{}

    with open(COUNTERS_FILE, "r") as f:
        content = f.read().strip()

    if not content:
        return{}

    return json.loads(content)

def _save(counters):

    with open(COUNTERS_FILE, "w") as f:
        json.dump(counters, f, indent=4)

def next_id(name, folder=None, pattern=None):

    counters = _load()

    if name not in counters:

        highest = 0

        if folder is not None and pattern is not None:

            files = list(Path(folder).glob(pattern))

            if files:
                highest = max(int(f.stem.split("_")[1]) for f in files)

        counters[name] = highest

    counters[name] += 1

    _save(counters)

    return counters[name]
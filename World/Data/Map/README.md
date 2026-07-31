# World SQLite Map Viewer

A native Pygame map browser for the SQLite world. It does not serve or require
HTML.  `world.db` remains your simulation database; the viewer keeps its own
`map.db` for persistent map seed and city-position data.

## Run

Install the Pygame-compatible package in the Python environment you use for
the project, then run:

```powershell
py -m pip install -r requirements.txt
py world_map.py
```

The viewer automatically uses `Data/Database/world.db` and creates its own
`Data/Map/map.db` on first launch.

To use a different database, pass it explicitly:

```powershell
py world_map.py "C:\path\to\world.db"
```

## Controls

- Click a city sprite to inspect that city's population, buildings, ruler,
  resources, and available quests.
- Drag the map to pan.
- Use the mouse wheel to zoom.
- Press `R` to reset the view; `Esc` or `Q` exits.

The supplied database schema contains no geographic-coordinate fields. The
viewer therefore creates consistent map positions from existing kingdom/city
IDs and opens the database in read-only mode, so viewing cannot change your
world data.

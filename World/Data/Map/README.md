# World map

A Pygame window onto the world: terrain, climate and rivers; kingdoms, their
counties and the neutral ground between them; towns, villages and roads.
It reads the world **read-only**, so viewing can never change your world.

## Run

```powershell
py -m pip install -r requirements.txt
py world_map.py
```

It opens `Data/Database/world.db`. To view another world (a save, say):

```powershell
py world_map.py "..\..\Saves\my-save.db"
```

## Controls

- Drag to pan; mouse wheel (or `+` and `-`) to zoom.
- `R` resets the view; `F5` reloads the world; `Esc` or `Q` quits.

## Files

- `map_model.py` -- reads the world (no Pygame).
- `map_render.py` -- draws the layers onto any surface.
- `world_map.py` -- the window.
- `old/` -- the Phase 0/1 viewer, from before the world had geography.
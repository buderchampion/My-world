"""Persistent map-only data.

This database intentionally lives beside the Pygame viewer.  It never writes
to the simulation's ``world.db``; it only stores display data that the world
schema does not yet model (map seed, city coordinates, future style data).
"""

from __future__ import annotations

import math
import random
import sqlite3
import colorsys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


MAP_WIDTH = 2_400
MAP_HEIGHT = 1_600


@dataclass(frozen=True)
class MapLayout:
    world_id: int
    seed: int
    width: int
    height: int


class MapStore:
    def __init__(self, database_path: Path) -> None:
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS map_layouts (
                world_id INTEGER PRIMARY KEY,
                seed INTEGER NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS map_city_positions (
                city_id INTEGER PRIMARY KEY,
                world_id INTEGER NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_map_city_positions_world
                ON map_city_positions(world_id);
            CREATE TABLE IF NOT EXISTS map_kingdom_styles (
                kingdom_id INTEGER PRIMARY KEY,
                color_r INTEGER NOT NULL,
                color_g INTEGER NOT NULL,
                color_b INTEGER NOT NULL
            );
            """
        )
        self.connection.commit()

    def get_layout(self, world_id: int) -> MapLayout:
        row = self.connection.execute(
            "SELECT * FROM map_layouts WHERE world_id = ?", (world_id,)
        ).fetchone()
        if row is None:
            # Deterministic first seed, persisted so future procedural settings
            # do not rearrange an already-created map.
            seed = (world_id * 1_103_515_245 + 12_345) & 0x7FFF_FFFF
            self.connection.execute(
                "INSERT INTO map_layouts (world_id, seed, width, height) VALUES (?, ?, ?, ?)",
                (world_id, seed, MAP_WIDTH, MAP_HEIGHT),
            )
            self.connection.commit()
            return MapLayout(world_id, seed, MAP_WIDTH, MAP_HEIGHT)
        if row["width"] != MAP_WIDTH or row["height"] != MAP_HEIGHT:
            # Preserve existing city placements when the display world grows.
            # This affects only map.db, never the simulation database.
            scale_x = MAP_WIDTH / row["width"]
            scale_y = MAP_HEIGHT / row["height"]
            self.connection.execute(
                "UPDATE map_city_positions SET x = x * ?, y = y * ? WHERE world_id = ?",
                (scale_x, scale_y, world_id),
            )
            self.connection.execute(
                "UPDATE map_layouts SET width = ?, height = ? WHERE world_id = ?",
                (MAP_WIDTH, MAP_HEIGHT, world_id),
            )
            self.connection.commit()
            return MapLayout(world_id, row["seed"], MAP_WIDTH, MAP_HEIGHT)
        return MapLayout(row["world_id"], row["seed"], row["width"], row["height"])

    def ensure_city_positions(
        self, world_id: int, cities: Iterable[sqlite3.Row], layout: MapLayout
    ) -> dict[int, tuple[float, float]]:
        cities = list(cities)
        existing = {
            row["city_id"]: (row["x"], row["y"])
            for row in self.connection.execute(
                "SELECT city_id, x, y FROM map_city_positions WHERE world_id = ?", (world_id,)
            )
        }
        missing = [row for row in cities if row["id"] not in existing]
        if not missing:
            return existing

        groups: dict[int, list[sqlite3.Row]] = {}
        for city in missing:
            groups.setdefault(city["kingdom_id"], []).append(city)
        kingdom_ids = sorted(groups)
        for kingdom_ordinal, kingdom_id in enumerate(kingdom_ids):
            group = groups[kingdom_id]
            kingdom_angle = math.tau * kingdom_ordinal / max(len(kingdom_ids), 1) - math.pi / 2
            center_x = layout.width * (0.5 + math.cos(kingdom_angle) * 0.28)
            center_y = layout.height * (0.5 + math.sin(kingdom_angle) * 0.22)
            rng = random.Random(layout.seed ^ kingdom_id)
            for index, city in enumerate(group):
                if index == 0:
                    x, y = center_x, center_y
                else:
                    angle = math.tau * index / len(group) + rng.uniform(-0.32, 0.32)
                    radius = 110 + (index % 3) * 52 + rng.uniform(-18, 18)
                    x = center_x + math.cos(angle) * radius
                    y = center_y + math.sin(angle) * radius
                x = max(72, min(layout.width - 72, x))
                y = max(72, min(layout.height - 72, y))
                self.connection.execute(
                    "INSERT INTO map_city_positions (city_id, world_id, x, y) VALUES (?, ?, ?, ?)",
                    (city["id"], world_id, x, y),
                )
                existing[city["id"]] = (x, y)
        self.connection.commit()
        return existing

    def kingdom_colors(self, kingdom_ids: Iterable[int]) -> dict[int, tuple[int, int, int]]:
        """Return durable, visually distinct political colors for kingdoms.

        Hues are assigned in golden-ratio steps (0.618... apart, mod 1) in
        the order each kingdom is first seen, rather than as an
        independent random hue per kingdom id. Independent random hues
        can coincidentally land close together (this happened in testing
        -- two kingdoms both came out a similar green); golden-ratio
        stepping guarantees good separation no matter how many kingdoms
        exist. Already-assigned colors are still read from
        map_kingdom_styles first, so existing worlds keep their colors.
        """
        result: dict[int, tuple[int, int, int]] = {}
        existing_count = self.connection.execute(
            "SELECT COUNT(*) as n FROM map_kingdom_styles"
        ).fetchone()["n"]
        next_ordinal = existing_count
        for kingdom_id in sorted(set(kingdom_ids)):
            row = self.connection.execute(
                "SELECT color_r, color_g, color_b FROM map_kingdom_styles WHERE kingdom_id = ?",
                (kingdom_id,),
            ).fetchone()
            if row is None:
                hue = (next_ordinal * 0.618033988749895) % 1.0
                next_ordinal += 1
                red, green, blue = colorsys.hsv_to_rgb(hue, 0.62, 0.92)
                color = (round(red * 255), round(green * 255), round(blue * 255))
                self.connection.execute(
                    "INSERT INTO map_kingdom_styles (kingdom_id, color_r, color_g, color_b) VALUES (?, ?, ?, ?)",
                    (kingdom_id, *color),
                )
            else:
                color = (row["color_r"], row["color_g"], row["color_b"])
            result[kingdom_id] = color
        self.connection.commit()
        return result

    def close(self) -> None:
        self.connection.close()

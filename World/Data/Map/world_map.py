"""Phase 0/1 native Pygame world map: persistent layout plus terrain."""

from __future__ import annotations

import math
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import pygame

from map_data import MapLayout, MapStore


WINDOW_SIZE = (1280, 760)
SIDEBAR_WIDTH = 390
MAP_DIR = Path(__file__).resolve().parent
DATA_DIR = MAP_DIR.parent
WORLD_DATABASE = DATA_DIR / "Database" / "world.db"
MAP_DATABASE = MAP_DIR / "map.db"

TEXT = (239, 242, 234)
MUTED = (166, 183, 185)
PANEL = (19, 29, 39)
PANEL_EDGE = (74, 105, 119)
GOLD = (239, 196, 84)
CITY_COLORS = {"Capital": (242, 190, 84), "City": (226, 114, 91), "Town": (135, 205, 128), "Village": (126, 173, 221)}


@dataclass(frozen=True)
class City:
    id: int
    kingdom_id: int
    name: str
    kind: str
    population: int
    kingdom_name: str
    is_capital: bool
    x: float
    y: float


@dataclass(frozen=True)
class Selection:
    """What's currently selected -- a city (has a map sprite), a kingdom
    (selected via its name label, no sprite of its own), or a building
    (selected via a row in the sidebar once its city is showing, since
    buildings have no map position of their own)."""
    kind: str  # "city", "kingdom", or "building"
    id: int


class WorldData:
    def __init__(self, world_database: Path, map_database: Path) -> None:
        self.connection = sqlite3.connect(f"file:{world_database.as_posix()}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        world = self.connection.execute("SELECT id, name FROM worlds ORDER BY id DESC LIMIT 1").fetchone()
        if world is None:
            raise RuntimeError("The world database has no generated worlds yet.")
        self.world_id, self.world_name = world["id"], world["name"]
        self.map_store = MapStore(map_database)
        self.layout = self.map_store.get_layout(self.world_id)
        rows = self.connection.execute(
            """
            SELECT c.id, c.kingdom_id, c.name, c.type, c.population, k.name AS kingdom_name,
                   CASE WHEN k.capital_city_id = c.id THEN 1 ELSE 0 END AS is_capital
            FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
            WHERE k.world_id = ? ORDER BY c.kingdom_id, c.id
            """, (self.world_id,)
        ).fetchall()
        positions = self.map_store.ensure_city_positions(self.world_id, rows, self.layout)
        self.cities = [City(r["id"], r["kingdom_id"], r["name"], r["type"], r["population"], r["kingdom_name"], bool(r["is_capital"]), *positions[r["id"]]) for r in rows]
        self.kingdom_colors = self.map_store.kingdom_colors(city.kingdom_id for city in self.cities)
        self.kingdom_names = {city.kingdom_id: city.kingdom_name for city in self.cities}

    def city_details(self, city_id: int) -> dict[str, object]:
        city = self.connection.execute(
            """
            SELECT c.*, k.name AS kingdom_name, r.name AS ruler_name,
                   (SELECT COUNT(*) FROM npcs n WHERE n.city_id = c.id AND n.alive = 1) AS live_population,
                   (SELECT COUNT(*) FROM buildings b WHERE b.city_id = c.id) AS building_count,
                   (SELECT COUNT(*) FROM quests q WHERE q.city_id = c.id AND q.status = 'Available') AS quest_count
            FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id LEFT JOIN npcs r ON r.id = c.ruler_id
            WHERE c.id = ?
            """, (city_id,)
        ).fetchone()
        resources = self.connection.execute("SELECT resource_name, amount FROM city_resources WHERE city_id = ? ORDER BY amount DESC", (city_id,)).fetchall()
        return {"city": city, "resources": resources}

    def kingdom_details(self, kingdom_id: int) -> dict[str, object]:
        kingdom = self.connection.execute(
            """
            SELECT k.*, cap.name AS capital_name, r.name AS ruler_name,
                   (SELECT COUNT(*) FROM cities c WHERE c.kingdom_id = k.id) AS city_count,
                   (SELECT COALESCE(SUM(c.population), 0) FROM cities c WHERE c.kingdom_id = k.id) AS total_population
            FROM kingdoms k
            LEFT JOIN cities cap ON cap.id = k.capital_city_id
            LEFT JOIN npcs r ON r.id = k.ruler_id
            WHERE k.id = ?
            """, (kingdom_id,)
        ).fetchone()
        return {"kingdom": kingdom}

    def city_buildings(self, city_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT b.type, COUNT(*) AS count,
                   MIN(b.id) AS sample_building_id,
                   SUM((SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1)) AS workers,
                   SUM((SELECT COUNT(*) FROM npcs n WHERE n.home_id = b.id AND n.alive = 1)) AS residents
            FROM buildings b
            WHERE b.city_id = ?
            GROUP BY b.type
            ORDER BY count DESC
            """, (city_id,)
        ).fetchall()

    def building_details(self, building_id: int) -> dict[str, object]:
        building = self.connection.execute(
            """
            SELECT b.*, c.name AS city_name,
                   (SELECT COUNT(*) FROM npcs n WHERE n.workplace_id = b.id AND n.alive = 1) AS workers,
                   (SELECT COUNT(*) FROM npcs n WHERE n.home_id = b.id AND n.alive = 1) AS residents
            FROM buildings b JOIN cities c ON c.id = b.city_id
            WHERE b.id = ?
            """, (building_id,)
        ).fetchone()
        return {"building": building}

    def npc_details(self, npc_id: int) -> dict[str, object]:
        npc = self.connection.execute(
            """
            SELECT n.*, c.name AS city_name
            FROM npcs n JOIN cities c ON c.id = n.city_id
            WHERE n.id = ?
            """, (npc_id,)
        ).fetchone()
        return {"npc": npc}

    def search(self, query: str, limit_per_category: int = 4) -> dict[str, list[sqlite3.Row]]:
        pattern = f"%{query}%"
        kingdoms = self.connection.execute(
            "SELECT id, name FROM kingdoms WHERE world_id = ? AND name LIKE ? LIMIT ?",
            (self.world_id, pattern, limit_per_category)
        ).fetchall()
        cities = self.connection.execute(
            """
            SELECT c.id, c.name, c.type, k.name AS kingdom_name
            FROM cities c JOIN kingdoms k ON k.id = c.kingdom_id
            WHERE k.world_id = ? AND c.name LIKE ? LIMIT ?
            """, (self.world_id, pattern, limit_per_category)
        ).fetchall()
        buildings = self.connection.execute(
            """
            SELECT b.id, b.name, b.type, c.name AS city_name
            FROM buildings b JOIN cities c ON c.id = b.city_id JOIN kingdoms k ON k.id = c.kingdom_id
            WHERE k.world_id = ? AND (b.name LIKE ? OR b.type LIKE ?) LIMIT ?
            """, (self.world_id, pattern, pattern, limit_per_category)
        ).fetchall()
        npcs = self.connection.execute(
            """
            SELECT n.id, n.name, n.profession, c.name AS city_name
            FROM npcs n JOIN cities c ON c.id = n.city_id JOIN kingdoms k ON k.id = c.kingdom_id
            WHERE k.world_id = ? AND n.name LIKE ? AND n.alive = 1 LIMIT ?
            """, (self.world_id, pattern, limit_per_category)
        ).fetchall()
        return {"kingdoms": kingdoms, "cities": cities, "buildings": buildings, "npcs": npcs}

    def close(self) -> None:
        self.map_store.close()
        self.connection.close()


def hash_noise(x: int, y: int, seed: int) -> float:
    value = (x * 374761393 + y * 668265263 + seed * 69069) & 0xFFFFFFFF
    value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
    return (value ^ (value >> 16)) / 0xFFFFFFFF


def smooth(value: float) -> float:
    return value * value * (3 - 2 * value)


def value_noise(x: float, y: float, seed: int) -> float:
    x0, y0 = math.floor(x), math.floor(y)
    tx, ty = smooth(x - x0), smooth(y - y0)
    a, b = hash_noise(x0, y0, seed), hash_noise(x0 + 1, y0, seed)
    c, d = hash_noise(x0, y0 + 1, seed), hash_noise(x0 + 1, y0 + 1, seed)
    return (a + (b - a) * tx) + ((c + (d - c) * tx) - (a + (b - a) * tx)) * ty


def terrain_value(x: float, y: float, seed: int, layout: MapLayout) -> float:
    nx, ny = x / layout.width, y / layout.height
    large = value_noise(nx * 3.2, ny * 3.2, seed)
    medium = value_noise(nx * 8.5, ny * 8.5, seed + 17)
    detail = value_noise(nx * 22, ny * 22, seed + 41)
    edge_distance = min(nx, 1 - nx, ny, 1 - ny) * 2
    island = max(0, min(1, edge_distance * 1.6))
    return large * 0.48 + medium * 0.32 + detail * 0.12 + island * 0.28


def build_terrain(layout: MapLayout) -> pygame.Surface:
    """Build a cached, seeded terrain surface: ocean, land, woods, peaks and rivers."""
    surface = pygame.Surface((layout.width, layout.height))
    tile = 6
    for y in range(0, layout.height, tile):
        for x in range(0, layout.width, tile):
            height = terrain_value(x, y, layout.seed, layout)
            forest = value_noise(x / 85, y / 85, layout.seed + 83)
            if height < 0.43:
                color = (23, 73, 108) if height < 0.33 else (41, 103, 132)
            elif height < 0.48:
                color = (209, 187, 123)
            elif height > 0.79:
                color = (132, 133, 129)
            elif height > 0.71:
                color = (157, 149, 110)
            elif forest > 0.60:
                color = (42, 100, 61)
            else:
                color = (83, 139, 76)
            pygame.draw.rect(surface, color, (x, y, tile, tile))
    # Stylized rivers follow deterministic paths from interior highlands to the coast.
    river_rng = __import__("random").Random(layout.seed + 501)
    for _ in range(7):
        x, y = river_rng.randint(280, layout.width - 280), river_rng.randint(180, layout.height - 180)
        points = [(x, y)]
        for step in range(26):
            angle = math.atan2(y - layout.height / 2, x - layout.width / 2) + river_rng.uniform(-0.48, 0.48)
            x += math.cos(angle) * (14 + step * 1.2)
            y += math.sin(angle) * (14 + step * 1.2)
            points.append((x, y))
            if x < 0 or x > layout.width or y < 0 or y > layout.height:
                break
        if len(points) > 1:
            pygame.draw.lines(surface, (57, 144, 181), False, points, 3)
    return surface


# How far a kingdom's claimed territory extends from its cluster of
# cities. A kingdom's own cities never spread much past ~250 units from
# its center (see map_data.ensure_city_positions), so this comfortably
# covers a kingdom's real territory with some margin, while staying
# well short of the typical distance between two different kingdoms'
# centers -- the gap in between is what stays unclaimed.
MAX_CLAIM_RADIUS = 330
MAX_CLAIM_RADIUS_SQUARED = MAX_CLAIM_RADIUS ** 2


def build_kingdom_overlay(data: WorldData) -> tuple[pygame.Surface, dict[int, pygame.Vector2]]:
    """A coarse Voronoi political layer, anchored to capital cities.

    Tiles beyond MAX_CLAIM_RADIUS of every kingdom's anchor are left
    unclaimed (no fill), leaving a band of neutral wilderness between
    territories instead of borders always touching edge-to-edge.
    """
    layout = data.layout
    surface = pygame.Surface((layout.width, layout.height), pygame.SRCALPHA)
    anchors: dict[int, pygame.Vector2] = {}
    for city in data.cities:
        if city.is_capital or city.kingdom_id not in anchors:
            anchors[city.kingdom_id] = pygame.Vector2(city.x, city.y)
    tile = 24
    previous_row: list[int | None] | None = None
    for y in range(0, layout.height, tile):
        row: list[int | None] = []
        for x in range(0, layout.width, tile):
            center = pygame.Vector2(x + tile / 2, y + tile / 2)
            nearest_id = min(anchors, key=lambda key: center.distance_squared_to(anchors[key]))
            in_range = center.distance_squared_to(anchors[nearest_id]) <= MAX_CLAIM_RADIUS_SQUARED
            kingdom_id = nearest_id if in_range else None
            row.append(kingdom_id)
            if kingdom_id is not None:
                color = data.kingdom_colors[kingdom_id]
                pygame.draw.rect(surface, (*color, 46), (x, y, tile, tile))
            if len(row) > 1 and row[-2] != kingdom_id:
                border_side = kingdom_id if kingdom_id is not None else row[-2]
                if border_side is not None:
                    pygame.draw.line(surface, (*data.kingdom_colors[border_side], 205), (x, y), (x, y + tile), 2)
            if previous_row is not None and previous_row[len(row) - 1] != kingdom_id:
                border_side = kingdom_id if kingdom_id is not None else previous_row[len(row) - 1]
                if border_side is not None:
                    pygame.draw.line(surface, (*data.kingdom_colors[border_side], 205), (x, y), (x + tile, y), 2)
        previous_row = row
    return surface, anchors


class Camera:
    """A bounded world-space camera with eased movement and zoom."""

    EASING_SPEED = 11.0
    KEYBOARD_SPEED = 900.0
    MAX_ZOOM = 2.4

    def __init__(self, layout: MapLayout) -> None:
        self.layout = layout
        center = pygame.Vector2(layout.width / 2, layout.height / 2)
        self.center = center.copy()
        self.target_center = center.copy()
        self.zoom = self.target_zoom = 0.72

    @staticmethod
    def _minimum_zoom(viewport: pygame.Rect, layout: MapLayout) -> float:
        # Permit a little water margin around the world but never let it become
        # too small to interact with on a narrow resized window.
        return max(0.30, min(viewport.width / layout.width, viewport.height / layout.height) * 0.75)

    def clamp_target(self, viewport: pygame.Rect) -> None:
        self.target_zoom = max(self._minimum_zoom(viewport, self.layout), min(self.MAX_ZOOM, self.target_zoom))
        half_width = viewport.width / (2 * self.target_zoom)
        half_height = viewport.height / (2 * self.target_zoom)
        if half_width * 2 >= self.layout.width:
            self.target_center.x = self.layout.width / 2
        else:
            self.target_center.x = max(half_width, min(self.layout.width - half_width, self.target_center.x))
        if half_height * 2 >= self.layout.height:
            self.target_center.y = self.layout.height / 2
        else:
            self.target_center.y = max(half_height, min(self.layout.height - half_height, self.target_center.y))

    def update(self, viewport: pygame.Rect, elapsed: float) -> None:
        self.clamp_target(viewport)
        blend = 1 - math.exp(-self.EASING_SPEED * min(elapsed, 0.1))
        self.center += (self.target_center - self.center) * blend
        self.zoom += (self.target_zoom - self.zoom) * blend

    def reset(self, viewport: pygame.Rect) -> None:
        self.target_center.update(self.layout.width / 2, self.layout.height / 2)
        self.target_zoom = 0.72
        self.clamp_target(viewport)

    def center_on(self, x: float, y: float, viewport: pygame.Rect, zoom: float = 1.1) -> None:
        self.target_center.update(x, y)
        self.target_zoom = max(self.target_zoom, zoom)
        self.clamp_target(viewport)

    def world_to_screen(self, world: pygame.Vector2, viewport: pygame.Rect) -> pygame.Vector2:
        return pygame.Vector2(viewport.center) + (world - self.center) * self.zoom

    def screen_to_world(self, screen: tuple[int, int], viewport: pygame.Rect, target=False) -> pygame.Vector2:
        center = self.target_center if target else self.center
        zoom = self.target_zoom if target else self.zoom
        return (pygame.Vector2(screen) - pygame.Vector2(viewport.center)) / zoom + center

    def zoom_at(self, screen: tuple[int, int], wheel_steps: int, viewport: pygame.Rect) -> None:
        # Anchor from the rendered camera rather than its future target. This
        # keeps the pixel under the cursor stable even while a pan is easing.
        anchor = self.screen_to_world(screen, viewport)
        self.target_zoom *= 1.13 ** wheel_steps
        self.clamp_target(viewport)
        # Keep the world point under the cursor fixed as the target zoom changes.
        self.target_center = anchor - (pygame.Vector2(screen) - pygame.Vector2(viewport.center)) / self.target_zoom
        self.clamp_target(viewport)


class MapViewer:
    def __init__(self, data: WorldData) -> None:
        pygame.init()
        pygame.display.set_caption(f"World Map — {data.world_name}")
        self.screen = pygame.display.set_mode(WINDOW_SIZE, pygame.RESIZABLE)
        self.data, self.terrain = data, build_terrain(data.layout)
        self.kingdom_overlay, self.kingdom_anchors = build_kingdom_overlay(data)
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("segoeui", 18)
        self.small = pygame.font.SysFont("segoeui", 15)
        self.title = pygame.font.SysFont("segoeui", 26, bold=True)
        self.camera = Camera(data.layout)
        self.selection: Selection | None = None
        self.dragging = False
        self.terrain_cache: tuple[tuple[int, int], pygame.Surface] | None = None
        self.overlay_cache: tuple[tuple[int, int], pygame.Surface] | None = None
        # Populated fresh each frame while drawing, then consulted on click --
        # avoids computing text-layout bounds twice (once to draw, once to
        # hit-test) by reusing the rects drawing already produced.
        self.kingdom_label_rects: dict[int, pygame.Rect] = {}
        self.building_row_rects: list[tuple[pygame.Rect, int]] = []
        self.search_query = ""
        self.search_active = False
        self.search_results: dict[str, list[sqlite3.Row]] = {}
        self.search_box_rect = pygame.Rect(0, 0, 0, 0)  # positioned each frame in draw_sidebar
        self.search_result_rects: list[tuple[pygame.Rect, str, int, float, float]] = []
        self.running = True

    @property
    def map_rect(self) -> pygame.Rect:
        return pygame.Rect(0, 0, max(self.screen.get_width() - SIDEBAR_WIDTH, 360), self.screen.get_height())

    def world_to_screen(self, x: float, y: float) -> pygame.Vector2:
        return self.camera.world_to_screen(pygame.Vector2(x, y), self.map_rect)

    def screen_to_world(self, point: tuple[int, int]) -> pygame.Vector2:
        return self.camera.screen_to_world(point, self.map_rect)

    def city_radius(self, city: City) -> int:
        return max(5, min(12, 4 + int(math.log10(max(city.population, 1)) * 3)))

    def draw_text(self, text: str, pos: tuple[int, int], color=TEXT, font=None) -> pygame.Rect:
        surface = (font or self.font).render(text, True, color)
        self.screen.blit(surface, pos)
        return pygame.Rect(pos[0], pos[1], surface.get_width(), surface.get_height())

    def draw_settlement_icon(self, city: City, pos: pygame.Vector2) -> None:
        """Draw a readable, code-native icon instead of a generic dot."""
        size = max(7, round(self.city_radius(city) * (0.72 + self.camera.zoom * 0.28)))
        x, y = round(pos.x), round(pos.y)
        color = CITY_COLORS.get(city.kind, (202, 152, 213))
        outline = (18, 25, 29)
        pygame.draw.circle(self.screen, outline, (x, y), size + 3)
        if city.kind == "Village":
            # A single thatched house.
            pygame.draw.rect(self.screen, (190, 143, 82), (x - size // 2, y, size, size // 2 + 2))
            pygame.draw.polygon(self.screen, color, [(x - size // 2 - 2, y), (x, y - size // 2 - 3), (x + size // 2 + 2, y)])
        elif city.kind == "Town":
            # Two homes: visibly more developed than a village.
            for offset in (-size // 3, size // 3):
                pygame.draw.rect(self.screen, (202, 162, 98), (x + offset - size // 4, y - 1, size // 2, size // 2 + 2))
                pygame.draw.polygon(self.screen, color, [(x + offset - size // 4 - 1, y - 1), (x + offset, y - size // 2 - 3), (x + offset + size // 4 + 1, y - 1)])
        elif city.kind == "City":
            # A walled settlement with two tower caps.
            pygame.draw.rect(self.screen, color, (x - size, y - size // 2, size * 2, size + 3), border_radius=2)
            pygame.draw.rect(self.screen, outline, (x - size, y - size // 2, size * 2, size + 3), 1, border_radius=2)
            for offset in (-size // 2, size // 2):
                pygame.draw.rect(self.screen, color, (x + offset - 2, y - size, 5, size + 4))
                pygame.draw.line(self.screen, outline, (x + offset - 2, y - size), (x + offset - 2, y + 3), 1)
        else:
            # Capital: compact castle silhouette.
            pygame.draw.rect(self.screen, color, (x - size, y - size // 3, size * 2, size + 5), border_radius=1)
            for offset in (-size + 2, 0, size - 2):
                pygame.draw.rect(self.screen, color, (x + offset - 3, y - size, 7, size + 8))
            pygame.draw.rect(self.screen, outline, (x - size, y - size // 3, size * 2, size + 5), 1, border_radius=1)
        if city.is_capital:
            crown = [(x, y - size - 14), (x + 7, y - size - 6), (x + 5, y - size), (x - 5, y - size), (x - 7, y - size - 6)]
            pygame.draw.polygon(self.screen, GOLD, crown)
            pygame.draw.polygon(self.screen, (55, 43, 18), crown, 1)

    def should_label_city(self, city: City, pos: pygame.Vector2) -> bool:
        is_selected = self.selection is not None and self.selection.kind == "city" and self.selection.id == city.id
        if is_selected or pos.distance_to(pygame.mouse.get_pos()) < self.city_radius(city) + 9:
            return True
        if city.is_capital:
            return True
        thresholds = {"City": 0.50, "Town": 0.78, "Village": 1.05}
        return self.camera.zoom >= thresholds.get(city.kind, 0.50)

    def draw_map(self) -> None:
        rect, layout = self.map_rect, self.data.layout
        self.screen.fill((15, 33, 46))
        scaled_size = (max(1, round(layout.width * self.camera.zoom)), max(1, round(layout.height * self.camera.zoom)))
        if self.terrain_cache is None or self.terrain_cache[0] != scaled_size:
            self.terrain_cache = (scaled_size, pygame.transform.smoothscale(self.terrain, scaled_size))
        terrain = self.terrain_cache[1]
        if self.overlay_cache is None or self.overlay_cache[0] != scaled_size:
            self.overlay_cache = (scaled_size, pygame.transform.smoothscale(self.kingdom_overlay, scaled_size))
        overlay = self.overlay_cache[1]
        destination = self.world_to_screen(0, 0)
        previous_clip = self.screen.get_clip()
        self.screen.set_clip(rect)
        self.screen.blit(terrain, destination)
        self.screen.blit(overlay, destination)
        self.kingdom_label_rects = {}
        for kingdom_id, anchor in self.kingdom_anchors.items():
            pos = self.world_to_screen(anchor.x, anchor.y)
            is_selected = self.selection is not None and self.selection.kind == "kingdom" and self.selection.id == kingdom_id
            color = GOLD if is_selected else self.data.kingdom_colors[kingdom_id]
            label_pos = (int(pos.x + 14), int(pos.y - 32))
            self.kingdom_label_rects[kingdom_id] = self.draw_text(
                self.data.kingdom_names[kingdom_id], label_pos, color, self.small
            )
        for city in self.data.cities:
            pos, radius = self.world_to_screen(city.x, city.y), self.city_radius(city)
            city_selected = self.selection is not None and self.selection.kind == "city" and self.selection.id == city.id
            kingdom_selected = self.selection is not None and self.selection.kind == "kingdom" and self.selection.id == city.kingdom_id
            if city_selected:
                pygame.draw.circle(self.screen, GOLD, pos, radius + 7, 2)
            elif kingdom_selected and city.is_capital:
                pygame.draw.circle(self.screen, GOLD, pos, radius + 5, 1)
            self.draw_settlement_icon(city, pos)
            if self.should_label_city(city, pos):
                self.draw_text(city.name, (int(pos.x + radius + 5), int(pos.y - 10)), TEXT, self.small)
        self.screen.set_clip(previous_clip)
        self.draw_text(self.data.world_name, (42, 28), TEXT, self.title)
        self.draw_text("Settlement icons  •  Drag/WASD: move  •  Wheel: zoom  •  R: reset", (42, 62), MUTED, self.small)

    def draw_sidebar(self) -> None:
        rect = pygame.Rect(self.map_rect.right, 0, SIDEBAR_WIDTH, self.screen.get_height())
        pygame.draw.rect(self.screen, PANEL, rect)
        pygame.draw.line(self.screen, PANEL_EDGE, rect.topleft, rect.bottomleft, 2)
        x = rect.left + 24
        self.building_row_rects = []
        self.search_result_rects = []

        self.search_box_rect = pygame.Rect(x, 18, SIDEBAR_WIDTH - 48, 34)
        box_color = GOLD if self.search_active else PANEL_EDGE
        pygame.draw.rect(self.screen, box_color, self.search_box_rect, 2, border_radius=5)
        if self.search_query:
            label, label_color = self.search_query, TEXT
        elif self.search_active:
            label, label_color = "", TEXT
        else:
            label, label_color = "Search kingdoms, cities, buildings, NPCs...", MUTED
        if self.search_active:
            label += "|"
        self.draw_text(label, (self.search_box_rect.left + 10, self.search_box_rect.top + 8), label_color, self.small)

        y = 76

        if self.search_query:
            categories = [
                ("Kingdoms", "kingdom", self.search_results.get("kingdoms", [])),
                ("Cities", "city", self.search_results.get("cities", [])),
                ("Buildings", "building", self.search_results.get("buildings", [])),
                ("NPCs", "npc", self.search_results.get("npcs", [])),
            ]
            any_results = any(rows for _, _, rows in categories)
            if not any_results:
                self.draw_text("No matches.", (x, y), MUTED, self.small)
                return
            for heading, kind, rows in categories:
                if not rows:
                    continue
                self.draw_text(heading, (x, y), TEXT, self.font)
                y += 30
                for row in rows:
                    row_rect = pygame.Rect(x - 6, y - 3, SIDEBAR_WIDTH - 36, 24)
                    if row_rect.collidepoint(pygame.mouse.get_pos()):
                        pygame.draw.rect(self.screen, PANEL_EDGE, row_rect, border_radius=3)
                    if kind == "kingdom":
                        self.draw_text(row["name"], (x, y), MUTED, self.small)
                        world_x, world_y = self.kingdom_anchors[row["id"]].x, self.kingdom_anchors[row["id"]].y
                    elif kind == "city":
                        self.draw_text(f"{row['name']}  ({row['kingdom_name']})", (x, y), MUTED, self.small)
                        city_match = next(c for c in self.data.cities if c.id == row["id"])
                        world_x, world_y = city_match.x, city_match.y
                    elif kind == "building":
                        self.draw_text(f"{row['name']}  —  {row['city_name']}", (x, y), MUTED, self.small)
                        city_match = next(c for c in self.data.cities if c.name == row["city_name"])
                        world_x, world_y = city_match.x, city_match.y
                    else:  # npc
                        self.draw_text(f"{row['name']}  ({row['profession']})  —  {row['city_name']}", (x, y), MUTED, self.small)
                        city_match = next(c for c in self.data.cities if c.name == row["city_name"])
                        world_x, world_y = city_match.x, city_match.y
                    self.search_result_rects.append((row_rect, kind, row["id"], world_x, world_y))
                    y += 26
                y += 14
            return

        if self.selection is None:
            self.draw_text("World atlas", (x, y), TEXT, self.title)
            self.draw_text("Terrain and map positions are stored", (x, y + 54), MUTED, self.small)
            self.draw_text("separately from the simulation database.", (x, y + 76), MUTED, self.small)
            self.draw_text(f"Cities in this world: {len(self.data.cities)}", (x, y + 126), TEXT, self.font)
            y += 174
            self.draw_text("Settlement legend", (x, y), TEXT, self.font)
            for kind in ("Capital", "City", "Town", "Village"):
                y += 28
                pygame.draw.rect(self.screen, CITY_COLORS[kind], (x, y + 2, 13, 13), border_radius=2)
                self.draw_text(kind, (x + 23, y), MUTED, self.small)
            y += 46
            self.draw_text("Click a kingdom's name label", (x, y), MUTED, self.small)
            self.draw_text("on the map to select it.", (x, y + 20), MUTED, self.small)
            return

        if self.selection.kind == "kingdom":
            details = self.data.kingdom_details(self.selection.id)
            kingdom: sqlite3.Row = details["kingdom"]  # type: ignore[assignment]
            self.draw_text(kingdom["name"], (x, y), TEXT, self.title)
            self.draw_text("Kingdom", (x, y + 38), GOLD, self.font)
            values = [
                ("Capital", kingdom["capital_name"] or "None"),
                ("Ruler", kingdom["ruler_name"] or "Unassigned"),
                ("Cities", kingdom["city_count"]),
                ("Total population", kingdom["total_population"]),
            ]
            y += 86
            for label, value in values:
                self.draw_text(label, (x, y), MUTED, self.small)
                self.draw_text(str(value), (x + 164, y), TEXT, self.small)
                y += 27
            return

        if self.selection.kind == "building":
            details = self.data.building_details(self.selection.id)
            building: sqlite3.Row = details["building"]  # type: ignore[assignment]
            self.draw_text(building["name"], (x, y), TEXT, self.title)
            self.draw_text(f"{building['type']}  •  {building['city_name']}", (x, y + 38), GOLD, self.font)
            values = [
                ("Category", building["category"]),
                ("Capacity", building["capacity"]),
                ("Workers", building["workers"]),
                ("Residents", building["residents"]),
                ("Condition", f"{building['condition']}%"),
            ]
            y += 86
            for label, value in values:
                self.draw_text(label, (x, y), MUTED, self.small)
                self.draw_text(str(value), (x + 164, y), TEXT, self.small)
                y += 27
            return

        if self.selection.kind == "npc":
            details = self.data.npc_details(self.selection.id)
            npc: sqlite3.Row = details["npc"]  # type: ignore[assignment]
            self.draw_text(npc["name"], (x, y), TEXT, self.title)
            self.draw_text(f"{npc['title'] or npc['profession']}  •  {npc['city_name']}", (x, y + 38), GOLD, self.font)
            values = [
                ("Class", npc["class"]),
                ("Age", npc["age"]),
                ("Gender", npc["gender"]),
                ("Gold", f"{npc['gold']:g}"),
                ("Health", f"{npc['health']}/100"),
                ("Status", "Alive" if npc["alive"] else "Deceased"),
            ]
            y += 86
            for label, value in values:
                self.draw_text(label, (x, y), MUTED, self.small)
                self.draw_text(str(value), (x + 164, y), TEXT, self.small)
                y += 27
            return

        # kind == "city"
        details = self.data.city_details(self.selection.id)
        city: sqlite3.Row = details["city"]  # type: ignore[assignment]
        self.draw_text(city["name"], (x, y), TEXT, self.title)
        self.draw_text(f"{city['type']}  •  {city['kingdom_name']}", (x, y + 38), GOLD, self.font)
        values = [("Recorded population", city["population"]), ("Living NPCs", city["live_population"]), ("Buildings", city["building_count"]), ("Available quests", city["quest_count"]), ("Ruler", city["ruler_name"] or "Unassigned")]
        y += 86
        for label, value in values:
            self.draw_text(label, (x, y), MUTED, self.small)
            self.draw_text(str(value), (x + 164, y), TEXT, self.small)
            y += 27
        self.draw_text("Resources", (x, y + 10), TEXT, self.font)
        y += 40
        resources: list[sqlite3.Row] = details["resources"]  # type: ignore[assignment]
        if not resources:
            self.draw_text("No recorded resources", (x, y), MUTED, self.small)
        for resource in resources[:8]:
            self.draw_text(resource["resource_name"], (x, y), MUTED, self.small)
            self.draw_text(f"{resource['amount']:g}", (x + 220, y), TEXT, self.small)
            y += 23
        y += 30
        self.draw_text("Buildings (click a type to inspect one)", (x, y), TEXT, self.font)
        y += 32
        buildings = self.data.city_buildings(self.selection.id)
        if not buildings:
            self.draw_text("No recorded buildings", (x, y), MUTED, self.small)
        for building in buildings[:10]:
            row_rect = pygame.Rect(x - 6, y - 3, SIDEBAR_WIDTH - 36, 22)
            if row_rect.collidepoint(pygame.mouse.get_pos()):
                pygame.draw.rect(self.screen, PANEL_EDGE, row_rect, border_radius=3)
            self.draw_text(f"{building['type']}", (x, y), MUTED, self.small)
            occupants = building["workers"] + building["residents"]
            self.draw_text(f"x{building['count']}  ({occupants} there)", (x + 220, y), TEXT, self.small)
            self.building_row_rects.append((row_rect, building["sample_building_id"]))
            y += 25

    def city_at(self, point: tuple[int, int]) -> City | None:
        for city in reversed(self.data.cities):
            if self.world_to_screen(city.x, city.y).distance_to(point) <= self.city_radius(city) + 7:
                return city
        return None

    def kingdom_label_at(self, point: tuple[int, int]) -> int | None:
        for kingdom_id, label_rect in self.kingdom_label_rects.items():
            if label_rect.collidepoint(point):
                return kingdom_id
        return None

    def building_row_at(self, point: tuple[int, int]) -> int | None:
        for row_rect, building_id in self.building_row_rects:
            if row_rect.collidepoint(point):
                return building_id
        return None

    def search_result_at(self, point: tuple[int, int]) -> tuple[str, int, float, float] | None:
        for row_rect, kind, entity_id, world_x, world_y in self.search_result_rects:
            if row_rect.collidepoint(point):
                return kind, entity_id, world_x, world_y
        return None

    def run_search(self) -> None:
        self.search_results = self.data.search(self.search_query) if len(self.search_query) >= 2 else {}

    def select(self, kind: str, entity_id: int) -> None:
        # Clears any active search whenever a selection is made, so the
        # sidebar shows the new selection instead of getting stuck on
        # stale search results.
        self.selection = Selection(kind, entity_id)
        self.search_query = ""
        self.search_active = False
        pygame.key.stop_text_input()

    def blur_search(self) -> None:
        self.search_active = False
        pygame.key.stop_text_input()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            self.running = False
            return

        # Keyboard input, while actively typing in the search box, takes
        # priority over every other keyboard shortcut -- so typing "r"
        # doesn't reset the camera and Escape clears the search instead
        # of quitting. Mouse clicks are NOT gated here; they're handled
        # by the unified logic below regardless of focus state, so
        # clicking a search result works whether or not the box still
        # has focus.
        if self.search_active and event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.search_query = ""
            self.search_results = {}
            self.blur_search()
            return
        if self.search_active and event.type == pygame.KEYDOWN and event.key == pygame.K_BACKSPACE:
            self.search_query = self.search_query[:-1]
            self.run_search()
            return
        if self.search_active and event.type == pygame.KEYDOWN and event.key == pygame.K_RETURN:
            if self.search_result_rects:
                _, kind, entity_id, world_x, world_y = self.search_result_rects[0]
                self.select(kind, entity_id)
                self.camera.center_on(world_x, world_y, self.map_rect)
            return
        if self.search_active and event.type == pygame.TEXTINPUT:
            self.search_query += event.text
            self.run_search()
            return

        if event.type == pygame.KEYDOWN and event.key in (pygame.K_ESCAPE, pygame.K_q):
            self.running = False
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_r:
            self.camera.reset(self.map_rect)
        elif event.type == pygame.MOUSEWHEEL and self.map_rect.collidepoint(pygame.mouse.get_pos()):
            self.camera.zoom_at(pygame.mouse.get_pos(), event.y, self.map_rect)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.map_rect.collidepoint(event.pos):
            self.blur_search()
            city_hit = self.city_at(event.pos)
            if city_hit:
                self.select("city", city_hit.id)
                return
            kingdom_hit = self.kingdom_label_at(event.pos)
            if kingdom_hit is not None:
                self.select("kingdom", kingdom_hit)
                return
            self.dragging = True
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.search_box_rect.collidepoint(event.pos):
                self.search_active = True
                pygame.key.start_text_input()
                return
            result_hit = self.search_result_at(event.pos)
            if result_hit is not None:
                kind, entity_id, world_x, world_y = result_hit
                self.select(kind, entity_id)
                self.camera.center_on(world_x, world_y, self.map_rect)
                return
            building_hit = self.building_row_at(event.pos)
            if building_hit is not None:
                self.select("building", building_hit)
            elif self.search_active:
                self.blur_search()
        elif event.type == pygame.MOUSEMOTION and self.dragging:
            self.camera.target_center -= pygame.Vector2(event.rel) / self.camera.target_zoom
            self.camera.clamp_target(self.map_rect)
        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False

    def handle_keyboard_camera(self, elapsed: float) -> None:
        keys = pygame.key.get_pressed()
        direction = pygame.Vector2(
            int(keys[pygame.K_d] or keys[pygame.K_RIGHT]) - int(keys[pygame.K_a] or keys[pygame.K_LEFT]),
            int(keys[pygame.K_s] or keys[pygame.K_DOWN]) - int(keys[pygame.K_w] or keys[pygame.K_UP]),
        )
        if direction.length_squared():
            self.camera.target_center += direction.normalize() * self.camera.KEYBOARD_SPEED * elapsed / self.camera.target_zoom
            self.camera.clamp_target(self.map_rect)

    def run(self) -> None:
        while self.running:
            elapsed = self.clock.tick(60) / 1000
            for event in pygame.event.get():
                self.handle_event(event)
            self.handle_keyboard_camera(elapsed)
            self.camera.update(self.map_rect, elapsed)
            self.draw_map()
            self.draw_sidebar()
            pygame.display.flip()
        pygame.quit()


def main() -> int:
    try:
        data = WorldData(WORLD_DATABASE, MAP_DATABASE)
        MapViewer(data).run()
        data.close()
    except (sqlite3.Error, OSError, RuntimeError) as error:
        print(f"Could not open map data: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

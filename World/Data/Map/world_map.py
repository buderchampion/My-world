"""
The world map: a Pygame window onto the running world (read-only -- the map
never changes your world).

    py world_map.py                 the world in Data/Database/world.db
    py world_map.py <path to .db>   another world (a save, say)

Drag to pan, mouse wheel (or + and -) to zoom, R to reset the view, L to
show or hide the legend, F5 to reload the world, Esc or Q to quit. While
the world runs, the map follows it: every REFRESH_SECONDS it looks for
changes and redraws armies, sieges and caravans where they now are.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pygame

from map_model import open_world, load_world, load_movement, realm_signature
import map_render as R

WINDOW_SIZE = (1280, 800)
TOP_BAR = 34
UNITS_PER_PIXEL = 4                  # detail of the land and realm layers: one pixel per this many map units
MAX_LAYER = 2000                     # ...but never more than this many pixels a side
ZOOM_MIN, ZOOM_MAX = 0.2, 6.0        # screen pixels per map unit
WORLD_DATABASE = Path(__file__).resolve().parents[1] / "Database" / "world.db"
REFRESH_SECONDS = 2                  # how often the map looks for changes while the world runs
BACKGROUND = (24, 30, 36)
PANEL = (19, 29, 39)


class Camera:
    """Which part of the world is on screen: a centre in map units and a zoom in screen pixels per unit."""

    def __init__(self, view_size, map_size):
        self.view_w, self.view_h = view_size
        self.map_size = map_size
        self.reset()

    def reset(self):
        self.zoom = min(self.view_w, self.view_h) / self.map_size * 0.97
        self.cx = self.cy = self.map_size / 2

    def to_screen(self, x, y):
        return (int((x - self.cx) * self.zoom + self.view_w / 2), int((y - self.cy) * self.zoom + self.view_h / 2) + TOP_BAR)

    def to_map(self, sx, sy):
        return ((sx - self.view_w / 2) / self.zoom + self.cx, (sy - TOP_BAR - self.view_h / 2) / self.zoom + self.cy)

    def pan(self, dx, dy):
        self.cx = min(self.map_size, max(0, self.cx - dx / self.zoom))
        self.cy = min(self.map_size, max(0, self.cy - dy / self.zoom))

    def zoom_at(self, factor, sx, sy):
        """Zoom by `factor`, keeping the map point under the cursor where it is."""
        mx, my = self.to_map(sx, sy)
        self.zoom = min(ZOOM_MAX, max(ZOOM_MIN * 2000 / self.map_size, self.zoom * factor))
        nx, ny = self.to_map(sx, sy)
        self.cx += mx - nx
        self.cy += my - ny


def draw_layer(screen, layer, camera):
    """Draws the part of a whole-world layer that is on screen, scaled to the current zoom."""
    resolution = layer.get_width()
    scale = resolution / camera.map_size
    left, top = camera.to_map(0, TOP_BAR)
    right, bottom = camera.to_map(camera.view_w, TOP_BAR + camera.view_h)
    x0, y0 = max(0, int(left * scale)), max(0, int(top * scale))
    x1, y1 = min(resolution, int(right * scale) + 2), min(resolution, int(bottom * scale) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    piece = layer.subsurface(pygame.Rect(x0, y0, x1 - x0, y1 - y0))
    sx, sy = camera.to_screen(x0 / scale, y0 / scale)
    ex, ey = camera.to_screen(x1 / scale, y1 / scale)
    size = (max(1, ex - sx), max(1, ey - sy))
    zoomed_in = size[0] > piece.get_width()                  # smooth when magnified, so borders do not turn to stairs
    screen.blit((pygame.transform.smoothscale if zoomed_in else pygame.transform.scale)(piece, size), (sx, sy))


class MapWindow:
    def __init__(self, database: Path, size=WINDOW_SIZE):
        self.database = database
        self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.display.set_caption("My World -- map")
        self.font = pygame.font.SysFont(None, 18)
        self.small = pygame.font.SysFont(None, 14)
        self.big = pygame.font.SysFont(None, 26)
        self.title = pygame.font.SysFont(None, 22)
        self.show_legend = True
        self.terrain = self.territory = None
        self.signature = None
        self.stamp = None
        self.next_check = 0.0
        self.load()
        self.camera = Camera((size[0], size[1] - TOP_BAR), self.view.size)

    def _file_stamp(self):
        """When the world last changed on disk (the database and its write-ahead log)."""
        return tuple(os.path.getmtime(p) if os.path.exists(p) else 0
                     for p in (str(self.database), str(self.database) + "-wal"))

    def load(self, force=False):
        """Reads the world afresh: the land layer once, the realm layer only when towns change hands
        (or when forced), and everything on the move every time."""
        conn = open_world(self.database)
        try:
            self.view = load_world(conn)
            self.moving = load_movement(conn, self.view)
        finally:
            conn.close()
        resolution = min(MAX_LAYER, int(self.view.size / UNITS_PER_PIXEL))
        if self.terrain is None or force:
            self.terrain = R.build_terrain(self.view, resolution)
        signature = realm_signature(self.view)
        if signature != self.signature or force:
            self.territory = R.build_territory(self.view, resolution)
            self.signature = signature
        self.stamp = self._file_stamp()
        self.refreshed = time.strftime("%H:%M:%S")

    def follow(self):
        """While the world runs: every REFRESH_SECONDS, reload if the world has changed."""
        now = time.monotonic()
        if now < self.next_check:
            return
        self.next_check = now + REFRESH_SECONDS
        if self._file_stamp() != self.stamp:
            try:
                self.load()
            except Exception:              # caught mid-write: try again next time
                pass

    def draw(self):
        self.screen.fill(BACKGROUND)
        draw_layer(self.screen, self.terrain, self.camera)
        draw_layer(self.screen, self.territory, self.camera)
        zoom = self.camera.zoom
        R.draw_rivers(self.screen, self.view, self.camera.to_screen, zoom)
        R.draw_roads(self.screen, self.view, self.camera.to_screen, zoom)
        placed = []
        R.draw_settlements(self.screen, self.view, self.camera.to_screen, zoom, self.font, self.small, placed)
        R.draw_movement(self.screen, self.view, self.moving, self.camera.to_screen, zoom, self.small)
        R.draw_kingdom_names(self.screen, self.view, self.camera.to_screen, zoom, self.big)
        if self.show_legend:
            R.draw_legend(self.screen, self.small, (10, self.screen.get_height() - 22 * len(R.LEGEND) - 24))
        pygame.draw.rect(self.screen, PANEL, (0, 0, self.screen.get_width(), TOP_BAR))
        seed = f"   seed {self.view.seed}" if self.view.seed is not None else ""
        moving = f"   |   {len(self.moving.armies)} armies, {len(self.moving.sieges)} sieges, {len(self.moving.caravans)} caravans   (updated {self.refreshed})"
        text = f"{self.view.world_name}  --  {self.view.date}{seed}{moving}"
        self.screen.blit(self.title.render(text, True, R.TEXT), (12, 9))
        hint = self.small.render("drag: pan   wheel: zoom   R: reset   L: legend   F5: reload   Esc: quit", True, (160, 170, 175))
        self.screen.blit(hint, (self.screen.get_width() - hint.get_width() - 12, 12))

    def handle(self, event):
        """One input event. Returns False to quit."""
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_ESCAPE, pygame.K_q):
                return False
            if event.key == pygame.K_r:
                self.camera.reset()
            elif event.key == pygame.K_F5:
                self.load(force=True)
            elif event.key == pygame.K_l:
                self.show_legend = not self.show_legend
            elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                self.camera.zoom_at(1.25, self.camera.view_w / 2, TOP_BAR + self.camera.view_h / 2)
            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                self.camera.zoom_at(0.8, self.camera.view_w / 2, TOP_BAR + self.camera.view_h / 2)
        elif event.type == pygame.MOUSEWHEEL:
            x, y = pygame.mouse.get_pos()
            self.camera.zoom_at(1.15 ** event.y, x, y)
        elif event.type == pygame.MOUSEMOTION and event.buttons[0]:
            self.camera.pan(*event.rel)
        elif event.type == pygame.VIDEORESIZE:
            self.camera.view_w, self.camera.view_h = event.w, event.h - TOP_BAR
        return True

    def run(self):
        clock = pygame.time.Clock()
        running = True
        while running:
            for event in pygame.event.get():
                running = self.handle(event) and running
            self.follow()
            self.draw()
            pygame.display.flip()
            clock.tick(60)


def main() -> int:
    database = Path(sys.argv[1]) if len(sys.argv) > 1 else WORLD_DATABASE
    if not database.exists():
        print(f"No world at {database} -- run main.py or run_world.py first.")
        return 1
    pygame.init()
    MapWindow(database).run()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
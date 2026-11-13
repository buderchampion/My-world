"""
Drawing the map: the land (terrain, climate, rivers), the realms (kingdom
territory, county borders, the neutral ground between them), roads, and
towns and villages. Every function draws onto a surface it is given, so the
same code serves the window (world_map.py) and off-screen images.
"""

from __future__ import annotations

import math

import numpy as np
import pygame

try:                                   # as part of the Data.Map package (check_project.py)...
    from .map_model import REGION_SIZE
except ImportError:                    # ...or run from this folder (py world_map.py)
    from map_model import REGION_SIZE

# --- the land --------------------------------------------------------------
TERRAIN_COLORS = {
    "Plains": (139, 170, 92), "Forest": (62, 110, 64), "Hills": (150, 148, 96),
    "Mountains": (128, 118, 108), "Marsh": (84, 122, 104), "Steppe": (182, 168, 112),
}
CLIMATE_TINT = {"Cold": (20, 26, 38), "Temperate": (0, 0, 0), "Warm": (24, 14, -8)}
RIVER = (70, 120, 190)
BORDER_WARP = 0.28           # how far region borders wander, as a share of a region's width

# --- the realms ------------------------------------------------------------
TERRITORY_REACH = 180        # map units of land a settlement holds around it; beyond every kingdom's reach is neutral ground
TERRITORY_ALPHA = 70
BORDER_ALPHA = 235
COUNTY_ALPHA = 90

# --- roads and settlements -------------------------------------------------
ROAD_COLORS = {"Capital Road": (112, 82, 52), "Trade Route": (214, 182, 92)}
SETTLEMENT_COLORS = {"Capital": (242, 196, 84), "City": (232, 132, 98), "Town": (232, 222, 196), "Village": (196, 186, 160)}
TEXT = (245, 242, 230)
SHADOW = (20, 24, 28)


def _grid(resolution, size):
    """Map coordinates of every pixel's centre, for a square grid of `resolution` pixels covering `size` map units."""
    step = size / resolution
    axis = (np.arange(resolution) + 0.5) * step
    return np.meshgrid(axis, axis)             # xs, ys -- [row][column] = [y][x]


def _warp(xs, ys, seed):
    """Gentle waves so borders between regions look like land, not like squares."""
    amp = REGION_SIZE * BORDER_WARP
    f1, f2 = 2 * math.pi / (REGION_SIZE * 1.3), 2 * math.pi / (REGION_SIZE * 0.55)
    s = (seed or 0) % 1000
    dx = amp * (0.65 * np.sin(ys * f1 + s) + 0.35 * np.sin(xs * f2 + s * 0.7))
    dy = amp * (0.65 * np.sin(xs * f1 + s * 1.3) + 0.35 * np.sin(ys * f2 + s * 0.3))
    return xs + dx, ys + dy


def meander(start, end, seed, bends=10):
    """A river's course from one region centre to the next: it winds, rather than running ruler-straight."""
    import random
    rng = random.Random(seed)
    length = math.dist(start, end)
    nx, ny = -(end[1] - start[1]) / max(1e-9, length), (end[0] - start[0]) / max(1e-9, length)
    phase, swing = rng.uniform(0, math.tau), length * 0.08
    points = []
    for i in range(bends + 1):
        t = i / bends
        off = math.sin(t * math.pi) * swing * (math.sin(t * math.tau * 1.5 + phase) + rng.uniform(-0.3, 0.3))
        points.append((start[0] + (end[0] - start[0]) * t + nx * off, start[1] + (end[1] - start[1]) * t + ny * off))
    return points


def build_terrain(view, resolution=1000):
    """The land as a surface covering the whole map: each pixel takes its region's terrain, tinted by climate
    and shaded by height, with rivers running downhill between region centres."""
    xs, ys = _warp(*_grid(resolution, view.size), view.seed)
    nearest = np.zeros(xs.shape, dtype=np.int32)
    best = np.full(xs.shape, np.inf)
    to_px = resolution / view.size
    reach = int(REGION_SIZE * 1.8 * to_px) + 2                 # a region only paints pixels near it: fast on big worlds
    for i, r in enumerate(view.regions):
        cx, cy = int(r.x * to_px), int(r.y * to_px)
        rows, cols = slice(max(0, cy - reach), min(resolution, cy + reach)), slice(max(0, cx - reach), min(resolution, cx + reach))
        d = (xs[rows, cols] - r.x) ** 2 + (ys[rows, cols] - r.y) ** 2
        b, n = best[rows, cols], nearest[rows, cols]
        closer = d < b
        b[closer], n[closer] = d[closer], i

    palette = np.zeros((len(view.regions), 3))
    for i, r in enumerate(view.regions):
        base = np.array(TERRAIN_COLORS.get(r.terrain, (120, 140, 100)), dtype=float)
        base += np.array(CLIMATE_TINT.get(r.climate, (0, 0, 0)))
        base *= 0.82 + 0.36 * r.elevation                     # higher land a little lighter
        palette[i] = np.clip(base, 0, 255)
    rgb = palette[nearest]
    # a soft relief: darker towards each region's edge
    shade = 1.0 - 0.10 * np.clip(np.sqrt(best) / REGION_SIZE, 0, 1)
    rgb = np.clip(rgb * shade[..., None], 0, 255).astype(np.uint8)

    surface = pygame.surfarray.make_surface(rgb.transpose(1, 0, 2))
    return surface


def draw_rivers(surface, view, to_screen, zoom):
    """Rivers as lines on screen, so they keep a river's width at any zoom (baked into the land, they swelled)."""
    if not hasattr(view, "_river_paths"):
        by_id = {r.id: r for r in view.regions}
        view._river_paths = [meander((by_id[a].x, by_id[a].y), (by_id[b].x, by_id[b].y), seed=a * 7919 + b) for a, b in view.rivers]
    width = max(1, min(5, int(zoom * 3)))
    for path in view._river_paths:
        pygame.draw.lines(surface, RIVER, False, [to_screen(x, y) for x, y in path], width)


def build_territory(view, resolution=1000):
    """Kingdom territory as a see-through layer: the land each settlement holds is tinted in its kingdom's
    colour, with darker lines at kingdom borders and fainter ones between counties. Land beyond every
    kingdom's reach stays clear: the neutral ground."""
    xs, ys = _grid(resolution, view.size)
    best = np.full(xs.shape, np.inf)
    owner = np.full(xs.shape, -1, dtype=np.int64)
    county = np.full(xs.shape, -1, dtype=np.int64)
    reach2 = TERRITORY_REACH ** 2
    for s in view.settlements.values():
        kingdom = view.kingdoms.get(s.kingdom_id)
        if kingdom is None or kingdom.fallen:
            continue
        to_px = resolution / view.size
        cx, cy, span = int(s.x * to_px), int(s.y * to_px), int(TERRITORY_REACH * to_px) + 2
        rows, cols = slice(max(0, cy - span), min(resolution, cy + span)), slice(max(0, cx - span), min(resolution, cx + span))
        d = (xs[rows, cols] - s.x) ** 2 + (ys[rows, cols] - s.y) ** 2
        b, o, c = best[rows, cols], owner[rows, cols], county[rows, cols]
        closer = (d < b) & (d <= reach2)
        b[closer] = d[closer]
        o[closer] = s.kingdom_id
        c[closer] = s.province_id if s.province_id is not None else -(s.kingdom_id + 1)   # crown lands: one "county"

    rgba = np.zeros((resolution, resolution, 4), dtype=np.uint8)
    for kid, kingdom in view.kingdoms.items():
        mask = owner == kid
        rgba[mask, :3] = kingdom.color
        rgba[mask, 3] = TERRITORY_ALPHA

    def edges(grid):
        e = np.zeros(grid.shape, dtype=bool)
        e[1:, :] |= grid[1:, :] != grid[:-1, :]
        e[:, 1:] |= grid[:, 1:] != grid[:, :-1]
        return e

    county_lines = edges(county) & (owner >= 0)
    rgba[county_lines, 3] = COUNTY_ALPHA + 60
    rgba[county_lines, :3] = (rgba[county_lines, :3] * 0.6).astype(np.uint8)
    kingdom_lines = edges(owner)
    kingdom_lines[1:, :] |= kingdom_lines[:-1, :]            # two pixels wide: a realm's edge shows on any land
    kingdom_lines[:, 1:] |= kingdom_lines[:, :-1]
    for kid, kingdom in view.kingdoms.items():
        line = kingdom_lines & ((owner == kid) | (np.roll(owner, 1, 0) == kid) | (np.roll(owner, 1, 1) == kid))
        rgba[line, :3] = [int(c * 0.55) for c in kingdom.color]
        rgba[line, 3] = BORDER_ALPHA

    surface = pygame.Surface((resolution, resolution), pygame.SRCALPHA)
    pixels = pygame.surfarray.pixels3d(surface)
    pixels[:] = rgba[..., :3].transpose(1, 0, 2)
    del pixels
    alpha = pygame.surfarray.pixels_alpha(surface)
    alpha[:] = rgba[..., 3].T
    del alpha
    return surface


def settlement_radius(s, zoom):
    """How big a settlement is drawn, by its kind and size, growing a little as you zoom in."""
    base = {"Capital": 7, "City": 5.5, "Town": 4.5, "Village": 2.6}.get(s.kind, 3)
    base += min(3, math.log10(max(10, s.population + s.households * 5)) - 1)
    return max(2, base * min(2.2, max(0.8, zoom * 1.6)))


FAR_TRADE_ROUTE = (128, 116, 84)   # zoomed far out, trade routes fade back (every capital has one to every other)


def draw_roads(surface, view, to_screen, zoom):
    far = zoom < 0.35
    for road in view.roads:
        a, b = view.settlements[road.a], view.settlements[road.b]
        width = 1 if far else 2 if road.kind == "Trade Route" else 1
        width = max(1, int(width * max(1, zoom * 1.4)))
        pa, pb = to_screen(a.x, a.y), to_screen(b.x, b.y)
        if road.kind == "Trade Route":                             # trade routes dashed, in gold
            length = math.dist(pa, pb)
            steps = max(1, int(length / 10))
            for i in range(0, steps, 2):
                t0, t1 = i / steps, min(1, (i + 1) / steps)
                p0 = (pa[0] + (pb[0] - pa[0]) * t0, pa[1] + (pb[1] - pa[1]) * t0)
                p1 = (pa[0] + (pb[0] - pa[0]) * t1, pa[1] + (pb[1] - pa[1]) * t1)
                pygame.draw.line(surface, FAR_TRADE_ROUTE if far else ROAD_COLORS[road.kind], p0, p1, width)
        else:
            pygame.draw.line(surface, ROAD_COLORS.get(road.kind, (110, 90, 60)), pa, pb, width)


def draw_label(surface, font, text, pos, color=TEXT, placed=None):
    """A label with a shadow. With `placed` (the rectangles of labels already drawn), one that would overlap
    another is skipped -- so busy places stay readable. Returns whether it was drawn."""
    label = font.render(text, True, color)
    x, y = int(pos[0] - label.get_width() / 2), int(pos[1])
    rect = pygame.Rect(x - 2, y - 1, label.get_width() + 4, label.get_height() + 2)
    if placed is not None:
        if rect.collidelist(placed) != -1:
            return False
        placed.append(rect)
    surface.blit(font.render(text, True, SHADOW), (x + 1, y + 1))
    surface.blit(label, (x, y))
    return True


def draw_settlements(surface, view, to_screen, zoom, font, small_font, placed=None):
    order = {"Village": 0, "Town": 1, "City": 2, "Capital": 3}
    labels = []
    for s in sorted(view.settlements.values(), key=lambda s: order.get(s.kind, 0)):
        kingdom = view.kingdoms.get(s.kingdom_id)
        x, y = to_screen(s.x, s.y)
        r = settlement_radius(s, zoom)
        ring = kingdom.color if kingdom else (90, 90, 90)
        if s.kind == "Capital":                                    # a capital: a crowned diamond
            points = [(x, y - r * 1.3), (x + r * 1.1, y), (x, y + r * 1.3), (x - r * 1.1, y)]
            pygame.draw.polygon(surface, SETTLEMENT_COLORS["Capital"], points)
            pygame.draw.polygon(surface, ring, points, max(2, int(r / 3)))
        elif s.kind in ("Town", "City"):                           # a town: a walled square
            rect = pygame.Rect(0, 0, r * 2, r * 2)
            rect.center = (x, y)
            pygame.draw.rect(surface, SETTLEMENT_COLORS[s.kind], rect)
            pygame.draw.rect(surface, ring, rect, max(1, int(r / 3)))
        else:                                                      # a village: a dot
            pygame.draw.circle(surface, SETTLEMENT_COLORS["Village"], (x, y), r)
            pygame.draw.circle(surface, ring, (x, y), r, 1)
        if s.kind == "Capital" or (s.kind in ("Town", "City") and zoom >= 0.45) or (s.kind == "Village" and zoom >= 1.1):
            labels.append((order.get(s.kind, 0), s, (x, y + r + 2)))
    placed = [] if placed is None else placed
    for _, s, pos in sorted(labels, key=lambda l: -l[0]):        # capitals first, then towns, then villages
        draw_label(surface, font if s.kind != "Village" else small_font, s.name, pos, placed=placed)


def draw_kingdom_names(surface, view, to_screen, zoom, font):
    """Each kingdom's name over its capital, in its colour, when zoomed out."""
    if zoom > 0.9:
        return
    for kingdom in view.kingdoms.values():
        if kingdom.fallen or kingdom.capital_id not in view.settlements:
            continue
        cap = view.settlements[kingdom.capital_id]
        x, y = to_screen(cap.x, cap.y)
        draw_label(surface, font, kingdom.name.upper(), (x, y - 34), tuple(min(255, int(c * 1.25) + 30) for c in kingdom.color))


# --- things on the move (part 2) ----------------------------------------------------------
SIEGE = (214, 54, 46)
TRADE_CART = (236, 200, 96)
FLAG_POLE = (40, 34, 30)


def _dashed(surface, color, points, width, dash=9):
    """A dashed line through `points` (screen positions)."""
    carry = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.dist((x0, y0), (x1, y1))
        t = carry
        while t < length:
            a, b = t / max(length, 1e-9), min(1.0, (t + dash) / max(length, 1e-9))
            pygame.draw.line(surface, color, (x0 + (x1 - x0) * a, y0 + (y1 - y0) * a), (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b), width)
            t += dash * 2
        carry = t - length


def draw_shield(surface, center, size, color, outline=(250, 246, 236)):
    x, y = center
    points = [(x - size, y - size), (x + size, y - size), (x + size, y + size * 0.3), (x, y + size * 1.25), (x - size, y + size * 0.3)]
    pygame.draw.polygon(surface, color, points)
    pygame.draw.polygon(surface, outline, points, 2)


def army_size(soldiers, zoom):
    return max(5, min(18, (4 + math.sqrt(max(1, soldiers)) * 0.9) * max(0.8, min(1.6, zoom * 1.5))))


def draw_movement(surface, view, moving, to_screen, zoom, font):
    """Marches (the road ahead), sieges, occupied towns, caravans and armies -- in that order, so markers sit on top."""
    for army in moving.armies:                                     # the road still ahead, beneath everything
        if army.path:
            color = view.kingdoms[army.kingdom_id].color if army.kingdom_id in view.kingdoms else (200, 200, 200)
            points = [to_screen(x, y) for x, y in [(army.x, army.y)] + army.path[1:]]
            _dashed(surface, color, points, max(2, int(zoom * 2)))
            end = points[-1]
            pygame.draw.circle(surface, color, end, max(4, int(zoom * 4)), 2)
    for siege in moving.sieges:                                    # a red ring, filling as the works rise
        town = view.settlements.get(siege.city_id)
        if not town:
            continue
        x, y = to_screen(town.x, town.y)
        r = max(14, int(settlement_radius(town, zoom) + 10))      # a siege stands out at any distance
        rect = pygame.Rect(x - r, y - r, r * 2, r * 2)
        pygame.draw.circle(surface, (90, 26, 22), (x, y), r, 4)
        if siege.progress > 0:
            pygame.draw.arc(surface, SIEGE, rect, math.pi / 2, math.pi / 2 + math.tau * siege.progress, 6)
        text = f"siege {siege.progress:.0%}" + (", hungry" if siege.hungry_days else "") if zoom >= 0.3 else "siege"
        draw_label(surface, font, text, (x, y - r - 15), (255, 120, 104))
    for city_id, occupier in moving.occupied.items():             # a little flag in the occupier's colour
        town, kingdom = view.settlements.get(city_id), view.kingdoms.get(occupier)
        if town and kingdom:
            x, y = to_screen(town.x, town.y)
            top = y - int(settlement_radius(town, zoom)) - 20
            pygame.draw.line(surface, FLAG_POLE, (x, y), (x, top), 3)
            flag = [(x, top), (x + 15, top + 5), (x, top + 11)]
            pygame.draw.polygon(surface, kingdom.color, flag)
            pygame.draw.polygon(surface, (250, 246, 236), flag, 1)
    for cart in moving.caravans:                                   # small carts on the roads
        x, y = to_screen(cart.x, cart.y)
        color = TRADE_CART if cart.kind == "Trade" else view.kingdoms[cart.kingdom_id].color if cart.kingdom_id in view.kingdoms else TRADE_CART
        size = 3 if zoom < 0.6 else 4
        pygame.draw.rect(surface, SHADOW, (x - size, y - size + 1, size * 2 + 1, size * 2))
        pygame.draw.rect(surface, color, (x - size, y - size, size * 2, size * 2 - 1))
    besieged = {siege.city_id for siege in moving.sieges}
    for army in moving.armies:                                     # armies on top
        color = view.kingdoms[army.kingdom_id].color if army.kingdom_id in view.kingdoms else (200, 200, 200)
        x, y = to_screen(army.x, army.y)
        size = army_size(army.soldiers, zoom)
        here = next((t for t in view.settlements.values() if t.id in besieged and (t.x, t.y) == (army.x, army.y)), None)
        if here is not None and not army.path:                     # besiegers camp outside the walls, beside the ring
            ring = max(14, int(settlement_radius(here, zoom) + 10))
            x -= ring + size + 4
        draw_shield(surface, (x, y - size * 0.4), size, color)
        if zoom >= 0.45:
            draw_label(surface, font, f"{army.soldiers}", (x, y + size * 0.9))


LEGEND = [("shield", "an army (number: its soldiers)"), ("march", "its march: the road still ahead"),
          ("siege", "a siege: the ring fills as the works rise"), ("flag", "an occupied town, in the occupier's colour"),
          ("trade", "a trade caravan"), ("supply", "a supply caravan, in its army's colour")]


def draw_legend(surface, font, corner):
    """What the symbols mean, in a small panel."""
    x0, y0 = corner
    panel = pygame.Surface((300, 22 * len(LEGEND) + 14), pygame.SRCALPHA)
    panel.fill((18, 24, 30, 215))
    surface.blit(panel, (x0, y0))
    for i, (kind, text) in enumerate(LEGEND):
        cx, cy = x0 + 20, y0 + 18 + i * 22
        if kind == "shield":
            draw_shield(surface, (cx, cy - 2), 7, (180, 60, 60))
        elif kind == "march":
            _dashed(surface, (180, 60, 60), [(cx - 10, cy), (cx + 10, cy)], 2, dash=5)
        elif kind == "siege":
            pygame.draw.circle(surface, (80, 24, 20), (cx, cy), 8, 3)
            pygame.draw.arc(surface, SIEGE, pygame.Rect(cx - 8, cy - 8, 16, 16), math.pi / 2, math.pi * 1.6, 4)
        elif kind == "flag":
            pygame.draw.line(surface, FLAG_POLE, (cx, cy + 7), (cx, cy - 8), 2)
            pygame.draw.polygon(surface, (90, 110, 200), [(cx, cy - 8), (cx + 10, cy - 4), (cx, cy)])
        else:
            color = TRADE_CART if kind == "trade" else (90, 110, 200)
            pygame.draw.rect(surface, color, (cx - 4, cy - 4, 8, 7))
        surface.blit(font.render(text, True, TEXT), (x0 + 40, cy - 7))
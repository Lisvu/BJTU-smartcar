"""Project frontier targets onto free cells with configurable obstacle clearance."""

from collections import deque
import math
from typing import Iterable, Optional, Set

from .occupancy import Cell, OccupancyMap, Point


def has_occupied_clearance(grid: OccupancyMap, cell: Cell, clearance_m: float) -> bool:
    if not grid.is_free(cell):
        return False
    return not any(grid.is_occupied(neighbor) for neighbor in grid.cells_within_radius(cell, clearance_m))


def nearest_safe_free_cell(
    grid: OccupancyMap,
    start: Cell,
    occupied_clearance_m: float,
    max_search_radius_m: float = 2.0,
    preferred_cells: Optional[Iterable[Cell]] = None,
) -> Optional[Cell]:
    """Breadth-first search for the closest known-free cell with obstacle clearance."""
    if not grid.in_bounds(start):
        return None
    preferred: Optional[Set[Cell]] = set(preferred_cells) if preferred_cells is not None else None
    queue = deque([(start, 0)])
    visited = {start}
    max_steps = int(math.ceil(max_search_radius_m / grid.geometry.resolution))
    fallback = None
    while queue:
        cell, depth = queue.popleft()
        if depth > max_steps:
            continue
        if has_occupied_clearance(grid, cell, occupied_clearance_m):
            if preferred is None or cell in preferred:
                return cell
            if fallback is None:
                fallback = cell
        if depth == max_steps:
            continue
        for neighbor in grid.neighbors(cell, connectivity=8):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, depth + 1))
    return fallback


def sanitize_world_target(
    grid: OccupancyMap,
    target_world: Point,
    occupied_clearance_m: float,
    max_search_radius_m: float = 2.0,
) -> Optional[Point]:
    try:
        cell = grid.world_to_cell(target_world)
    except IndexError:
        cell = grid.world_to_cell(target_world, clamp=True)
    safe = nearest_safe_free_cell(grid, cell, occupied_clearance_m, max_search_radius_m)
    return grid.cell_to_world(safe) if safe is not None else None

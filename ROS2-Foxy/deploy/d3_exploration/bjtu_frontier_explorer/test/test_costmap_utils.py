import numpy as np

from bjtu_frontier_explorer.costmap_utils import (
    has_occupied_clearance,
    nearest_safe_free_cell,
    sanitize_world_target,
)
from bjtu_frontier_explorer.occupancy import GridGeometry, OccupancyMap


def make_grid():
    data = np.zeros((9, 9), dtype=np.int8)
    data[4, 4] = 100
    return OccupancyMap(data, GridGeometry(9, 9, 0.1))


def test_clearance_rejects_obstacle_and_nearby_cells():
    grid = make_grid()
    assert not has_occupied_clearance(grid, (4, 4), 0.2)
    assert not has_occupied_clearance(grid, (5, 4), 0.2)
    assert has_occupied_clearance(grid, (7, 4), 0.2)


def test_target_on_obstacle_moves_to_nearest_safe_free_cell():
    grid = make_grid()
    safe = nearest_safe_free_cell(grid, (4, 4), occupied_clearance_m=0.15)
    assert safe is not None
    assert grid.is_free(safe)
    assert has_occupied_clearance(grid, safe, 0.15)


def test_world_target_outside_map_is_clamped_and_sanitized():
    grid = make_grid()
    world = sanitize_world_target(grid, (-100.0, -100.0), 0.0)
    assert world == grid.cell_to_world((0, 0))


def test_no_solution_within_radius_returns_none():
    data = np.full((5, 5), 100, dtype=np.int8)
    grid = OccupancyMap(data, GridGeometry(5, 5, 0.1))
    assert nearest_safe_free_cell(grid, (2, 2), 0.0, 0.2) is None

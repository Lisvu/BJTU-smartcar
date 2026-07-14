import math

import numpy as np
import pytest

from bjtu_frontier_explorer.occupancy import GridGeometry, OccupancyMap


def make_grid(yaw=0.0):
    geometry = GridGeometry(8, 6, 0.25, origin_x=2.0, origin_y=-1.0, origin_yaw=yaw)
    return OccupancyMap(np.zeros((6, 8), dtype=np.int8), geometry)


@pytest.mark.parametrize("cell", [(0, 0), (2, 4), (7, 5)])
def test_cell_world_round_trip(cell):
    grid = make_grid()
    assert grid.world_to_cell(grid.cell_to_world(cell)) == cell


def test_rotated_origin_transform_and_round_trip():
    grid = make_grid(math.pi / 2.0)
    world = grid.cell_to_world((1, 2))
    assert world[0] == pytest.approx(2.0 - 2.5 * 0.25)
    assert world[1] == pytest.approx(-1.0 + 1.5 * 0.25)
    assert grid.world_to_cell(world) == (1, 2)


def test_world_outside_raises_or_clamps():
    grid = make_grid()
    with pytest.raises(IndexError):
        grid.world_to_cell((-100.0, -100.0))
    assert grid.world_to_cell((-100.0, -100.0), clamp=True) == (0, 0)


def test_invalid_shape_and_geometry_rejected():
    with pytest.raises(ValueError):
        GridGeometry(0, 2, 0.1)
    geometry = GridGeometry(2, 2, 0.1)
    with pytest.raises(ValueError):
        OccupancyMap(np.zeros((3, 3)), geometry)


def test_neighborhoods_at_corner():
    grid = make_grid()
    assert set(grid.neighbors((0, 0), 4)) == {(1, 0), (0, 1)}
    assert set(grid.neighbors((0, 0), 8)) == {(1, 0), (0, 1), (1, 1)}

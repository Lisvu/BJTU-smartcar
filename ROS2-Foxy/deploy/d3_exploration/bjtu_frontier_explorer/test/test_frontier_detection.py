import numpy as np

from bjtu_frontier_explorer.frontier_detection import (
    FrontierDetectionConfig,
    detect_frontiers,
)
from bjtu_frontier_explorer.occupancy import GridGeometry, OccupancyMap


def grid(values):
    array = np.asarray(values, dtype=np.int8)
    return OccupancyMap(array, GridGeometry(array.shape[1], array.shape[0], 1.0))


def test_exact_frontier_cells_with_four_neighbor_unknown_rule():
    subject = grid([
        [-1, -1, -1, -1, -1],
        [-1, 0, 0, 0, -1],
        [-1, 0, 0, 0, 100],
        [100, 0, 0, 0, 100],
    ])
    config = FrontierDetectionConfig(unknown_connectivity=4, free_connectivity=8, min_free_neighbors=1)
    assert detect_frontiers(subject, config) == frozenset({(1, 1), (2, 1), (3, 1), (1, 2)})


def test_diagonal_unknown_requires_eight_connectivity():
    subject = grid([[0, 100], [100, -1]])
    assert detect_frontiers(subject, FrontierDetectionConfig(4, 8, 0)) == frozenset()
    assert detect_frontiers(subject, FrontierDetectionConfig(8, 8, 0)) == frozenset({(0, 0)})


def test_free_neighbor_filter_removes_isolated_noise():
    subject = grid([[-1, -1, -1], [-1, 0, -1], [-1, -1, -1]])
    assert detect_frontiers(subject, FrontierDetectionConfig(min_free_neighbors=0)) == frozenset({(1, 1)})
    assert detect_frontiers(subject, FrontierDetectionConfig(min_free_neighbors=1)) == frozenset()


def test_all_unknown_and_all_known_are_degenerate_empty_cases():
    unknown = grid(np.full((4, 4), -1))
    known = grid(np.zeros((4, 4)))
    assert detect_frontiers(unknown) == frozenset()
    assert detect_frontiers(known) == frozenset()


def test_boundary_free_cells_are_not_implicitly_frontiers():
    subject = grid(np.zeros((3, 3)))
    assert detect_frontiers(subject) == frozenset()

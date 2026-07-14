import numpy as np
import pytest

from bjtu_frontier_explorer.clustering import summarize_cluster
from bjtu_frontier_explorer.occupancy import GridGeometry, OccupancyMap
from bjtu_frontier_explorer.scoring import ScoringConfig, rank_frontiers, select_frontier


def make_grid():
    data = np.zeros((30, 30), dtype=np.int8)
    data[10:20, 10:20] = -1
    return OccupancyMap(data, GridGeometry(30, 30, 0.1))


def test_larger_nearby_frontier_wins():
    grid = make_grid()
    near_large = summarize_cluster([(4, 4), (4, 5), (5, 4), (5, 5)])
    far_small = summarize_cluster([(20, 20), (20, 21)])
    selected = select_frontier(grid, [far_small, near_large], (0.0, 0.0))
    assert selected is not None
    assert selected.cluster == near_large


def test_distance_bias_reduces_distance_discrimination():
    grid = make_grid()
    near = summarize_cluster([(2, 2), (2, 3)])
    far = summarize_cluster([(18, 2), (18, 3)])
    low_bias = rank_frontiers(grid, [near, far], (0.0, 0.0), config=ScoringConfig(distance_bias_m=0.01))
    high_bias = rank_frontiers(grid, [near, far], (0.0, 0.0), config=ScoringConfig(distance_bias_m=100.0))
    low_ratio = low_bias[0].score / low_bias[1].score
    high_ratio = high_bias[0].score / high_bias[1].score
    assert low_ratio > high_ratio
    assert high_ratio == pytest.approx(1.0, rel=0.03)


def test_information_gain_can_change_ranking():
    grid = make_grid()
    low_gain = summarize_cluster([(2, 2), (2, 3)])
    high_gain = summarize_cluster([(9, 15), (9, 16)])
    config = ScoringConfig(
        distance_bias_m=0.5,
        information_gain_weight=0.1,
        information_gain_radius_m=0.5,
    )
    selected = select_frontier(grid, [low_gain, high_gain], (0.0, 0.0), config=config)
    assert selected is not None
    assert selected.cluster == high_gain


def test_heading_penalty_prefers_forward_candidate():
    grid = make_grid()
    forward = summarize_cluster([(10, 5), (10, 6)])
    behind = summarize_cluster([(0, 5), (0, 6)])
    robot = (0.55, 0.55)
    config = ScoringConfig(distance_bias_m=1.0, heading_penalty_weight=10.0)
    assert select_frontier(grid, [behind, forward], robot, 0.0, config).cluster == forward


def test_empty_input_returns_none():
    assert select_frontier(make_grid(), [], (0.0, 0.0)) is None


def test_invalid_distance_bias_rejected():
    with pytest.raises(ValueError):
        ScoringConfig(distance_bias_m=0.0)

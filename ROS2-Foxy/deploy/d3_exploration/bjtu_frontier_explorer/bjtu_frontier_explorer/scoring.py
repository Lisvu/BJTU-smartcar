"""Greedy frontier scoring with optional information and heading terms."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, List, Optional

from .clustering import FrontierCluster
from .occupancy import Cell, OccupancyMap, Point


def normalize_angle(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


@dataclass(frozen=True)
class ScoringConfig:
    distance_bias_m: float = 0.5
    area_weight: float = 1.0
    information_gain_weight: float = 0.0
    information_gain_radius_m: float = 0.75
    heading_penalty_weight: float = 0.0

    def __post_init__(self) -> None:
        if self.distance_bias_m <= 0.0:
            raise ValueError("distance_bias_m must be positive")
        if self.information_gain_radius_m < 0.0:
            raise ValueError("information_gain_radius_m cannot be negative")


@dataclass(frozen=True)
class ScoredFrontier:
    cluster: FrontierCluster
    target_cell: Cell
    target_world: Point
    area_m2: float
    distance_m: float
    heading_error_rad: float
    information_gain_cells: int
    score: float


def representative_cell(cluster: FrontierCluster) -> Cell:
    """Return the real cluster cell nearest its arithmetic centroid."""
    cx, cy = cluster.centroid_cell
    return min(cluster.cells, key=lambda cell: ((cell[0] - cx) ** 2 + (cell[1] - cy) ** 2, cell))


def approximate_information_gain(grid: OccupancyMap, target: Cell, radius_m: float) -> int:
    return sum(grid.is_unknown(cell) for cell in grid.cells_within_radius(target, radius_m))


def score_cluster(
    grid: OccupancyMap,
    cluster: FrontierCluster,
    robot_world: Point,
    robot_yaw: float = 0.0,
    config: ScoringConfig = ScoringConfig(),
    target_cell: Optional[Cell] = None,
) -> ScoredFrontier:
    target = target_cell if target_cell is not None else representative_cell(cluster)
    world = grid.cell_to_world(target)
    dx, dy = world[0] - robot_world[0], world[1] - robot_world[1]
    distance = math.hypot(dx, dy)
    heading_error = normalize_angle(math.atan2(dy, dx) - robot_yaw)
    area = cluster.cell_count * grid.geometry.resolution ** 2
    gain = approximate_information_gain(grid, target, config.information_gain_radius_m)
    utility = config.area_weight * area + config.information_gain_weight * gain
    heading_penalty = config.heading_penalty_weight * abs(heading_error) / math.pi
    score = utility / (distance + config.distance_bias_m) - heading_penalty
    return ScoredFrontier(cluster, target, world, area, distance, heading_error, gain, score)


def rank_frontiers(
    grid: OccupancyMap,
    clusters: Iterable[FrontierCluster],
    robot_world: Point,
    robot_yaw: float = 0.0,
    config: ScoringConfig = ScoringConfig(),
) -> List[ScoredFrontier]:
    scored = [score_cluster(grid, cluster, robot_world, robot_yaw, config) for cluster in clusters]
    return sorted(scored, key=lambda item: (-item.score, item.distance_m, -item.cluster.cell_count))


def select_frontier(
    grid: OccupancyMap,
    clusters: Iterable[FrontierCluster],
    robot_world: Point,
    robot_yaw: float = 0.0,
    config: ScoringConfig = ScoringConfig(),
) -> Optional[ScoredFrontier]:
    ranked = rank_frontiers(grid, clusters, robot_world, robot_yaw, config)
    return ranked[0] if ranked else None

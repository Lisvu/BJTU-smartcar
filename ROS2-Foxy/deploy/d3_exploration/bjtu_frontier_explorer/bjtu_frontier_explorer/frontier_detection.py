"""Yamauchi frontier-cell detection on a partially known occupancy map."""

from dataclasses import dataclass
from typing import FrozenSet

from .occupancy import Cell, OccupancyMap


@dataclass(frozen=True)
class FrontierDetectionConfig:
    unknown_connectivity: int = 8
    free_connectivity: int = 8
    min_free_neighbors: int = 1

    def __post_init__(self) -> None:
        if self.unknown_connectivity not in (4, 8) or self.free_connectivity not in (4, 8):
            raise ValueError("frontier connectivity must be 4 or 8")
        if self.min_free_neighbors < 0:
            raise ValueError("min_free_neighbors cannot be negative")


def is_frontier_cell(grid: OccupancyMap, cell: Cell, config: FrontierDetectionConfig) -> bool:
    """A frontier is known free, adjacent to unknown, and locally supported by free space."""
    if not grid.is_free(cell):
        return False
    if not any(grid.is_unknown(neighbor) for neighbor in grid.neighbors(cell, config.unknown_connectivity)):
        return False
    free_count = sum(grid.is_free(neighbor) for neighbor in grid.neighbors(cell, config.free_connectivity))
    return free_count >= config.min_free_neighbors


def detect_frontiers(
    grid: OccupancyMap, config: FrontierDetectionConfig = FrontierDetectionConfig()
) -> FrozenSet[Cell]:
    return frozenset(cell for cell in grid.iter_cells() if is_frontier_cell(grid, cell, config))

"""Occupancy-grid storage, geometry, and neighborhood operations.

The algorithms use ``(x, y)`` cells while NumPy stores values as ``grid[y, x]``.
Keeping this conversion in one class prevents the row/column inversions that are
otherwise easy to introduce in frontier and costmap code.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Iterator, Sequence, Tuple

import numpy as np

Cell = Tuple[int, int]
Point = Tuple[float, float]

UNKNOWN = -1
FREE = 0
OCCUPIED_THRESHOLD = 50


@dataclass(frozen=True)
class GridGeometry:
    """Metric metadata needed to transform cells through a rotated map origin."""

    width: int
    height: int
    resolution: float
    origin_x: float = 0.0
    origin_y: float = 0.0
    origin_yaw: float = 0.0

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("grid width and height must be positive")
        if not math.isfinite(self.resolution) or self.resolution <= 0.0:
            raise ValueError("grid resolution must be positive and finite")


class OccupancyMap:
    """Validated 2-D occupancy data with metric coordinate transforms."""

    def __init__(self, data: np.ndarray, geometry: GridGeometry):
        array = np.asarray(data, dtype=np.int16)
        expected = (geometry.height, geometry.width)
        if array.shape != expected:
            raise ValueError(f"occupancy shape {array.shape} does not match {expected}")
        self.data = array.copy()
        self.geometry = geometry

    @classmethod
    def from_flat(cls, values: Sequence[int], geometry: GridGeometry) -> "OccupancyMap":
        if len(values) != geometry.width * geometry.height:
            raise ValueError("flat occupancy data length does not match map dimensions")
        return cls(np.asarray(values, dtype=np.int16).reshape(geometry.height, geometry.width), geometry)

    @classmethod
    def from_ros(cls, message) -> "OccupancyMap":
        """Create from a nav_msgs/OccupancyGrid-like object without importing ROS."""
        info = message.info
        q = info.origin.orientation
        yaw = math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )
        geometry = GridGeometry(
            width=int(info.width),
            height=int(info.height),
            resolution=float(info.resolution),
            origin_x=float(info.origin.position.x),
            origin_y=float(info.origin.position.y),
            origin_yaw=yaw,
        )
        return cls.from_flat(message.data, geometry)

    def in_bounds(self, cell: Cell) -> bool:
        x, y = cell
        return 0 <= x < self.geometry.width and 0 <= y < self.geometry.height

    def value(self, cell: Cell) -> int:
        if not self.in_bounds(cell):
            raise IndexError(f"cell {cell} lies outside the occupancy map")
        return int(self.data[cell[1], cell[0]])

    def is_free(self, cell: Cell) -> bool:
        return self.in_bounds(cell) and self.value(cell) == FREE

    def is_unknown(self, cell: Cell) -> bool:
        return self.in_bounds(cell) and self.value(cell) == UNKNOWN

    def is_occupied(self, cell: Cell, threshold: int = OCCUPIED_THRESHOLD) -> bool:
        return self.in_bounds(cell) and self.value(cell) >= threshold

    def neighbors(self, cell: Cell, connectivity: int = 8) -> Iterator[Cell]:
        if connectivity not in (4, 8):
            raise ValueError("connectivity must be 4 or 8")
        x, y = cell
        offsets = ((1, 0), (-1, 0), (0, 1), (0, -1))
        if connectivity == 8:
            offsets += ((1, 1), (1, -1), (-1, 1), (-1, -1))
        for dx, dy in offsets:
            candidate = (x + dx, y + dy)
            if self.in_bounds(candidate):
                yield candidate

    def cells_within_radius(self, center: Cell, radius_m: float) -> Iterator[Cell]:
        if radius_m < 0.0:
            raise ValueError("radius must be non-negative")
        radius_cells = int(math.ceil(radius_m / self.geometry.resolution))
        cx, cy = center
        limit_sq = (radius_m / self.geometry.resolution) ** 2
        for y in range(max(0, cy - radius_cells), min(self.geometry.height, cy + radius_cells + 1)):
            for x in range(max(0, cx - radius_cells), min(self.geometry.width, cx + radius_cells + 1)):
                if (x - cx) ** 2 + (y - cy) ** 2 <= limit_sq + 1e-12:
                    yield (x, y)

    def cell_to_world(self, cell: Cell, center: bool = True) -> Point:
        if not self.in_bounds(cell):
            raise IndexError(f"cell {cell} lies outside the occupancy map")
        offset = 0.5 if center else 0.0
        local_x = (cell[0] + offset) * self.geometry.resolution
        local_y = (cell[1] + offset) * self.geometry.resolution
        cosine = math.cos(self.geometry.origin_yaw)
        sine = math.sin(self.geometry.origin_yaw)
        return (
            self.geometry.origin_x + cosine * local_x - sine * local_y,
            self.geometry.origin_y + sine * local_x + cosine * local_y,
        )

    def world_to_cell(self, point: Point, clamp: bool = False) -> Cell:
        dx = point[0] - self.geometry.origin_x
        dy = point[1] - self.geometry.origin_y
        cosine = math.cos(self.geometry.origin_yaw)
        sine = math.sin(self.geometry.origin_yaw)
        local_x = cosine * dx + sine * dy
        local_y = -sine * dx + cosine * dy
        cell = (
            int(math.floor(local_x / self.geometry.resolution)),
            int(math.floor(local_y / self.geometry.resolution)),
        )
        if clamp:
            return (
                min(max(cell[0], 0), self.geometry.width - 1),
                min(max(cell[1], 0), self.geometry.height - 1),
            )
        if not self.in_bounds(cell):
            raise IndexError(f"world point {point} transforms outside the occupancy map")
        return cell

    def iter_cells(self) -> Iterable[Cell]:
        for y in range(self.geometry.height):
            for x in range(self.geometry.width):
                yield (x, y)

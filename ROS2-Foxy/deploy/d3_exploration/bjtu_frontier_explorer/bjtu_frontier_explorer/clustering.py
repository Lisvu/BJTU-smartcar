"""Connected-component clustering and geometric summaries for frontier cells."""

from collections import deque
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from .occupancy import Cell


@dataclass(frozen=True)
class FrontierCluster:
    cells: Tuple[Cell, ...]
    centroid_cell: Tuple[float, float]
    cell_count: int
    bbox: Tuple[int, int, int, int]


def _neighbor_cells(cell: Cell, connectivity: int) -> Iterable[Cell]:
    if connectivity not in (4, 8):
        raise ValueError("connectivity must be 4 or 8")
    x, y = cell
    offsets = ((1, 0), (-1, 0), (0, 1), (0, -1))
    if connectivity == 8:
        offsets += ((1, 1), (1, -1), (-1, 1), (-1, -1))
    return ((x + dx, y + dy) for dx, dy in offsets)


def summarize_cluster(cells: Sequence[Cell]) -> FrontierCluster:
    if not cells:
        raise ValueError("cannot summarize an empty frontier cluster")
    ordered = tuple(sorted(set(cells), key=lambda cell: (cell[1], cell[0])))
    xs = [cell[0] for cell in ordered]
    ys = [cell[1] for cell in ordered]
    return FrontierCluster(
        cells=ordered,
        centroid_cell=(sum(xs) / len(ordered), sum(ys) / len(ordered)),
        cell_count=len(ordered),
        bbox=(min(xs), min(ys), max(xs), max(ys)),
    )


def cluster_frontiers(
    frontier_cells: Iterable[Cell], min_frontier_cells: int = 1, connectivity: int = 8
) -> List[FrontierCluster]:
    if min_frontier_cells < 1:
        raise ValueError("min_frontier_cells must be at least one")
    remaining = set(frontier_cells)
    clusters: List[FrontierCluster] = []
    while remaining:
        seed = min(remaining, key=lambda cell: (cell[1], cell[0]))
        remaining.remove(seed)
        component = [seed]
        queue = deque([seed])
        while queue:
            current = queue.popleft()
            for neighbor in _neighbor_cells(current, connectivity):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.append(neighbor)
                    queue.append(neighbor)
        if len(component) >= min_frontier_cells:
            clusters.append(summarize_cluster(component))
    return sorted(clusters, key=lambda cluster: (-cluster.cell_count, cluster.centroid_cell))

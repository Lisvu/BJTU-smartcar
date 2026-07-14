import pytest

from bjtu_frontier_explorer.clustering import cluster_frontiers, summarize_cluster


def test_separate_components_and_geometry():
    cells = {(0, 0), (1, 0), (1, 1), (7, 5), (8, 5)}
    clusters = cluster_frontiers(cells, connectivity=8)
    assert [cluster.cell_count for cluster in clusters] == [3, 2]
    assert clusters[0].centroid_cell == pytest.approx((2 / 3, 1 / 3))
    assert clusters[0].bbox == (0, 0, 1, 1)
    assert clusters[1].bbox == (7, 5, 8, 5)


def test_diagonal_cells_split_with_four_and_join_with_eight():
    cells = {(0, 0), (1, 1)}
    assert len(cluster_frontiers(cells, connectivity=4)) == 2
    assert len(cluster_frontiers(cells, connectivity=8)) == 1


def test_small_cluster_filter():
    cells = {(0, 0), (1, 0), (10, 10)}
    clusters = cluster_frontiers(cells, min_frontier_cells=2)
    assert len(clusters) == 1
    assert set(clusters[0].cells) == {(0, 0), (1, 0)}


def test_duplicate_cells_are_counted_once():
    cluster = summarize_cluster([(1, 2), (1, 2), (2, 2)])
    assert cluster.cell_count == 2


def test_empty_summary_and_bad_minimum_rejected():
    with pytest.raises(ValueError):
        summarize_cluster([])
    with pytest.raises(ValueError):
        cluster_frontiers([], min_frontier_cells=0)

"""ROS 2 composition node for map frontiers, Nav2 goals, and STOP response."""

from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

import rclpy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import OccupancyGrid
from rclpy.duration import Duration
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from tf2_ros import Buffer, TransformException, TransformListener

from .clustering import cluster_frontiers
from .costmap_utils import nearest_safe_free_cell, sanitize_world_target
from .frontier_detection import FrontierDetectionConfig, detect_frontiers
from .fsm import ActionType, EventType, ExplorerFSM, ExplorerState, FsmConfig, FsmEvent
from .occupancy import OccupancyMap
from .scoring import ScoredFrontier, ScoringConfig, score_cluster


class FrontierExplorerNode(Node):
    """Select frontiers greedily and delegate collision-aware travel to Nav2."""

    PARAMETER_DEFAULTS = {
        "dry_run": True,
        "map_topic": "/map",
        "stop_pose_topic": "/bjtu/stop_pose_map",
        "map_frame": "map",
        "base_frame": "base_footprint",
        "navigate_action": "/navigate_to_pose",
        "spin_action": "/spin",
        "recompute_period_s": 2.0,
        "frontier_connectivity": 8,
        "unknown_connectivity": 8,
        "min_free_neighbors": 1,
        "min_frontier_cells": 3,
        "distance_bias_m": 0.5,
        "area_weight": 1.0,
        "information_gain_weight": 0.0,
        "information_gain_radius_m": 0.75,
        "heading_penalty_weight": 0.0,
        "occupied_clearance_m": 0.18,
        "safe_target_search_radius_m": 2.0,
        "frontier_blacklist_radius_m": 0.5,
        "frontier_exhaustion_cycles": 3,
        "approach_standoff_m": 0.7,
        "arrive_radius_m": 0.25,
        "stop_pose_timeout_s": 1.0,
        "spin_target_yaw": 6.283185307179586,
        "max_frontier_failures": 0,
        "max_approach_failures": 2,
        "max_spin_failures": 1,
    }

    def __init__(self) -> None:
        super().__init__("bjtu_frontier_explorer")
        for name, default in self.PARAMETER_DEFAULTS.items():
            self.declare_parameter(name, default)

        self.dry_run = bool(self._param("dry_run"))
        self.map_frame = str(self._param("map_frame"))
        self.base_frame = str(self._param("base_frame"))
        self._map: Optional[OccupancyMap] = None
        self._map_stamp: Optional[Tuple[int, int]] = None
        self._last_processed_stamp: Optional[Tuple[int, int]] = None
        self._blacklist: List[Tuple[float, float]] = []
        self._empty_cycles = 0
        self._latest_stop: Optional[PoseStamped] = None
        self._latest_stop_monotonic = 0.0

        self._fsm = ExplorerFSM(FsmConfig(
            arrive_radius_m=float(self._param("arrive_radius_m")),
            max_frontier_failures=int(self._param("max_frontier_failures")),
            max_approach_failures=int(self._param("max_approach_failures")),
            max_spin_failures=int(self._param("max_spin_failures")),
        ))
        self._tf_buffer = Buffer(cache_time=Duration(seconds=15.0))
        self._tf_listener = TransformListener(self._tf_buffer, self)

        map_qos = QoSProfile(depth=1)
        map_qos.reliability = ReliabilityPolicy.RELIABLE
        map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        self.create_subscription(OccupancyGrid, str(self._param("map_topic")), self._map_callback, map_qos)
        self.create_subscription(PoseStamped, str(self._param("stop_pose_topic")), self._stop_callback, 10)
        self.create_timer(float(self._param("recompute_period_s")), self._tick)

        self._nav = None
        if not self.dry_run:
            from .nav_interface import NavInterface
            self._nav = NavInterface(
                self,
                str(self._param("navigate_action")),
                str(self._param("spin_action")),
            )
        self.get_logger().warning(
            f"D3 explorer started dry_run={self.dry_run}; "
            f"motion_interfaces={'ABSENT' if self._nav is None else 'ENABLED'}"
        )

    def _param(self, name: str):
        return self.get_parameter(name).value

    def _map_callback(self, message: OccupancyGrid) -> None:
        try:
            self._map = OccupancyMap.from_ros(message)
            self._map_stamp = (message.header.stamp.sec, message.header.stamp.nanosec)
        except (ValueError, TypeError) as exc:
            self.get_logger().error(f"invalid occupancy map: {exc}")

    def _robot_pose(self) -> Optional[Tuple[float, float, float]]:
        try:
            transform = self._tf_buffer.lookup_transform(
                self.map_frame, self.base_frame, rclpy.time.Time(), timeout=Duration(seconds=0.2)
            )
        except TransformException as exc:
            self.get_logger().warning(f"waiting for {self.map_frame}->{self.base_frame}: {exc}")
            return None
        q = transform.transform.rotation
        yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        return transform.transform.translation.x, transform.transform.translation.y, yaw

    def _tick(self) -> None:
        if self._fsm.state != ExplorerState.EXPLORE or self._map is None:
            return
        if self._nav is not None and self._nav.pending:
            return
        if self._map_stamp == self._last_processed_stamp:
            return
        self._last_processed_stamp = self._map_stamp
        robot = self._robot_pose()
        if robot is None:
            return
        candidates = self._build_candidates(robot)
        if not candidates:
            self._empty_cycles += 1
            limit = int(self._param("frontier_exhaustion_cycles"))
            print(f"D3 frontier_scan candidates=0 empty_cycles={self._empty_cycles}/{limit}", flush=True)
            if self._empty_cycles >= limit:
                self._execute(self._fsm.handle(FsmEvent(EventType.FRONTIERS_EXHAUSTED)))
            return
        self._empty_cycles = 0
        best = max(candidates, key=lambda item: item.score)
        print(
            f"{'DRY_RUN' if self.dry_run else 'LIVE'} frontier target=({best.target_world[0]:.2f},"
            f"{best.target_world[1]:.2f}) cells={best.cluster.cell_count} area={best.area_m2:.3f}m2 "
            f"distance={best.distance_m:.2f}m gain={best.information_gain_cells} score={best.score:.4f}",
            flush=True,
        )
        self._execute(self._fsm.handle(FsmEvent(EventType.FRONTIER_AVAILABLE, best.target_world)))

    def _build_candidates(self, robot: Tuple[float, float, float]) -> List[ScoredFrontier]:
        assert self._map is not None
        detection = FrontierDetectionConfig(
            unknown_connectivity=int(self._param("unknown_connectivity")),
            free_connectivity=int(self._param("frontier_connectivity")),
            min_free_neighbors=int(self._param("min_free_neighbors")),
        )
        cells = detect_frontiers(self._map, detection)
        clusters = cluster_frontiers(
            cells,
            min_frontier_cells=int(self._param("min_frontier_cells")),
            connectivity=int(self._param("frontier_connectivity")),
        )
        scoring = ScoringConfig(
            distance_bias_m=float(self._param("distance_bias_m")),
            area_weight=float(self._param("area_weight")),
            information_gain_weight=float(self._param("information_gain_weight")),
            information_gain_radius_m=float(self._param("information_gain_radius_m")),
            heading_penalty_weight=float(self._param("heading_penalty_weight")),
        )
        output = []
        for cluster in clusters:
            safe_cell = nearest_safe_free_cell(
                self._map,
                min(cluster.cells, key=lambda cell: (
                    (cell[0] - cluster.centroid_cell[0]) ** 2 + (cell[1] - cluster.centroid_cell[1]) ** 2
                )),
                float(self._param("occupied_clearance_m")),
                float(self._param("safe_target_search_radius_m")),
                cluster.cells,
            )
            if safe_cell is None:
                continue
            scored = score_cluster(self._map, cluster, robot[:2], robot[2], scoring, safe_cell)
            blacklist_radius = float(self._param("frontier_blacklist_radius_m"))
            if any(
                math.hypot(scored.target_world[0] - x, scored.target_world[1] - y) < blacklist_radius
                for x, y in self._blacklist
            ):
                continue
            output.append(scored)
        return output

    def _stop_callback(self, message: PoseStamped) -> None:
        if message.header.frame_id and message.header.frame_id != self.map_frame:
            self.get_logger().warning(
                f"ignoring STOP pose in {message.header.frame_id}; expected {self.map_frame}"
            )
            return
        self._latest_stop = message
        self._latest_stop_monotonic = time.monotonic()
        if self._fsm.state != ExplorerState.EXPLORE:
            return
        target = (message.pose.position.x, message.pose.position.y)
        print(f"STOP detected map=({target[0]:.2f},{target[1]:.2f})", flush=True)
        self._execute(self._fsm.handle(FsmEvent(EventType.STOP_DETECTED, target)))

    def _approach_target(self, stop: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        if self._map is None:
            return None
        robot = self._robot_pose()
        if robot is None:
            return None
        dx, dy = stop[0] - robot[0], stop[1] - robot[1]
        distance = math.hypot(dx, dy)
        if distance <= 1e-6:
            return robot[:2]
        standoff = min(float(self._param("approach_standoff_m")), distance)
        raw = (stop[0] - dx / distance * standoff, stop[1] - dy / distance * standoff)
        return sanitize_world_target(
            self._map,
            raw,
            float(self._param("occupied_clearance_m")),
            float(self._param("safe_target_search_radius_m")),
        )

    def _execute(self, action) -> None:
        if action.kind == ActionType.NONE:
            return
        if action.kind == ActionType.STOP:
            if self._nav is not None:
                self._nav.cancel()
            self.get_logger().info(f"FSM STOP: {action.reason}")
            return
        if self.dry_run:
            print(
                f"DRY_RUN action={action.kind.name} target={action.target} reason={action.reason}",
                flush=True,
            )
            return
        if action.kind == ActionType.SELECT_ANOTHER_FRONTIER:
            self._last_processed_stamp = None
            return
        if action.kind == ActionType.NAVIGATE_FRONTIER and action.target is not None:
            self._send_pose(action.target, "frontier")
        elif action.kind == ActionType.NAVIGATE_APPROACH and action.target is not None:
            approach = self._approach_target(action.target)
            if approach is None:
                self._execute(self._fsm.handle(FsmEvent(EventType.NAV_FAILED)))
            else:
                self._send_pose(approach, "approach")
        elif action.kind == ActionType.START_SPIN:
            self._fsm.handle(FsmEvent(EventType.SPIN_STARTED))
            self._start_spin()

    def _send_pose(self, target: Tuple[float, float], kind: str) -> None:
        assert self._nav is not None
        robot = self._robot_pose()
        if robot is None:
            self._execute(self._fsm.handle(FsmEvent(EventType.NAV_FAILED)))
            return
        pose = PoseStamped()
        pose.header.frame_id = self.map_frame
        pose.header.stamp = self.get_clock().now().to_msg()
        pose.pose.position.x, pose.pose.position.y = target
        yaw = math.atan2(target[1] - robot[1], target[0] - robot[0])
        pose.pose.orientation.z = math.sin(yaw / 2.0)
        pose.pose.orientation.w = math.cos(yaw / 2.0)
        if not self._nav.navigate(pose, lambda result: self._nav_result(kind, target, result)):
            self.get_logger().error("NavigateToPose action server unavailable")
            self._execute(self._fsm.handle(FsmEvent(EventType.NAV_FAILED)))

    def _nav_result(self, kind: str, target: Tuple[float, float], result) -> None:
        if result.succeeded:
            self._execute(self._fsm.handle(FsmEvent(EventType.NAV_SUCCEEDED)))
            return
        if kind == "frontier":
            self._blacklist.append(target)
        self._execute(self._fsm.handle(FsmEvent(EventType.NAV_FAILED)))

    def _start_spin(self) -> None:
        assert self._nav is not None
        if not self._nav.spin(float(self._param("spin_target_yaw")), self._spin_result):
            self.get_logger().error("Spin action server unavailable")
            self._execute(self._fsm.handle(FsmEvent(EventType.SPIN_FAILED)))

    def _spin_result(self, result) -> None:
        event = EventType.SPIN_SUCCEEDED if result.succeeded else EventType.SPIN_FAILED
        self._execute(self._fsm.handle(FsmEvent(event)))

    def stop(self) -> None:
        if self._nav is not None:
            self._nav.cancel()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = FrontierExplorerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            node.stop()
            node.destroy_node()
            if rclpy.ok():
                rclpy.shutdown()
        except KeyboardInterrupt:
            # Foxy may deliver a second SIGINT while launch is already tearing
            # down the node. Motion goals were canceled by the first stop call.
            pass

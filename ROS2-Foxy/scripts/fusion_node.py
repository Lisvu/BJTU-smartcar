#!/usr/bin/env python3
"""Fuse YOLO socket detections with LaserScan ranges.

Default mode is a dry run: it publishes zero Twist messages and prints the
decision that would be used. Passing --drive enables slow forward motion only
when every safety check is clear. Passing --follow makes the robot follow the
nearest fused person target.
"""

from __future__ import annotations

import json
import math
import socket
import statistics
import threading
import time
from typing import Any

import argparse
from collections import deque
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from sensor_msgs.msg import LaserScan


def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_valid_distance(distance_m: float | None) -> bool:
    return distance_m is not None and math.isfinite(distance_m) and distance_m > 0.0


def estimate_distance_from_bbox_height(
    bbox_height_px: float | None,
    image_height_px: float | None,
    bbox_distance_scale_m: float = 0.45,
    bbox_distance_min_m: float = 0.25,
    bbox_distance_max_m: float = 3.0,
) -> float | None:
    if bbox_height_px is None or image_height_px is None:
        return None
    if bbox_height_px <= 0.0 or image_height_px <= 0.0:
        return None
    height_ratio = bbox_height_px / image_height_px
    if height_ratio <= 0.0:
        return None
    return clamp(
        bbox_distance_scale_m / height_ratio,
        bbox_distance_min_m,
        bbox_distance_max_m,
    )


def compute_follow_command(
    distance_m: float | None,
    bearing_rad: float | None,
    follow_distance_m: float = 0.5,
    follow_deadband_m: float = 0.1,
    follow_kp_lin: float = 0.4,
    follow_v_max_mps: float = 0.15,
    follow_kp_ang: float = 1.2,
    follow_w_max_radps: float = 0.8,
) -> tuple[float, float]:
    if distance_m is None or bearing_rad is None:
        return 0.0, 0.0

    linear_x = 0.0
    if distance_m > follow_distance_m + follow_deadband_m:
        linear_x = clamp(
            follow_kp_lin * (distance_m - follow_distance_m),
            0.0,
            follow_v_max_mps,
        )

    angular_z = clamp(
        -follow_kp_ang * bearing_rad,
        -follow_w_max_radps,
        follow_w_max_radps,
    )
    return linear_x, angular_z


def compute_recapture_command(
    bearing_rad: float | None,
    recapture_w_radps: float = 0.35,
    follow_w_max_radps: float = 0.8,
) -> tuple[float, float]:
    if bearing_rad is None or abs(bearing_rad) < 1e-3:
        return 0.0, 0.0
    angular_z = -math.copysign(
        min(abs(recapture_w_radps), follow_w_max_radps),
        bearing_rad,
    )
    return 0.0, angular_z


class FollowTargetState:
    def __init__(
        self,
        dist_smooth_n: int = 5,
        dist_hold_s: float = 0.6,
        bbox_distance_fallback: bool = True,
        bbox_distance_scale_m: float = 0.45,
        bbox_distance_min_m: float = 0.25,
        bbox_distance_max_m: float = 3.0,
    ) -> None:
        self.dist_hold_s = dist_hold_s
        self.bbox_distance_fallback = bbox_distance_fallback
        self.bbox_distance_scale_m = bbox_distance_scale_m
        self.bbox_distance_min_m = bbox_distance_min_m
        self.bbox_distance_max_m = bbox_distance_max_m
        self._distances = deque(maxlen=max(1, int(dist_smooth_n)))
        self.last_valid_distance: float | None = None
        self.last_valid_time = 0.0
        self.last_person_bearing: float | None = None
        self.last_person_time = 0.0

    def resolve_distance(
        self,
        raw_distance_m: float | None,
        bbox_height_px: float | None,
        image_height_px: float | None,
        now_s: float,
    ) -> tuple[float | None, str]:
        if is_valid_distance(raw_distance_m):
            self.last_valid_distance = raw_distance_m
            self.last_valid_time = now_s
            return raw_distance_m, "lidar"

        if (
            self.last_valid_distance is not None
            and now_s - self.last_valid_time <= self.dist_hold_s
        ):
            return self.last_valid_distance, "hold"

        if self.bbox_distance_fallback:
            bbox_distance = estimate_distance_from_bbox_height(
                bbox_height_px,
                image_height_px,
                self.bbox_distance_scale_m,
                self.bbox_distance_min_m,
                self.bbox_distance_max_m,
            )
            if bbox_distance is not None:
                self.last_valid_distance = bbox_distance
                self.last_valid_time = now_s
                return bbox_distance, "bbox"

        return None, "none"

    def smooth_distance(self, distance_m: float) -> float:
        self._distances.append(distance_m)
        return sum(self._distances) / len(self._distances)

    def note_person(self, bearing_rad: float, now_s: float) -> None:
        self.last_person_bearing = bearing_rad
        self.last_person_time = now_s

    def clear_if_lost(self, now_s: float, lost_grace_s: float) -> None:
        if now_s - self.last_person_time > lost_grace_s:
            self._distances.clear()


class FusionNode(Node):
    def __init__(self, mode: str) -> None:
        super().__init__("bjtu_yolo_lidar_fusion")
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 5001)
        self.declare_parameter("hfov_deg", 60.0)
        self.declare_parameter("stop_distance_m", 1.0)
        self.declare_parameter("clear_distance_m", 1.15)
        self.declare_parameter("safety_distance_m", 0.3)
        self.declare_parameter("safety_front_half_angle_deg", 25.0)
        # 0.15 m is kept as a sensor floor so the robot ignores near-field lidar noise.
        self.declare_parameter("safety_ignore_below_m", 0.15)
        self.declare_parameter("scan_window_deg", 2.0)
        self.declare_parameter("scan_angle_offset_deg", 180.0)
        self.declare_parameter("invert_bearing", False)
        self.declare_parameter("distance_method", "median")
        self.declare_parameter("reconnect_delay_s", 1.0)
        self.declare_parameter("stale_timeout_s", 1.0)
        self.declare_parameter("forward_speed_mps", 0.12)
        self.declare_parameter("follow_distance_m", 0.5)
        self.declare_parameter("follow_deadband_m", 0.1)
        self.declare_parameter("follow_kp_lin", 0.4)
        self.declare_parameter("follow_v_max_mps", 0.15)
        self.declare_parameter("follow_kp_ang", 1.2)
        self.declare_parameter("follow_w_max_radps", 0.8)
        self.declare_parameter("recapture_w_radps", 0.35)
        self.declare_parameter("dist_smooth_n", 5)
        self.declare_parameter("dist_hold_s", 0.6)
        self.declare_parameter("bbox_distance_fallback", True)
        self.declare_parameter("bbox_distance_scale_m", 0.45)
        self.declare_parameter("bbox_distance_min_m", 0.25)
        self.declare_parameter("bbox_distance_max_m", 3.0)
        self.declare_parameter("lost_grace_s", 0.4)
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")

        self.mode = mode
        self.host = str(self.get_parameter("host").value)
        self.port = int(self.get_parameter("port").value)
        self.hfov_rad = math.radians(float(self.get_parameter("hfov_deg").value))
        self.stop_distance_m = float(self.get_parameter("stop_distance_m").value)
        self.clear_distance_m = float(self.get_parameter("clear_distance_m").value)
        self.safety_distance_m = float(self.get_parameter("safety_distance_m").value)
        self.safety_front_half_angle_rad = math.radians(
            float(self.get_parameter("safety_front_half_angle_deg").value)
        )
        self.safety_ignore_below_m = float(self.get_parameter("safety_ignore_below_m").value)
        self.scan_window_rad = math.radians(float(self.get_parameter("scan_window_deg").value))
        self.scan_angle_offset_rad = math.radians(float(self.get_parameter("scan_angle_offset_deg").value))
        self.invert_bearing = bool(self.get_parameter("invert_bearing").value)
        self.distance_method = str(self.get_parameter("distance_method").value).lower()
        self.reconnect_delay_s = float(self.get_parameter("reconnect_delay_s").value)
        self.stale_timeout_s = float(self.get_parameter("stale_timeout_s").value)
        self.forward_speed_mps = float(self.get_parameter("forward_speed_mps").value)
        self.follow_distance_m = float(self.get_parameter("follow_distance_m").value)
        self.follow_deadband_m = float(self.get_parameter("follow_deadband_m").value)
        self.follow_kp_lin = float(self.get_parameter("follow_kp_lin").value)
        self.follow_v_max_mps = float(self.get_parameter("follow_v_max_mps").value)
        self.follow_kp_ang = float(self.get_parameter("follow_kp_ang").value)
        self.follow_w_max_radps = float(self.get_parameter("follow_w_max_radps").value)
        self.recapture_w_radps = float(self.get_parameter("recapture_w_radps").value)
        self.dist_smooth_n = int(self.get_parameter("dist_smooth_n").value)
        self.dist_hold_s = float(self.get_parameter("dist_hold_s").value)
        self.bbox_distance_fallback = bool(self.get_parameter("bbox_distance_fallback").value)
        self.bbox_distance_scale_m = float(self.get_parameter("bbox_distance_scale_m").value)
        self.bbox_distance_min_m = float(self.get_parameter("bbox_distance_min_m").value)
        self.bbox_distance_max_m = float(self.get_parameter("bbox_distance_max_m").value)
        self.lost_grace_s = float(self.get_parameter("lost_grace_s").value)
        self.cmd_vel_topic = str(self.get_parameter("cmd_vel_topic").value)
        self._follow_state = FollowTargetState(
            dist_smooth_n=self.dist_smooth_n,
            dist_hold_s=self.dist_hold_s,
            bbox_distance_fallback=self.bbox_distance_fallback,
            bbox_distance_scale_m=self.bbox_distance_scale_m,
            bbox_distance_min_m=self.bbox_distance_min_m,
            bbox_distance_max_m=self.bbox_distance_max_m,
        )

        self._latest_scan: LaserScan | None = None
        self._latest_scan_time = 0.0
        self._latest_detection_time = 0.0
        self._scan_lock = threading.Lock()
        self._decision = "STOP"
        self._decision_reason = "startup"
        self._nearest_person_distance: float | None = None
        self._follow_target_distance: float | None = None
        self._follow_target_bearing: float | None = None
        self._last_publish_debug_time = 0.0
        self._last_publish_debug = ""
        self._stop_event = threading.Event()
        self.create_subscription(LaserScan, "/scan", self._scan_callback, 10)
        self._cmd_pub = self.create_publisher(Twist, self.cmd_vel_topic, 10)
        self.create_timer(0.1, self._publish_command)

        self._socket_thread = threading.Thread(target=self._socket_loop, daemon=True)
        self._socket_thread.start()
        self.get_logger().info(
            f"fusion listening for detections at tcp://{self.host}:{self.port}, "
            f"mode={self.mode}, "
            f"hfov={math.degrees(self.hfov_rad):.1f}deg "
            f"stop={self.stop_distance_m:.2f}m clear={self.clear_distance_m:.2f}m "
            f"safety={self.safety_distance_m:.2f}m "
            f"front_half_angle={math.degrees(self.safety_front_half_angle_rad):.1f}deg "
            f"follow_distance={self.follow_distance_m:.2f}m "
            f"follow_deadband={self.follow_deadband_m:.2f}m "
            f"dist_smooth_n={self.dist_smooth_n} dist_hold={self.dist_hold_s:.2f}s "
            f"lost_grace={self.lost_grace_s:.2f}s"
        )

    def destroy_node(self) -> bool:
        self._stop_event.set()
        self._publish_zero_burst()
        return super().destroy_node()

    def _publish_zero_burst(self) -> None:
        twist = Twist()
        for _ in range(3):
            self._cmd_pub.publish(twist)
            time.sleep(0.05)

    def _scan_callback(self, msg: LaserScan) -> None:
        with self._scan_lock:
            self._latest_scan = msg
            self._latest_scan_time = time.monotonic()

    def _socket_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=3.0) as sock:
                    self.get_logger().info(f"connected to yolo socket {self.host}:{self.port}")
                    file_obj = sock.makefile("r", encoding="utf-8")
                    for line in file_obj:
                        if self._stop_event.is_set():
                            return
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError as exc:
                            self.get_logger().warning(f"bad detection json: {exc}: {line[:120]}")
                            continue
                        self._latest_detection_time = time.monotonic()
                        self._handle_detection_payload(payload)
            except OSError as exc:
                self.get_logger().warning(
                    f"waiting for yolo socket {self.host}:{self.port}: {exc}"
                )
                time.sleep(self.reconnect_delay_s)

    def _handle_detection_payload(self, payload: dict[str, Any]) -> None:
        now = time.monotonic()
        width = float(payload.get("w") or 0.0)
        height = float(payload.get("h") or 0.0)
        if width <= 0:
            return
        person_distances: list[float] = []
        follow_targets: list[tuple[float, float, str, float | None]] = []
        person_bearings: list[float] = []
        for det in payload.get("dets", []):
            if det.get("cls") != "person":
                continue
            cx = float(det.get("cx", 0.0))
            bearing = (cx - width / 2.0) / width * self.hfov_rad
            if self.invert_bearing:
                bearing = -bearing
            person_bearings.append(bearing)
            scan_angle = normalize_angle(bearing + self.scan_angle_offset_rad)
            distance = self._distance_at_bearing(scan_angle)
            if distance is None:
                dist_text = "nan"
            else:
                person_distances.append(distance)
                dist_text = f"{distance:.2f}"
            follow_distance, follow_source = self._follow_state.resolve_distance(
                distance,
                float(det.get("bh", 0.0)),
                height,
                now,
            )
            if follow_distance is not None:
                follow_targets.append((follow_distance, bearing, follow_source, distance))
                follow_dist_text = f"{follow_distance:.2f}"
            else:
                follow_dist_text = "nan"
            decision = self._update_person_decision(person_distances)
            v, w = compute_follow_command(
                follow_distance,
                bearing if follow_distance is not None else None,
                self.follow_distance_m,
                self.follow_deadband_m,
                self.follow_kp_lin,
                self.follow_v_max_mps,
                self.follow_kp_ang,
                self.follow_w_max_radps,
            )
            print(
                f"person bearing={math.degrees(bearing):.1f}deg "
                f"dist={dist_text}m follow_dist={follow_dist_text}m "
                f"source={follow_source} decision={decision} "
                f"follow_v={v:.2f} follow_w={w:.2f}",
                flush=True,
            )
        if follow_targets:
            target_distance, target_bearing, target_source, _ = min(
                follow_targets,
                key=lambda item: item[0],
            )
            self._follow_state.note_person(target_bearing, now)
            target_distance = self._follow_state.smooth_distance(target_distance)
            self._follow_target_distance = target_distance
            self._follow_target_bearing = target_bearing
            print(
                f"follow_target source={target_source} "
                f"bearing={math.degrees(target_bearing):.1f}deg "
                f"smooth_dist={target_distance:.2f}m",
                flush=True,
            )
        elif person_bearings:
            self._follow_state.note_person(person_bearings[0], now)
            self._follow_target_distance = None
            self._follow_target_bearing = person_bearings[0]
        else:
            self._follow_target_distance = None
            self._follow_target_bearing = None
            self._follow_state.clear_if_lost(now, self.lost_grace_s)
        if not person_distances and not any(det.get("cls") == "person" for det in payload.get("dets", [])):
            self._nearest_person_distance = None
            self._decision = "CLEAR"
            self._decision_reason = "no_person"

    def _update_person_decision(self, distances: list[float]) -> str:
        if not distances:
            return self._decision
        nearest = min(distances)
        self._nearest_person_distance = nearest
        if nearest < self.stop_distance_m:
            self._decision = "STOP"
            self._decision_reason = f"person<{self.stop_distance_m:.2f}m"
        elif nearest > self.clear_distance_m:
            self._decision = "CLEAR"
            self._decision_reason = f"person>{self.clear_distance_m:.2f}m"
        else:
            self._decision_reason = "hysteresis_hold"
        return self._decision

    def _publish_command(self) -> None:
        now = time.monotonic()
        scan_stale = now - self._latest_scan_time > self.stale_timeout_s
        detection_stale = now - self._latest_detection_time > self.stale_timeout_s
        close_obstacle = self._has_close_obstacle()
        stop_reasons: list[str] = []
        if self.mode == "drive" and self._decision == "STOP":
            stop_reasons.append(self._decision_reason)
        if scan_stale:
            stop_reasons.append("scan_stale")
        if detection_stale:
            stop_reasons.append("detection_stale")
        if close_obstacle:
            stop_reasons.append("front_obstacle")
        if self.mode == "dry-run":
            stop_reasons.append("dry_run")
        should_stop = (
            bool(stop_reasons)
        )
        twist = Twist()
        if should_stop:
            pass
        elif self.mode == "drive":
            twist.linear.x = self.forward_speed_mps
        elif self.mode == "follow":
            twist.linear.x, twist.angular.z = self._compute_follow_twist(now)
        self._print_publish_debug(now, twist, stop_reasons)
        self._cmd_pub.publish(twist)

    def _print_publish_debug(self, now: float, twist: Twist, stop_reasons: list[str]) -> None:
        reasons = ",".join(stop_reasons) if stop_reasons else "ok"
        target_dist = "nan" if self._follow_target_distance is None else f"{self._follow_target_distance:.2f}"
        target_bearing = (
            "nan"
            if self._follow_target_bearing is None
            else f"{math.degrees(self._follow_target_bearing):.1f}"
        )
        message = (
            f"cmd mode={self.mode} v={twist.linear.x:.2f} w={twist.angular.z:.2f} "
            f"reason={reasons} target_dist={target_dist}m "
            f"target_bearing={target_bearing}deg"
        )
        if message != self._last_publish_debug or now - self._last_publish_debug_time > 0.5:
            print(message, flush=True)
            self._last_publish_debug = message
            self._last_publish_debug_time = now

    def _compute_follow_twist(self, now: float) -> tuple[float, float]:
        person_age = now - self._follow_state.last_person_time
        if self._follow_target_distance is not None and self._follow_target_bearing is not None:
            return compute_follow_command(
                self._follow_target_distance,
                self._follow_target_bearing,
                self.follow_distance_m,
                self.follow_deadband_m,
                self.follow_kp_lin,
                self.follow_v_max_mps,
                self.follow_kp_ang,
                self.follow_w_max_radps,
            )
        if 0.0 <= person_age <= self.lost_grace_s:
            return compute_recapture_command(
                self._follow_state.last_person_bearing,
                self.recapture_w_radps,
                self.follow_w_max_radps,
            )
        return 0.0, 0.0

    def _has_close_obstacle(self) -> bool:
        with self._scan_lock:
            scan = self._latest_scan
        if scan is None:
            return True

        front_angle = normalize_angle(self.scan_angle_offset_rad)
        for i, raw in enumerate(scan.ranges):
            beam_angle = normalize_angle(scan.angle_min + i * scan.angle_increment)
            if abs(normalize_angle(beam_angle - front_angle)) > self.safety_front_half_angle_rad:
                continue
            value = float(raw)
            if not math.isfinite(value):
                continue
            if value <= 0.0:
                continue
            if value < self.safety_ignore_below_m:
                continue
            if scan.range_min and value < scan.range_min:
                continue
            if value < self.safety_distance_m:
                return True
        return False

    def _distance_at_bearing(self, target_angle: float) -> float | None:
        with self._scan_lock:
            scan = self._latest_scan
        if scan is None or scan.angle_increment == 0.0 or not scan.ranges:
            return None

        index = self._nearest_index(scan, target_angle)
        if index is None:
            return None
        span = max(1, int(abs(self.scan_window_rad / scan.angle_increment)))
        values: list[float] = []
        for i in range(max(0, index - span), min(len(scan.ranges), index + span + 1)):
            value = float(scan.ranges[i])
            if not math.isfinite(value):
                continue
            if scan.range_min and value < scan.range_min:
                continue
            if scan.range_max and value > scan.range_max:
                continue
            if value <= 0.0:
                continue
            values.append(value)
        if not values:
            return None
        if self.distance_method == "min":
            return min(values)
        return float(statistics.median(values))

    def _nearest_index(self, scan: LaserScan, target_angle: float) -> int | None:
        count = len(scan.ranges)
        if count == 0:
            return None
        candidates = [target_angle, target_angle + 2.0 * math.pi, target_angle - 2.0 * math.pi]
        best_index: int | None = None
        best_error = float("inf")
        for candidate in candidates:
            raw = (candidate - scan.angle_min) / scan.angle_increment
            index = int(round(raw))
            if index < 0 or index >= count:
                continue
            angle = scan.angle_min + index * scan.angle_increment
            error = abs(normalize_angle(candidate - angle))
            if error < best_error:
                best_index = index
                best_error = error
        return best_index


def parse_args(args: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Fuse YOLO socket detections with LaserScan.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--drive", action="store_true", help="Enable forward motion when CLEAR. Default is dry-run zero Twist.")
    mode_group.add_argument("--follow", action="store_true", help="Follow the nearest fused person target.")
    return parser.parse_known_args(args)


def main(args: list[str] | None = None) -> None:
    cli_args, ros_args = parse_args(args)
    rclpy.init(args=ros_args)
    mode = "follow" if cli_args.follow else "drive" if cli_args.drive else "dry-run"
    node = FusionNode(mode=mode)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

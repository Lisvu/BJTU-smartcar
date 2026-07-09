#!/usr/bin/env python3
"""Drive command node for traffic-sign detections.

This node is independent from fusion_node.py. It consumes JSON detections from
detect_headless.py on a separate TCP port and maps the currently visible traffic
sign to a Twist command. Dry-run is the default and does not publish /cmd_vel.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import threading
import time
from typing import Any

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan


SIGN_ACTIONS = {
    "ahead": "AHEAD",
    "turn_left": "LEFT",
    "turn_right": "RIGHT",
    "stop": "STOP",
    "no_entry": "STOP",
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def select_main_sign(
    payload: dict[str, Any],
    conf_thres: float,
    act_ratio: float,
) -> dict[str, float | str] | None:
    image_height = safe_float(payload.get("h"))
    if image_height <= 0.0:
        return None

    candidates: list[dict[str, float | str]] = []
    for det in payload.get("dets", []):
        cls = str(det.get("cls", ""))
        if cls not in SIGN_ACTIONS:
            continue
        conf = safe_float(det.get("conf"))
        box_width = safe_float(det.get("bw"))
        box_height = safe_float(det.get("bh"))
        ratio = box_height / image_height
        if conf < conf_thres or ratio < act_ratio:
            continue
        candidates.append(
            {
                "cls": cls,
                "conf": conf,
                "ratio": ratio,
                "area": max(0.0, box_width) * max(0.0, box_height),
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda item: (float(item["conf"]), float(item["area"])))


def command_for_sign(
    cls: str | None,
    linear_speed_mps: float,
    angular_speed_radps: float,
    max_linear_speed_mps: float,
    max_angular_speed_radps: float,
) -> tuple[str, float, float]:
    linear = clamp(abs(linear_speed_mps), 0.0, abs(max_linear_speed_mps))
    angular = clamp(abs(angular_speed_radps), 0.0, abs(max_angular_speed_radps))
    if cls == "ahead":
        return "AHEAD", linear, 0.0
    if cls == "turn_left":
        return "LEFT", 0.0, angular
    if cls == "turn_right":
        return "RIGHT", 0.0, -angular
    return "STOP", 0.0, 0.0


class SignCommandNode(Node):
    def __init__(self, drive_enabled: bool) -> None:
        super().__init__("bjtu_traffic_sign_command")
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 5002)
        self.declare_parameter("conf_thres", 0.5)
        self.declare_parameter("act_ratio", 0.06)
        self.declare_parameter("stale_timeout_s", 1.0)
        self.declare_parameter("linear_speed_mps", 0.12)
        self.declare_parameter("angular_speed_radps", 0.6)
        self.declare_parameter("max_linear_speed_mps", 0.15)
        self.declare_parameter("max_angular_speed_radps", 0.8)
        self.declare_parameter("safety_distance_m", 0.3)
        self.declare_parameter("safety_front_half_angle_deg", 25.0)
        self.declare_parameter("safety_ignore_below_m", 0.15)
        self.declare_parameter("scan_angle_offset_deg", 180.0)
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")

        self.drive_enabled = drive_enabled
        self.host = str(self.get_parameter("host").value)
        self.port = int(self.get_parameter("port").value)
        self.conf_thres = float(self.get_parameter("conf_thres").value)
        self.act_ratio = float(self.get_parameter("act_ratio").value)
        self.stale_timeout_s = float(self.get_parameter("stale_timeout_s").value)
        self.linear_speed_mps = float(self.get_parameter("linear_speed_mps").value)
        self.angular_speed_radps = float(self.get_parameter("angular_speed_radps").value)
        self.max_linear_speed_mps = float(self.get_parameter("max_linear_speed_mps").value)
        self.max_angular_speed_radps = float(self.get_parameter("max_angular_speed_radps").value)
        self.safety_distance_m = float(self.get_parameter("safety_distance_m").value)
        self.safety_front_half_angle_rad = math.radians(
            float(self.get_parameter("safety_front_half_angle_deg").value)
        )
        self.safety_ignore_below_m = float(self.get_parameter("safety_ignore_below_m").value)
        self.scan_angle_offset_rad = math.radians(float(self.get_parameter("scan_angle_offset_deg").value))
        self.scan_topic = str(self.get_parameter("scan_topic").value)
        self.cmd_vel_topic = str(self.get_parameter("cmd_vel_topic").value)

        self._latest_scan: LaserScan | None = None
        self._scan_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._latest_detection_time = 0.0
        self._current_cls: str | None = None
        self._current_conf = 0.0
        self._current_ratio = 0.0
        self._current_action = "STOP"
        self._planned_linear = 0.0
        self._planned_angular = 0.0
        self._last_decision_line = ""
        self._last_decision_time = 0.0
        self._last_cmd_line = ""
        self._last_cmd_time = 0.0
        self._stop_event = threading.Event()

        self.create_subscription(LaserScan, self.scan_topic, self._scan_callback, qos_profile_sensor_data)
        self._cmd_pub = self.create_publisher(Twist, self.cmd_vel_topic, 10) if self.drive_enabled else None
        self.create_timer(0.1, self._publish_or_print_command)
        self._socket_thread = threading.Thread(target=self._socket_loop, daemon=True)
        self._socket_thread.start()
        self.get_logger().info(
            f"traffic sign command node socket=tcp://{self.host}:{self.port} "
            f"mode={'drive' if self.drive_enabled else 'dry-run'} "
            f"conf_thres={self.conf_thres:.2f} act_ratio={self.act_ratio:.2f} "
            f"linear={self.linear_speed_mps:.2f} angular={self.angular_speed_radps:.2f}"
        )

    def destroy_node(self) -> bool:
        self._stop_event.set()
        self._publish_zero_burst()
        return super().destroy_node()

    def _publish_zero_burst(self) -> None:
        if not self.drive_enabled or self._cmd_pub is None:
            return
        twist = Twist()
        for _ in range(3):
            self._cmd_pub.publish(twist)
            time.sleep(0.05)

    def _scan_callback(self, msg: LaserScan) -> None:
        with self._scan_lock:
            self._latest_scan = msg

    def _socket_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=3.0) as sock:
                    self.get_logger().info(f"connected to sign socket {self.host}:{self.port}")
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
                            self.get_logger().warning(f"bad sign json: {exc}: {line[:120]}")
                            continue
                        self._handle_detection_payload(payload)
            except OSError as exc:
                self.get_logger().warning(
                    f"waiting for sign socket {self.host}:{self.port}: {exc}"
                )
                time.sleep(1.0)

    def _handle_detection_payload(self, payload: dict[str, Any]) -> None:
        now = time.monotonic()
        main_sign = select_main_sign(payload, self.conf_thres, self.act_ratio)
        with self._state_lock:
            self._latest_detection_time = now
            if main_sign is None:
                self._current_cls = None
                self._current_conf = 0.0
                self._current_ratio = 0.0
                self._current_action = "STOP"
                self._planned_linear = 0.0
                self._planned_angular = 0.0
                self._print_decision("no sign -> STOP", now)
                return

            cls = str(main_sign["cls"])
            action, linear, angular = command_for_sign(
                cls,
                self.linear_speed_mps,
                self.angular_speed_radps,
                self.max_linear_speed_mps,
                self.max_angular_speed_radps,
            )
            self._current_cls = cls
            self._current_conf = float(main_sign["conf"])
            self._current_ratio = float(main_sign["ratio"])
            self._current_action = action
            self._planned_linear = linear
            self._planned_angular = angular
            self._print_decision(
                f"cls={cls} conf={self._current_conf:.2f} ratio={self._current_ratio:.2f} -> {action}",
                now,
            )

    def _print_decision(self, line: str, now: float) -> None:
        if line != self._last_decision_line or now - self._last_decision_time > 0.5:
            print(line, flush=True)
            self._last_decision_line = line
            self._last_decision_time = now

    def _publish_or_print_command(self) -> None:
        now = time.monotonic()
        with self._state_lock:
            stale = now - self._latest_detection_time > self.stale_timeout_s
            no_sign = self._current_cls is None
            action = self._current_action
            linear = self._planned_linear
            angular = self._planned_angular

        stop_reasons: list[str] = []
        if stale:
            stop_reasons.append("detection_stale")
        if no_sign:
            stop_reasons.append("no_sign")
        if self._has_close_obstacle():
            stop_reasons.append("front_obstacle")

        twist = Twist()
        if not stop_reasons:
            twist.linear.x = linear
            twist.angular.z = angular

        self._print_command(now, action, twist, stop_reasons)
        if self.drive_enabled and self._cmd_pub is not None:
            self._cmd_pub.publish(twist)

    def _print_command(self, now: float, action: str, twist: Twist, stop_reasons: list[str]) -> None:
        reasons = ",".join(stop_reasons) if stop_reasons else "ok"
        mode = "drive" if self.drive_enabled else "dry-run"
        line = (
            f"cmd mode={mode} action={action} v={twist.linear.x:.2f} "
            f"w={twist.angular.z:.2f} reason={reasons}"
        )
        if line != self._last_cmd_line or now - self._last_cmd_time > 0.5:
            print(line, flush=True)
            self._last_cmd_line = line
            self._last_cmd_time = now

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
            value = safe_float(raw, default=float("nan"))
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


def parse_args(args: list[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(description="Map traffic-sign detections to Twist commands.")
    parser.add_argument("--drive", action="store_true", help="Publish Twist commands. Default is dry-run printing only.")
    return parser.parse_known_args(args)


def main(args: list[str] | None = None) -> None:
    cli_args, ros_args = parse_args(args)
    rclpy.init(args=ros_args)
    node = SignCommandNode(drive_enabled=cli_args.drive)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

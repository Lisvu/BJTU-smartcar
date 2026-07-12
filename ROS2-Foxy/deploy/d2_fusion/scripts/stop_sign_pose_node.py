#!/usr/bin/env python3
"""Locate STOP detections in base_link and map using a LaserScan."""

from __future__ import annotations

import json
import math
import socket
import statistics
import threading
import time

import numpy as np
import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.node import Node
from sensor_msgs.msg import Image, LaserScan
from tf2_ros import Buffer, TransformException, TransformListener


def normalize_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle <= -math.pi:
        angle += 2.0 * math.pi
    return angle


class StopSignPoseNode(Node):
    def __init__(self) -> None:
        super().__init__("bjtu_stop_sign_pose")
        self.declare_parameter("host", "127.0.0.1")
        self.declare_parameter("port", 5002)
        self.declare_parameter("target_class", "stop")
        self.declare_parameter("conf_thres", 0.5)
        self.declare_parameter("hfov_deg", 60.0)
        self.declare_parameter("invert_bearing", False)
        self.declare_parameter("scan_angle_offset_deg", 180.0)
        self.declare_parameter("scan_window_deg", 2.0)
        self.declare_parameter("distance_method", "median")
        self.declare_parameter("depth_topic", "/camera/depth/image_raw")
        self.declare_parameter("depth_roi_scale", 0.6)
        self.declare_parameter("depth_min_m", 0.2)
        self.declare_parameter("depth_max_m", 8.0)
        self.declare_parameter("depth_min_valid_pixels", 20)
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("base_frame", "base_link")
        self.declare_parameter("map_frame", "map")

        self.host = str(self.get_parameter("host").value)
        self.port = int(self.get_parameter("port").value)
        self.target_class = str(self.get_parameter("target_class").value)
        self.conf_thres = float(self.get_parameter("conf_thres").value)
        self.hfov_rad = math.radians(float(self.get_parameter("hfov_deg").value))
        self.invert_bearing = bool(self.get_parameter("invert_bearing").value)
        self.scan_angle_offset_rad = math.radians(
            float(self.get_parameter("scan_angle_offset_deg").value)
        )
        self.scan_window_rad = math.radians(
            float(self.get_parameter("scan_window_deg").value)
        )
        self.distance_method = str(self.get_parameter("distance_method").value)
        self.depth_roi_scale = float(self.get_parameter("depth_roi_scale").value)
        self.depth_min_m = float(self.get_parameter("depth_min_m").value)
        self.depth_max_m = float(self.get_parameter("depth_max_m").value)
        self.depth_min_valid_pixels = int(
            self.get_parameter("depth_min_valid_pixels").value
        )
        self.base_frame = str(self.get_parameter("base_frame").value)
        self.map_frame = str(self.get_parameter("map_frame").value)

        self._scan: LaserScan | None = None
        self._depth: Image | None = None
        self._scan_lock = threading.Lock()
        self._depth_lock = threading.Lock()
        self._stop_event = threading.Event()
        self.create_subscription(
            LaserScan,
            str(self.get_parameter("scan_topic").value),
            self._scan_callback,
            10,
        )
        self.create_subscription(
            Image,
            str(self.get_parameter("depth_topic").value),
            self._depth_callback,
            10,
        )
        self._base_pub = self.create_publisher(PoseStamped, "/bjtu/stop_pose_base", 10)
        self._map_pub = self.create_publisher(PoseStamped, "/bjtu/stop_pose_map", 10)
        self._tf_buffer = Buffer()
        self._tf_listener = TransformListener(self._tf_buffer, self)
        self._socket_thread = threading.Thread(target=self._socket_loop, daemon=True)
        self._socket_thread.start()
        self.get_logger().info(
            f"STOP pose fusion tcp://{self.host}:{self.port} hfov="
            f"{math.degrees(self.hfov_rad):.1f}deg invert_bearing={self.invert_bearing} "
            f"scan_angle_offset={math.degrees(self.scan_angle_offset_rad):.1f}deg; "
            "range_source=registered_depth; lidar is validation only; "
            "no cmd_vel publisher is created"
        )

    def destroy_node(self) -> bool:
        self._stop_event.set()
        return super().destroy_node()

    def _scan_callback(self, msg: LaserScan) -> None:
        with self._scan_lock:
            self._scan = msg

    def _depth_callback(self, msg: Image) -> None:
        with self._depth_lock:
            self._depth = msg

    def _socket_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with socket.create_connection((self.host, self.port), timeout=3.0) as sock:
                    self.get_logger().info(f"connected to detection socket {self.host}:{self.port}")
                    for line in sock.makefile("r", encoding="utf-8"):
                        if self._stop_event.is_set():
                            return
                        try:
                            self._handle_payload(json.loads(line))
                        except (ValueError, TypeError, json.JSONDecodeError) as exc:
                            self.get_logger().warning(f"invalid detection payload: {exc}")
            except OSError as exc:
                self.get_logger().warning(f"waiting for detection socket: {exc}")
                time.sleep(1.0)

    def _handle_payload(self, payload: dict) -> None:
        width = float(payload.get("w") or 0.0)
        if width <= 0.0:
            return
        candidates = [
            det
            for det in payload.get("dets", [])
            if det.get("cls") == self.target_class
            and float(det.get("conf", 0.0)) >= self.conf_thres
        ]
        if not candidates:
            return
        det = max(candidates, key=lambda item: float(item.get("conf", 0.0)))
        cx = float(det.get("cx", 0.0))
        bearing = (cx - width / 2.0) / width * self.hfov_rad
        if self.invert_bearing:
            bearing = -bearing
        scan_angle = normalize_angle(bearing + self.scan_angle_offset_rad)
        distance = self._depth_in_bbox(
            cx,
            float(det.get("cy", 0.0)),
            float(det.get("bw", 0.0)),
            float(det.get("bh", 0.0)),
            width,
            float(payload.get("h") or 0.0),
        )
        lidar_check = self._distance_at_angle(scan_angle)
        if distance is None:
            print(
                f"STOP conf={float(det.get('conf', 0.0)):.3f} "
                f"cx={cx:.0f} bbox=({float(det.get('bw', 0.0)):.0f},"
                f"{float(det.get('bh', 0.0)):.0f}) "
                f"bearing={math.degrees(bearing):+.1f}deg depth=nan "
                f"lidar_check={lidar_check if lidar_check is not None else float('nan'):.2f}m",
                flush=True,
            )
            return
        self._publish_poses(
            distance,
            bearing,
            float(det.get("conf", 0.0)),
            cx,
            float(det.get("bw", 0.0)),
            float(det.get("bh", 0.0)),
            lidar_check,
        )

    def _depth_in_bbox(
        self,
        cx: float,
        cy: float,
        bbox_width: float,
        bbox_height: float,
        image_width: float,
        image_height: float,
    ) -> float | None:
        with self._depth_lock:
            depth = self._depth
        if depth is None or depth.encoding != "16UC1" or image_width <= 0 or image_height <= 0:
            return None
        values = np.frombuffer(depth.data, dtype=np.uint16)
        if values.size != depth.width * depth.height:
            return None
        image = values.reshape((depth.height, depth.width))
        sx = depth.width / image_width
        sy = depth.height / image_height
        half_width = max(1.0, bbox_width * self.depth_roi_scale * 0.5 * sx)
        half_height = max(1.0, bbox_height * self.depth_roi_scale * 0.5 * sy)
        center_x = cx * sx
        center_y = cy * sy
        x1 = max(0, int(center_x - half_width))
        x2 = min(depth.width, int(center_x + half_width) + 1)
        y1 = max(0, int(center_y - half_height))
        y2 = min(depth.height, int(center_y + half_height) + 1)
        roi_m = image[y1:y2, x1:x2].astype(np.float32) * 0.001
        valid = roi_m[
            np.isfinite(roi_m)
            & (roi_m >= self.depth_min_m)
            & (roi_m <= self.depth_max_m)
        ]
        if valid.size < self.depth_min_valid_pixels:
            return None
        return float(np.median(valid))

    def _distance_at_angle(self, target_angle: float) -> float | None:
        with self._scan_lock:
            scan = self._scan
        if scan is None or not scan.ranges or scan.angle_increment == 0.0:
            return None
        indices: list[int] = []
        for angle in (target_angle, target_angle + 2.0 * math.pi, target_angle - 2.0 * math.pi):
            index = int(round((angle - scan.angle_min) / scan.angle_increment))
            if 0 <= index < len(scan.ranges):
                indices.append(index)
        if not indices:
            return None
        index = min(
            indices,
            key=lambda i: abs(normalize_angle(target_angle - (scan.angle_min + i * scan.angle_increment))),
        )
        span = max(1, int(abs(self.scan_window_rad / scan.angle_increment)))
        values = []
        for i in range(max(0, index - span), min(len(scan.ranges), index + span + 1)):
            value = float(scan.ranges[i])
            if not math.isfinite(value) or value <= 0.0:
                continue
            if scan.range_min and value < scan.range_min:
                continue
            if scan.range_max and value > scan.range_max:
                continue
            values.append(value)
        if not values:
            return None
        return min(values) if self.distance_method == "min" else float(statistics.median(values))

    def _publish_poses(
        self,
        distance: float,
        bearing: float,
        confidence: float,
        cx: float,
        bbox_width: float,
        bbox_height: float,
        lidar_check: float | None,
    ) -> None:
        now = self.get_clock().now().to_msg()
        base_pose = PoseStamped()
        base_pose.header.stamp = now
        base_pose.header.frame_id = self.base_frame
        base_pose.pose.position.x = distance * math.cos(bearing)
        base_pose.pose.position.y = distance * math.sin(bearing)
        base_pose.pose.orientation.z = math.sin(bearing / 2.0)
        base_pose.pose.orientation.w = math.cos(bearing / 2.0)
        self._base_pub.publish(base_pose)

        try:
            transform = self._tf_buffer.lookup_transform(
                self.map_frame, self.base_frame, rclpy.time.Time()
            )
        except TransformException as exc:
            self.get_logger().warning(f"map transform unavailable: {exc}")
            return
        q = transform.transform.rotation
        map_yaw = math.atan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        map_pose = PoseStamped()
        map_pose.header.stamp = now
        map_pose.header.frame_id = self.map_frame
        map_pose.pose.position.x = transform.transform.translation.x + math.cos(map_yaw) * base_pose.pose.position.x - math.sin(map_yaw) * base_pose.pose.position.y
        map_pose.pose.position.y = transform.transform.translation.y + math.sin(map_yaw) * base_pose.pose.position.x + math.cos(map_yaw) * base_pose.pose.position.y
        target_yaw = normalize_angle(map_yaw + bearing)
        map_pose.pose.orientation.z = math.sin(target_yaw / 2.0)
        map_pose.pose.orientation.w = math.cos(target_yaw / 2.0)
        self._map_pub.publish(map_pose)
        print(
            f"STOP conf={confidence:.3f} cx={cx:.0f} "
            f"bbox=({bbox_width:.0f},{bbox_height:.0f}) "
            f"bearing={math.degrees(bearing):+.1f}deg "
            f"range={distance:.2f}m source=depth "
            f"lidar_check={lidar_check if lidar_check is not None else float('nan'):.2f}m "
            f"base=({base_pose.pose.position.x:.2f},"
            f"{base_pose.pose.position.y:.2f}) map=({map_pose.pose.position.x:.2f},"
            f"{map_pose.pose.position.y:.2f})",
            flush=True,
        )


def main() -> None:
    rclpy.init()
    node = StopSignPoseNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

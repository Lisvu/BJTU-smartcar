#!/usr/bin/env python3
import json
import math
import os
import re
import signal
import socket
import shlex
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import rclpy
from geometry_msgs.msg import PoseStamped, Quaternion, Twist
from nav_msgs.msg import OccupancyGrid, Odometry, Path as NavPath
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image as RosImage
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Float32, String
from geometry_msgs.msg import PoseWithCovarianceStamped

try:
    import cv2
except Exception:
    cv2 = None

try:
    import numpy as np
except Exception:
    np = None


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAP_DIRS = [
    Path("/root/maps"),
    Path("/root/yahboomcar_ros2_ws/yahboomcar_ws/src/yahboomcar_nav/maps"),
]
SELECTED_MAP = None
BJTU_DETECT_TIMEOUT_S = 2.0
BJTU_FEATURE_LOG_DIR = Path("/tmp")

FRONT_CENTER_DEG = 0.0
FRONT_HALF_WIDTH_DEG = 35.0
DEFAULT_STOP_DISTANCE_M = 0.35
SENSOR_DEFAULT_HOST = "192.168.1.11"
SENSOR_DEFAULT_PORT = 8888
SENSOR_TIMEOUT_S = 2.5
CMD_TIMEOUT_S = 0.45
CMD_WATCHDOG_INTERVAL_S = 0.05


def yaw_to_quaternion(yaw):
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


def quaternion_to_yaw(q):
    siny = 2.0 * (q.w * q.z + q.x * q.y)
    cosy = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny, cosy)


class CameraStream:
    def __init__(self, device=None):
        self.device = device
        self.cap = None
        self.lock = threading.Lock()
        self.last_error = None
        self.ros_frame = None
        self.ros_topic = None
        self.ros_frame_time = 0.0

    def candidate_devices(self):
        if self.device:
            return [self.device]
        return [f"/dev/video{i}" for i in range(8)]

    def try_open_device(self, device):
        cap = cv2.VideoCapture(device)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 20)
        ok = False
        for _ in range(5):
            ret, frame = cap.read()
            if ret and frame is not None:
                ok = True
                break
            time.sleep(0.05)
        if not ok:
            cap.release()
            return None
        return cap

    def open(self):
        if cv2 is None:
            self.last_error = "OpenCV is not available"
            return False
        with self.lock:
            if self.cap and self.cap.isOpened():
                return True
            errors = []
            for device in self.candidate_devices():
                cap = self.try_open_device(device)
                if cap is None:
                    errors.append(device)
                    continue
                self.cap = cap
                self.device = device
                self.last_error = None
                return True
            self.cap = None
            self.last_error = f"Cannot open camera from: {', '.join(errors)}"
            return False

    def reset(self):
        with self.lock:
            if self.cap:
                self.cap.release()
            self.cap = None
            self.device = None
            self.last_error = None
            self.ros_frame = None
            self.ros_topic = None
            self.ros_frame_time = 0.0

    def update_ros_image(self, topic, msg):
        if cv2 is None or np is None:
            return
        try:
            frame = self.ros_image_to_bgr(msg)
            if frame is None:
                return
            ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            if not ok:
                return
            with self.lock:
                self.ros_frame = encoded.tobytes()
                self.ros_topic = topic
                self.ros_frame_time = time.time()
                self.last_error = None
        except Exception as exc:
            with self.lock:
                self.last_error = f"ROS image decode failed: {exc}"

    def ros_image_to_bgr(self, msg):
        enc = msg.encoding.lower()
        height = int(msg.height)
        width = int(msg.width)
        if height <= 0 or width <= 0:
            return None
        if enc in ("bgr8", "rgb8"):
            arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(height, int(msg.step))[:, :width * 3]
            arr = arr.reshape(height, width, 3)
            return arr if enc == "bgr8" else cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        if enc in ("bgra8", "rgba8"):
            arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(height, int(msg.step))[:, :width * 4]
            arr = arr.reshape(height, width, 4)
            code = cv2.COLOR_BGRA2BGR if enc == "bgra8" else cv2.COLOR_RGBA2BGR
            return cv2.cvtColor(arr, code)
        if enc in ("mono8", "8uc1"):
            arr = np.frombuffer(msg.data, dtype=np.uint8).reshape(height, int(msg.step))[:, :width]
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        if enc in ("16uc1", "mono16"):
            arr = np.frombuffer(msg.data, dtype=np.uint16).reshape(height, int(msg.step) // 2)[:, :width]
            valid = arr[arr > 0]
            if valid.size:
                lo = max(1, int(np.percentile(valid, 2)))
                hi = max(lo + 1, int(np.percentile(valid, 98)))
            else:
                lo, hi = 1, 5000
            gray = np.clip((arr.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
            return cv2.applyColorMap(gray, cv2.COLORMAP_TURBO)
        return None

    def frame(self):
        with self.lock:
            if self.ros_frame and time.time() - self.ros_frame_time < 2.0:
                return self.ros_frame
        if self.open():
            with self.lock:
                ok, frame = self.cap.read()
            if not ok:
                self.last_error = "Camera frame read failed"
                with self.lock:
                    if self.cap:
                        self.cap.release()
                    self.cap = None
            else:
                ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                if ok:
                    return encoded.tobytes()
                self.last_error = "JPEG encode failed"
        with self.lock:
            return self.ros_frame

    def status(self):
        with self.lock:
            ros_age = time.time() - self.ros_frame_time if self.ros_frame_time else None
            ros_open = bool(self.ros_frame and ros_age is not None and ros_age < 2.0)
            cv_open = bool(self.cap and self.cap.isOpened())
            return {
                "available": cv2 is not None,
                "device": self.device,
                "open": cv_open or ros_open,
                "source": self.ros_topic if ros_open else self.device,
                "ros_age": ros_age,
                "error": self.last_error,
            }


class RosBridge(Node):
    def __init__(self):
        super().__init__(f"smartcar_web_bridge_{os.getpid()}")
        self.cmd_pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.goal_pub = self.create_publisher(PoseStamped, "/goal_pose", 10)
        self.initial_pose_pub = self.create_publisher(PoseWithCovarianceStamped, "/initialpose", 10)
        self._subscriptions = [
            self.create_subscription(Float32, "/voltage", self.on_voltage, 10),
            self.create_subscription(LaserScan, "/scan", self.on_scan, 10),
            self.create_subscription(OccupancyGrid, "/map", self.on_map, 1),
            self.create_subscription(NavPath, "/plan", self.on_plan, 1),
            self.create_subscription(Odometry, "/odom", self.on_odom, 20),
            self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self.on_amcl_pose, 10),
            self.create_subscription(PoseStamped, "/bjtu/stop_pose_base", lambda msg: self.on_stop_pose("base", msg), 10),
            self.create_subscription(PoseStamped, "/bjtu/stop_pose_map", lambda msg: self.on_stop_pose("map", msg), 10),
            self.create_subscription(String, "/bjtu_chatter", self.on_bjtu_chatter, 10),
            self.create_subscription(RosImage, "/camera/color/image_raw", lambda msg: camera.update_ros_image("/camera/color/image_raw", msg), qos_profile_sensor_data),
            self.create_subscription(RosImage, "/camera/ir/image_raw", lambda msg: camera.update_ros_image("/camera/ir/image_raw", msg), qos_profile_sensor_data),
            self.create_subscription(RosImage, "/camera/depth/image_raw", lambda msg: camera.update_ros_image("/camera/depth/image_raw", msg), qos_profile_sensor_data),
            self.create_subscription(RosImage, "/camera/depth_registered/image_raw", lambda msg: camera.update_ros_image("/camera/depth_registered/image_raw", msg), qos_profile_sensor_data),
        ]

        self.voltage = None
        self.front_distance = None
        self.map = None
        self.plan = []
        self.scan_points = []
        self.pose = None
        self.map_pose = None
        self.obstacle_guard = True
        self.stop_distance = DEFAULT_STOP_DISTANCE_M
        self.last_scan_time = 0.0
        self.last_map_time = 0.0
        self.last_odom_time = 0.0
        self.last_cmd = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.initial_pose = None
        self.last_motion_cmd_time = 0.0
        self.stop_poses = {}
        self.bjtu_chatter = None
        self.bjtu_chatter_time = 0.0
        self.lock = threading.Lock()
        self.watchdog_thread = threading.Thread(target=self.command_watchdog, daemon=True)
        self.watchdog_thread.start()

    def on_voltage(self, msg):
        with self.lock:
            self.voltage = float(msg.data)

    def on_map(self, msg):
        width = msg.info.width
        height = msg.info.height
        data = list(msg.data)
        with self.lock:
            self.map = {
                "width": width,
                "height": height,
                "resolution": float(msg.info.resolution),
                "origin": {
                    "x": float(msg.info.origin.position.x),
                    "y": float(msg.info.origin.position.y),
                    "theta": quaternion_to_yaw(msg.info.origin.orientation),
                },
                "data": data,
            }
            self.last_map_time = time.time()

    def on_plan(self, msg):
        poses = []
        step = max(1, len(msg.poses) // 300)
        for item in msg.poses[::step]:
            poses.append({
                "x": float(item.pose.position.x),
                "y": float(item.pose.position.y),
            })
        with self.lock:
            self.plan = poses

    def on_odom(self, msg):
        pose = msg.pose.pose
        with self.lock:
            self.pose = {
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "theta": quaternion_to_yaw(pose.orientation),
            }
            self.last_odom_time = time.time()

    def on_amcl_pose(self, msg):
        pose = msg.pose.pose
        with self.lock:
            self.map_pose = {
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "theta": quaternion_to_yaw(pose.orientation),
            }
            self.last_odom_time = time.time()

    def on_stop_pose(self, frame, msg):
        pose = msg.pose
        with self.lock:
            self.stop_poses[frame] = {
                "frame_id": msg.header.frame_id,
                "stamp": {
                    "sec": int(msg.header.stamp.sec),
                    "nanosec": int(msg.header.stamp.nanosec),
                },
                "x": float(pose.position.x),
                "y": float(pose.position.y),
                "z": float(pose.position.z),
                "theta": quaternion_to_yaw(pose.orientation),
                "received_at": time.time(),
            }

    def on_bjtu_chatter(self, msg):
        with self.lock:
            self.bjtu_chatter = str(msg.data)
            self.bjtu_chatter_time = time.time()

    def on_scan(self, msg):
        front_min = math.inf
        angle = msg.angle_min
        points = []
        step = max(1, len(msg.ranges) // 360)
        for idx, r in enumerate(msg.ranges):
            if math.isfinite(r) and msg.range_min <= r <= msg.range_max:
                deg = math.degrees(angle)
                diff = (deg - FRONT_CENTER_DEG + 180.0) % 360.0 - 180.0
                if abs(diff) <= FRONT_HALF_WIDTH_DEG:
                    front_min = min(front_min, float(r))
                if idx % step == 0:
                    points.append({
                        "x": float(math.cos(angle) * r),
                        "y": float(math.sin(angle) * r),
                    })
            angle += msg.angle_increment
        with self.lock:
            self.front_distance = None if math.isinf(front_min) else front_min
            self.scan_points = points
            self.last_scan_time = time.time()

    def status(self):
        with self.lock:
            scan_age = time.time() - self.last_scan_time if self.last_scan_time else None
            obstacle = (
                self.front_distance is not None
                and self.front_distance <= self.stop_distance
            )
            return {
                "voltage": self.voltage,
                "front_distance": self.front_distance,
                "scan_age": scan_age,
                "obstacle_guard": self.obstacle_guard,
                "stop_distance": self.stop_distance,
                "obstacle": obstacle,
                "last_cmd": self.last_cmd,
                "cmd_timeout": CMD_TIMEOUT_S,
                "cmd_age": (
                    time.time() - self.last_motion_cmd_time
                    if self.last_motion_cmd_time else None
                ),
                "map": None if not self.map else {
                    "width": self.map["width"],
                    "height": self.map["height"],
                    "resolution": self.map["resolution"],
                },
                "plan_len": len(self.plan),
                "pose": self.map_pose or self.pose,
                "pose_source": "amcl" if self.map_pose else "odom",
                "camera": camera.status(),
                "processes": ProcessManager.status(),
                "bjtu": self.bjtu_status_locked(),
            }

    def bjtu_status_locked(self):
        now = time.time()
        stop_poses = {}
        for key, value in self.stop_poses.items():
            item = dict(value)
            item["age"] = now - item.get("received_at", now)
            stop_poses[key] = item
        return {
            "stop_poses": stop_poses,
            "chatter": self.bjtu_chatter,
            "chatter_age": now - self.bjtu_chatter_time if self.bjtu_chatter_time else None,
        }

    def visualization(self):
        with self.lock:
            return {
                "map": self.map,
                "scan": self.scan_points,
                "pose": self.map_pose or self.pose,
                "plan": self.plan,
                "ages": {
                    "scan": time.time() - self.last_scan_time if self.last_scan_time else None,
                    "map": time.time() - self.last_map_time if self.last_map_time else None,
                    "odom": time.time() - self.last_odom_time if self.last_odom_time else None,
                },
            }

    def clear_navigation_overlay(self):
        with self.lock:
            self.plan = []
            self.initial_pose = None
            self.stop_poses = {}
        self.stop()
        return {"ok": True, "plan_len": 0}

    def clear_mapping_state(self):
        with self.lock:
            self.map = None
            self.plan = []
            self.scan_points = []
            self.map_pose = None
            self.last_map_time = 0.0
        return {"ok": True}

    def _publish_twist(self, linear_x=0.0, linear_y=0.0, angular_z=0.0):
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.linear.y = float(linear_y)
        msg.angular.z = float(angular_z)
        self.cmd_pub.publish(msg)
        with self.lock:
            self.last_cmd = {
                "linear_x": msg.linear.x,
                "linear_y": msg.linear.y,
                "angular_z": msg.angular.z,
            }
            if msg.linear.x or msg.linear.y or msg.angular.z:
                self.last_motion_cmd_time = time.time()
            else:
                self.last_motion_cmd_time = 0.0
        return self.last_cmd

    def publish_cmd(self, linear_x=0.0, linear_y=0.0, angular_z=0.0):
        with self.lock:
            blocked = (
                self.obstacle_guard
                and linear_x > 0
                and self.front_distance is not None
                and self.front_distance <= self.stop_distance
            )
        if blocked:
            linear_x = 0.0
            linear_y = 0.0
            angular_z = 0.0

        cmd = self._publish_twist(linear_x, linear_y, angular_z)
        return {
            "ok": True,
            "blocked": blocked,
            "cmd": cmd,
        }

    def stop(self):
        for _ in range(8):
            self._publish_twist(0.0, 0.0, 0.0)
            time.sleep(0.02)
        return {"ok": True, "cmd": self.last_cmd}

    def command_watchdog(self):
        while rclpy.ok():
            should_stop = False
            with self.lock:
                if self.last_motion_cmd_time:
                    age = time.time() - self.last_motion_cmd_time
                    moving = any(abs(v) > 1e-6 for v in self.last_cmd.values())
                    should_stop = moving and age > CMD_TIMEOUT_S
            if should_stop:
                msg = Twist()
                self.cmd_pub.publish(msg)
                with self.lock:
                    self.last_cmd = {
                        "linear_x": 0.0,
                        "linear_y": 0.0,
                        "angular_z": 0.0,
                    }
                    self.last_motion_cmd_time = 0.0
            time.sleep(CMD_WATCHDOG_INTERVAL_S)

    def set_guard(self, enabled=None, stop_distance=None):
        with self.lock:
            if enabled is not None:
                self.obstacle_guard = bool(enabled)
            if stop_distance is not None:
                self.stop_distance = float(stop_distance)
        return self.status()

    def publish_goal(self, x, y, theta):
        msg = PoseStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.position.x = float(x)
        msg.pose.position.y = float(y)
        msg.pose.orientation = yaw_to_quaternion(float(theta))
        self.goal_pub.publish(msg)
        return {"ok": True, "goal": {"x": x, "y": y, "theta": theta}}

    def publish_initial_pose(self, x, y, theta):
        pose_data = {"x": float(x), "y": float(y), "theta": float(theta)}
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = "map"
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x = pose_data["x"]
        msg.pose.pose.position.y = pose_data["y"]
        msg.pose.pose.orientation = yaw_to_quaternion(pose_data["theta"])
        msg.pose.covariance[0] = 0.25
        msg.pose.covariance[7] = 0.25
        msg.pose.covariance[35] = 0.06853891945200942
        for _ in range(5):
            msg.header.stamp = self.get_clock().now().to_msg()
            self.initial_pose_pub.publish(msg)
            time.sleep(0.08)
        with self.lock:
            self.initial_pose = pose_data
        return {"ok": True, "initial_pose": pose_data, "published": 5}

    def republish_initial_pose(self):
        with self.lock:
            pose = self.initial_pose
        if not pose:
            return
        for _ in range(3):
            self.publish_initial_pose(pose["x"], pose["y"], pose["theta"])
            time.sleep(0.25)


class ProcessManager:
    processes = {}
    logs = {}
    selected_map = None
    external_patterns = {
        "bringup": (
            "laser_bringup_launch.py|Mcnamu_driver_X3|base_node_X3|sllidar_node|"
            "ekf_node|imu_filter_madgwick_node|yahboom_joy_X3|joint_state_publisher|"
            "robot_state_publisher|static_transform_publisher"
        ),
        "camera": "astra_camera_node|astra.launch.xml",
        "slam": "slam_gmapping|map_gmapping_launch.py",
        "nav_dwa": "navigation_dwa_launch.py",
        "nav_teb": "navigation_teb_launch.py",
        "rviz_map": "display_map_launch.py",
        "rviz_nav": "display_nav_launch.py",
        "voice_ctrl": "Voice_Ctrl_Mcnamu_driver_X3",
    }

    ROS_ENV = (
        "source /opt/ros/foxy/setup.bash && "
        "source /root/yahboomcar_ros2_ws/software/library_ws/install/setup.bash && source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash && "
        "export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-32} ROS_LOCALHOST_ONLY=0 "
        "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp} "
        "ROBOT_TYPE=${ROBOT_TYPE:-x3} RPLIDAR_TYPE=${RPLIDAR_TYPE:-a1}"
    )
    COMMANDS = {
        "bringup": (
            f"{ROS_ENV} && "
            "ros2 launch yahboomcar_nav laser_bringup_launch.py robot_type:=x3 rplidar_type:=a1 & "
            "launch_pid=$!; sleep 6; "
            "pkill -TERM -f yahboom_joy_X3 2>/dev/null || true; "
            "while kill -0 $launch_pid 2>/dev/null || pgrep -f Mcnamu_driver_X3 >/dev/null; do sleep 5; done"
        ),
        "camera": f"{ROS_ENV} && ros2 launch astra_camera astra.launch.xml",
        "slam": f"{ROS_ENV} && ros2 launch yahboomcar_nav map_gmapping_launch.py",
        "mapping_keyboard": f"{ROS_ENV} && ros2 run yahboomcar_ctrl yahboom_keyboard",
        "save_map": (
            f"{ROS_ENV} && mkdir -p /root/maps && "
            "map_path=/root/maps/yahboomcar_$(date +%Y%m%d_%H%M%S) && "
            "ros2 launch yahboomcar_nav save_map_launch.py map_path:=$map_path"
        ),
        "nav_dwa": f"{ROS_ENV} && ros2 launch yahboomcar_nav navigation_dwa_launch.py",
        "nav_teb": f"{ROS_ENV} && ros2 launch yahboomcar_nav navigation_teb_launch.py",
        "rviz_map": f"{ROS_ENV} && export DISPLAY=:1 QT_X11_NO_MITSHM=1 && ros2 launch yahboomcar_nav display_map_launch.py",
        "rviz_nav": f"{ROS_ENV} && export DISPLAY=:1 QT_X11_NO_MITSHM=1 && ros2 launch yahboomcar_nav display_nav_launch.py",
        "voice_ctrl": f"{ROS_ENV} && ([ -e /dev/myspeech ] || ln -sf /dev/ttyUSB2 /dev/myspeech) && python3 /safe_voice_ctrl.py",
    }

    @classmethod
    def start(cls, name):
        if name not in cls.COMMANDS:
            return {"ok": False, "error": "unknown process"}
        if name in ("nav_dwa", "nav_teb"):
            for other in ("slam", "rviz_map"):
                cls.stop(other, force_external=True)
                cls.processes.pop(other, None)
        elif name == "slam":
            for other in ("nav_dwa", "nav_teb", "rviz_nav"):
                cls.stop(other, force_external=True)
                cls.processes.pop(other, None)
        elif name == "rviz_nav":
            cls.stop("rviz_map", force_external=True)
            cls.processes.pop("rviz_map", None)
        elif name == "rviz_map":
            cls.stop("rviz_nav", force_external=True)
            cls.processes.pop("rviz_nav", None)
        if name in cls.processes and cls.processes[name].poll() is None:
            return {
                "ok": True,
                "running": True,
                "message": "already running",
                "log": cls.logs.get(name),
            }
        if cls.external_running(name):
            return {
                "ok": True,
                "running": True,
                "message": "already running outside web",
                "log": cls.logs.get(name),
            }
        cmd = cls.COMMANDS[name]
        if name in ("nav_dwa", "nav_teb") and cls.selected_map:
            cmd = f"{cmd} map:={shlex.quote(cls.selected_map)}"
        log_path = f"/tmp/smartcar_web_{name}.log"
        log_file = open(log_path, "a", buffering=1)
        proc = subprocess.Popen(
            ["bash", "-lc", cmd],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid,
        )
        cls.processes[name] = proc
        cls.logs[name] = log_path
        if name in ("nav_dwa", "nav_teb") and bridge is not None:
            threading.Timer(5.0, bridge.republish_initial_pose).start()
        return {"ok": True, "pid": proc.pid, "log": log_path}


    @classmethod
    def start_save_map(cls, map_name=None):
        if 'save_map' in cls.processes and cls.processes['save_map'].poll() is None:
            return {
                'ok': True,
                'running': True,
                'message': 'already running',
                'log': cls.logs.get('save_map'),
            }
        map_path = sanitize_map_name(map_name)
        cmd = (
            f"{cls.ROS_ENV} && mkdir -p /root/maps && "
            f"ros2 launch yahboomcar_nav save_map_launch.py map_path:={shlex.quote(str(map_path))}"
        )
        log_path = '/tmp/smartcar_web_save_map.log'
        log_file = open(log_path, 'a', buffering=1)
        proc = subprocess.Popen(
            ['bash', '-lc', cmd],
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid,
        )
        cls.processes['save_map'] = proc
        cls.logs['save_map'] = log_path
        return {
            'ok': True,
            'pid': proc.pid,
            'log': log_path,
            'map_name': map_path.name,
            'map_path': str(map_path),
            'yaml': str(map_path.with_suffix('.yaml')),
        }

    @classmethod
    def stop(cls, name, force_external=False):
        proc = cls.processes.get(name)
        if not proc or proc.poll() is not None:
            if force_external:
                cls.stop_external(name)
            return {"ok": True, "running": False}
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        if force_external:
            cls.stop_external(name)
        return {"ok": True, "stopped": name}

    @classmethod
    def restart(cls, name):
        force_external = name in ("bringup", "camera", "voice_ctrl")
        cls.stop(name, force_external=force_external)
        cls.processes.pop(name, None)
        if name == "bringup":
            subprocess.run(
                ["bash", "-lc", f"{cls.ROS_ENV} && ros2 daemon stop"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        time.sleep(1.0 if force_external else 0.3)
        return cls.start(name)

    @classmethod
    def stop_external(cls, name):
        pattern = cls.external_patterns.get(name)
        if not pattern:
            return False
        probe = subprocess.run(
            ["pgrep", "-f", pattern],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode != 0:
            return False
        for sig in ("-INT", "-TERM"):
            subprocess.run(
                ["pkill", sig, "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            time.sleep(0.2)
        return True

    @classmethod
    def topic_health(cls):
        if bridge is None:
            return {"scan": False, "odom": False, "cmd_vel": False, "map": False}
        return {
            "scan": bridge.count_publishers("/scan") > 0,
            "odom": bridge.count_publishers("/odom") > 0,
            "cmd_vel": bridge.count_subscribers("/cmd_vel") > 0,
            "map": bridge.count_publishers("/map") > 0,
        }

    @classmethod
    def start_mapping(cls):
        if bridge is not None:
            bridge.stop()
        for name in ("nav_dwa", "nav_teb", "slam", "bringup"):
            cls.stop(name, force_external=True)
        if bridge is not None:
            bridge.clear_mapping_state()
        result = cls.start("slam")
        return {
            "ok": True,
            "message": "建图启动中，请观察地图和状态刷新",
            "health": cls.topic_health(),
            "log": result.get("log"),
        }

    @classmethod
    def status(cls):
        names = set(cls.COMMANDS) | set(cls.processes)
        data = {}
        for name in sorted(names):
            proc_running = name in cls.processes and cls.processes[name].poll() is None
            pattern = cls.external_patterns.get(name)
            external = False
            if pattern:
                probe = subprocess.run(
                    ["pgrep", "-f", pattern],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                external = probe.returncode == 0
            data[name] = {
                "running": bool(proc_running or external),
                "log": cls.logs.get(name),
            }
        return data

    @classmethod
    def external_running(cls, name):
        if bridge is not None:
            if name == "bringup":
                with bridge.lock:
                    scan_fresh = bool(
                        bridge.last_scan_time
                        and time.time() - bridge.last_scan_time < 3.0
                    )
                return (
                    bridge.count_subscribers("/cmd_vel") > 0
                    and bridge.count_publishers("/scan") > 0
                    and scan_fresh
                )
            if name == "slam":
                return bridge.count_publishers("/map") > 0
            if name in ("nav_dwa", "nav_teb"):
                return bridge.count_publishers("/plan") > 0
        pattern = cls.external_patterns.get(name)
        if not pattern:
            return False
        try:
            res = subprocess.run(
                ["pgrep", "-f", pattern],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            return res.returncode == 0
        except Exception:
            return False


bridge = None
camera = CameraStream()


def resolve_bjtu_root():
    candidates = []
    env_root = os.environ.get("BJTU_ROS2_FOXY_ROOT") or os.environ.get("BJTU_REPO_PATH")
    if env_root:
        root = Path(env_root)
        candidates.extend([root, root / "ROS2-Foxy"])
    if len(BASE_DIR.parents) > 2:
        candidates.append(BASE_DIR.parents[2] / "ROS2-Foxy")
    candidates.extend([
        BASE_DIR.parent / "ROS2-Foxy",
        BASE_DIR / "ROS2-Foxy",
        Path("/root/ROS2-Foxy"),
        Path("/root/yahboomcar_ros2_ws/ROS2-Foxy"),
        Path("/root/yahboomcar_ros2_ws/yahboomcar_ws/ROS2-Foxy"),
        Path("/home/jetson/code/ROS2-Foxy"),
    ])
    for candidate in candidates:
        if candidate and (candidate / "scripts").exists():
            return candidate.resolve()
    return None


class BjtuFeatureManager:
    jobs = {}
    logs = {}
    active_mode = None
    root = None
    features = {
        "start_all": {
            "title": "基础跨机驾驶桥",
            "start": "scripts/start_all.sh",
            "stop": "scripts/stop_all.sh",
            "mode": "bridge",
            "description": "启动 Jetson 底盘驱动与 Zenoh 驾驶桥。",
        },
        "d1_slam_nav": {
            "title": "D1 在线 SLAM + Nav2",
            "start": "deploy/d1_nav/scripts/start_d1_slam_nav.sh",
            "stop": "deploy/d1_nav/scripts/stop_d1_slam_nav.sh",
            "mode": "d1",
            "description": "启动 slam_toolbox 在线建图与 Nav2 实时地图导航。",
        },
        "d2_stop_fusion": {
            "title": "D2 STOP 标志静态融合",
            "start": "deploy/d2_fusion/scripts/run_d2_static_fusion.sh",
            "stop": "deploy/d2_fusion/scripts/stop_d2_static_fusion.sh",
            "mode": "d2",
            "description": "启动 YOLO、深度相机、SLAM 和 STOP 位姿融合；不驱动车辆。",
        },
        "sign_control": {
            "title": "交通标志闭环控制",
            "start": "scripts/start_sign_control.sh",
            "stop": "scripts/stop_sign_control.sh",
            "mode": "sign",
            "description": "识别 ahead/turn/stop/no_entry 并按标志发布低速 /cmd_vel。",
        },
    }
    planned = {
        "person_follow": {
            "title": "行人跟随",
            "description": "fusion_node.py --follow 需要检测服务 5001、/scan 和 /cmd_vel 单发布者环境。",
            "state": "需要按车端部署再启用",
        },
        "rl_search": {
            "title": "前沿/RL 自走搜索",
            "description": "文档明确车端 FSM、策略导出、Nav2 action 适配仍未完成。",
            "state": "规划中，未开放启动",
        },
    }

    @classmethod
    def ensure_root(cls):
        cls.root = resolve_bjtu_root()
        return cls.root

    @classmethod
    def script_path(cls, feature, kind):
        root = cls.ensure_root()
        rel = cls.features[feature].get(kind)
        if not root or not rel:
            return None
        return root / rel

    @classmethod
    def status(cls):
        root = cls.ensure_root()
        data = {
            "root": str(root) if root else None,
            "active_mode": cls.active_mode,
            "features": {},
            "planned": cls.planned,
        }
        for name, cfg in cls.features.items():
            proc = cls.jobs.get(name)
            start_path = cls.script_path(name, "start")
            stop_path = cls.script_path(name, "stop")
            data["features"][name] = {
                "title": cfg["title"],
                "description": cfg["description"],
                "mode": cfg["mode"],
                "running": bool(proc and proc.poll() is None),
                "start_script": str(start_path) if start_path else None,
                "stop_script": str(stop_path) if stop_path else None,
                "available": bool(start_path and start_path.exists()),
                "stop_available": bool(stop_path and stop_path.exists()),
                "log": cls.logs.get(name),
            }
        return data

    @classmethod
    def start(cls, name):
        if name not in cls.features:
            return {"ok": False, "error": "unknown bjtu feature"}
        proc = cls.jobs.get(name)
        if proc and proc.poll() is None:
            return {"ok": True, "running": True, "message": "already running", "log": cls.logs.get(name)}
        script = cls.script_path(name, "start")
        if not script or not script.exists():
            return {"ok": False, "error": "script not available in web runtime", "status": cls.status()}
        mode = cls.features[name]["mode"]
        if cls.active_mode and cls.active_mode != mode:
            return {"ok": False, "error": f"mode {cls.active_mode} is active; stop it before starting {mode}"}
        log_path = str(BJTU_FEATURE_LOG_DIR / f"smartcar_web_bjtu_{name}.log")
        log_file = open(log_path, "a", buffering=1)
        env = os.environ.copy()
        env.setdefault("JETSON_PASSWORD", "yahboom")
        env.setdefault("JETSON_HOST", "jetson-desktop.local")
        proc = subprocess.Popen(
            ["bash", str(script)],
            cwd=str(script.parent),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid,
            env=env,
        )
        cls.jobs[name] = proc
        cls.logs[name] = log_path
        cls.active_mode = mode
        return {"ok": True, "pid": proc.pid, "log": log_path, "mode": mode}

    @classmethod
    def stop(cls, name):
        if name not in cls.features:
            return {"ok": False, "error": "unknown bjtu feature"}
        script = cls.script_path(name, "stop")
        stop_result = None
        if script and script.exists():
            log_path = str(BJTU_FEATURE_LOG_DIR / f"smartcar_web_bjtu_{name}_stop.log")
            with open(log_path, "a", buffering=1) as log_file:
                stop_result = subprocess.run(
                    ["bash", str(script)],
                    cwd=str(script.parent),
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=45,
                    check=False,
                    env={**os.environ, "JETSON_PASSWORD": os.environ.get("JETSON_PASSWORD", "yahboom")},
                )
        proc = cls.jobs.get(name)
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                proc.wait(timeout=6)
            except Exception:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:
                    pass
        if cls.features[name]["mode"] == cls.active_mode:
            cls.active_mode = None
        return {
            "ok": True,
            "stopped": name,
            "stop_returncode": None if stop_result is None else stop_result.returncode,
            "status": cls.status(),
        }

    @classmethod
    def log(cls, name, lines=160):
        path = cls.logs.get(name)
        if not path:
            path = str(BJTU_FEATURE_LOG_DIR / f"smartcar_web_bjtu_{name}.log")
        log_path = Path(path)
        if not log_path.exists():
            return {"ok": False, "error": "log not found", "log": path, "text": ""}
        try:
            count = max(20, min(int(lines), 400))
        except Exception:
            count = 160
        text = subprocess.run(
            ["tail", "-n", str(count), str(log_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        ).stdout
        return {"ok": True, "log": str(log_path), "text": text}


def read_detection_line(host="127.0.0.1", port=5002):
    started = time.time()
    buffer = b""
    with socket.create_connection((host, int(port)), timeout=BJTU_DETECT_TIMEOUT_S) as sock:
        sock.settimeout(BJTU_DETECT_TIMEOUT_S)
        while b"\n" not in buffer and len(buffer) < 262144:
            chunk = sock.recv(8192)
            if not chunk:
                break
            buffer += chunk
    line = buffer.split(b"\n", 1)[0].strip()
    if not line:
        return {"ok": False, "host": host, "port": int(port), "error": "detector returned no JSON line"}
    payload = json.loads(line.decode("utf-8", errors="strict"))
    return {
        "ok": True,
        "host": host,
        "port": int(port),
        "latency_ms": round((time.time() - started) * 1000, 1),
        "raw": payload,
        "detections": payload.get("dets", []),
    }


def sanitize_map_name(raw):
    name = str(raw or '').strip()
    if not name:
        name = 'map_' + time.strftime('%Y%m%d_%H%M%S')
    name = re.sub(r'[^\w.-]+', '_', name, flags=re.UNICODE).strip('._-')
    if not name:
        name = 'map_' + time.strftime('%Y%m%d_%H%M%S')
    if len(name) > 80:
        name = name[:80]
    base = Path('/root/maps') / name
    if base.with_suffix('.yaml').exists() or base.with_suffix('.pgm').exists() or base.with_suffix('.png').exists():
        base = Path('/root/maps') / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}"
    return base

def list_maps():
    maps = []
    seen = set()
    for directory in MAP_DIRS:
        if not directory.exists():
            continue
        for yaml_path in sorted(directory.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True):
            stem = yaml_path.with_suffix("")
            if str(stem) in seen:
                continue
            seen.add(str(stem))
            image = None
            for suffix in (".pgm", ".png"):
                candidate = stem.with_suffix(suffix)
                if candidate.exists():
                    image = str(candidate)
                    break
            stat = yaml_path.stat()
            maps.append({
                "name": stem.name,
                "yaml": str(yaml_path),
                "image": image,
                "directory": str(directory),
                "updated_at": stat.st_mtime,
                "updated_text": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
            })
    return {"maps": maps, "selected": ProcessManager.selected_map}


def select_map(yaml_path):
    path = Path(yaml_path).resolve()
    valid_paths = {Path(item["yaml"]).resolve() for item in list_maps()["maps"]}
    if path not in valid_paths:
        return {"ok": False, "error": "unknown map"}
    ProcessManager.selected_map = str(path)
    return {"ok": True, "selected": ProcessManager.selected_map}


def read_sensor_data(host=SENSOR_DEFAULT_HOST, port=SENSOR_DEFAULT_PORT):
    started = time.time()
    buffer = b""
    with socket.create_connection((host, int(port)), timeout=SENSOR_TIMEOUT_S) as sock:
        sock.settimeout(SENSOR_TIMEOUT_S)
        while b"\n" not in buffer and len(buffer) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
    line = buffer.split(b"\n", 1)[0].strip()
    if not line:
        return {
            "ok": False,
            "host": host,
            "port": int(port),
            "error": "sensor returned no JSON line",
        }
    payload = json.loads(line.decode("utf-8", errors="strict"))
    props = {}
    for service in payload.get("services", []):
        if service.get("service_id") == "sensorData":
            props = service.get("properties", {})
            break
    if not props and payload.get("services"):
        props = payload["services"][0].get("properties", {})
    return {
        "ok": True,
        "host": host,
        "port": int(port),
        "latency_ms": round((time.time() - started) * 1000, 1),
        "raw": payload,
        "properties": props,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.json_response(bridge.status())
            return
        if parsed.path == "/api/viz":
            self.json_response(bridge.visualization())
            return
        if parsed.path == "/api/maps":
            self.json_response(list_maps())
            return
        if parsed.path == "/api/process/status":
            self.json_response(ProcessManager.status())
            return
        if parsed.path == "/api/sensors":
            query = parse_qs(parsed.query)
            host = query.get("host", [SENSOR_DEFAULT_HOST])[0] or SENSOR_DEFAULT_HOST
            try:
                port = int(query.get("port", [str(SENSOR_DEFAULT_PORT)])[0] or SENSOR_DEFAULT_PORT)
                if port <= 0 or port > 65535:
                    raise ValueError("port out of range")
                self.json_response(read_sensor_data(host, port))
            except Exception as exc:
                self.json_response({
                    "ok": False,
                    "host": host,
                    "port": query.get("port", [str(SENSOR_DEFAULT_PORT)])[0],
                    "error": str(exc),
                })
            return
        if parsed.path == "/api/bjtu/status":
            self.json_response(BjtuFeatureManager.status())
            return
        if parsed.path == "/api/bjtu/log":
            query = parse_qs(parsed.query)
            self.json_response(BjtuFeatureManager.log(query.get("name", [""])[0], query.get("lines", ["160"])[0]))
            return
        if parsed.path == "/api/bjtu/detections":
            query = parse_qs(parsed.query)
            host = query.get("host", ["127.0.0.1"])[0] or "127.0.0.1"
            try:
                port = int(query.get("port", ["5002"])[0] or 5002)
                if port <= 0 or port > 65535:
                    raise ValueError("port out of range")
                self.json_response(read_detection_line(host, port))
            except Exception as exc:
                self.json_response({"ok": False, "host": host, "port": query.get("port", ["5002"])[0], "error": str(exc)})
            return
        if parsed.path == "/api/camera/stream":
            self.camera_stream()
            return
        return super().do_GET()

    def camera_stream(self):
        self.send_response(200)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        while True:
            frame = camera.frame()
            if frame is None:
                time.sleep(0.3)
                continue
            try:
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                time.sleep(0.05)
            except (BrokenPipeError, ConnectionResetError):
                break

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self.read_json()
        try:
            if parsed.path == "/api/move":
                res = bridge.publish_cmd(
                    data.get("linear_x", 0.0),
                    data.get("linear_y", 0.0),
                    data.get("angular_z", 0.0),
                )
            elif parsed.path == "/api/stop":
                res = bridge.stop()
            elif parsed.path == "/api/guard":
                res = bridge.set_guard(data.get("enabled"), data.get("stop_distance"))
            elif parsed.path == "/api/process/start":
                name = data["name"]
                if name == "bringup":
                    ProcessManager.stop("voice_ctrl")
                    res = ProcessManager.restart(name)
                elif name == "voice_ctrl":
                    ProcessManager.stop("bringup")
                    res = ProcessManager.restart(name)
                elif name == "camera":
                    camera.reset()
                    res = ProcessManager.restart(name)
                    res["camera"] = camera.status()
                else:
                    res = ProcessManager.start(name)
            elif parsed.path == "/api/process/stop":
                res = ProcessManager.stop(data["name"], bool(data.get("force_external")))
            elif parsed.path == "/api/mapping/start":
                res = ProcessManager.start_mapping()
            elif parsed.path == "/api/mapping/clear":
                res = bridge.clear_mapping_state()
            elif parsed.path == "/api/maps/save":
                res = ProcessManager.start_save_map(data.get("name") or data.get("map_name"))
            elif parsed.path == "/api/maps/select":
                res = select_map(data["yaml"])
            elif parsed.path == "/api/nav/initial_pose":
                res = bridge.publish_initial_pose(data["x"], data["y"], data.get("theta", 0.0))
            elif parsed.path == "/api/nav/clear":
                res = bridge.clear_navigation_overlay()
            elif parsed.path == "/api/nav/goal":
                res = bridge.publish_goal(data["x"], data["y"], data.get("theta", 0.0))
            elif parsed.path == "/api/bjtu/start":
                res = BjtuFeatureManager.start(data["name"])
            elif parsed.path == "/api/bjtu/stop":
                res = BjtuFeatureManager.stop(data["name"])
            else:
                self.json_response({"ok": False, "error": "not found"}, 404)
                return
            self.json_response(res)
        except Exception as exc:
            self.json_response({"ok": False, "error": repr(exc)}, 500)


class SmartCarHTTPServer(ThreadingHTTPServer):
    request_queue_size = 64
    daemon_threads = True


def main():
    global bridge
    os.environ.setdefault("ROS_DOMAIN_ID", "32")
    os.environ.setdefault("ROS_LOCALHOST_ONLY", "0")
    os.environ.setdefault("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")
    rclpy.init()
    bridge = RosBridge()
    spin_thread = threading.Thread(target=rclpy.spin, args=(bridge,), daemon=True)
    spin_thread.start()
    server = SmartCarHTTPServer(("0.0.0.0", 8000), Handler)
    print("Smart car web console: http://0.0.0.0:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        bridge.stop()
        bridge.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import json
import math
import os
import signal
import shlex
import subprocess
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import rclpy
from geometry_msgs.msg import PoseStamped, Quaternion, Twist
from nav_msgs.msg import OccupancyGrid, Odometry, Path as NavPath
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Float32
from geometry_msgs.msg import PoseWithCovarianceStamped

try:
    import cv2
except Exception:
    cv2 = None


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAP_DIRS = [
    Path("/root/maps"),
    Path("/root/yahboomcar_ros2_ws/yahboomcar_ws/src/yahboomcar_nav/maps"),
]
SELECTED_MAP = None

FRONT_CENTER_DEG = 0.0
FRONT_HALF_WIDTH_DEG = 35.0
DEFAULT_STOP_DISTANCE_M = 0.35
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

    def frame(self):
        if not self.open():
            return None
        with self.lock:
            ok, frame = self.cap.read()
        if not ok:
            self.last_error = "Camera frame read failed"
            with self.lock:
                if self.cap:
                    self.cap.release()
                self.cap = None
            return None
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ok:
            self.last_error = "JPEG encode failed"
            return None
        return encoded.tobytes()

    def status(self):
        return {
            "available": cv2 is not None,
            "device": self.device,
            "open": bool(self.cap and self.cap.isOpened()),
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
        self.initial_pose_pub.publish(msg)
        with self.lock:
            self.initial_pose = pose_data
        return {"ok": True, "initial_pose": pose_data}

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
            f"{ROS_ENV} && ros2 launch yahboomcar_nav laser_bringup_launch.py "
            "robot_type:=x3 rplidar_type:=a1"
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
        "voice_ctrl": f"{ROS_ENV} && ([ -e /dev/myspeech ] || ln -sf /dev/ttyUSB2 /dev/myspeech) && python3 /safe_voice_ctrl.py",
    }

    @classmethod
    def start(cls, name):
        if name not in cls.COMMANDS:
            return {"ok": False, "error": "unknown process"}
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
        cls.stop(name)
        cls.processes.pop(name, None)
        time.sleep(0.3)
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
        return {
            name: {
                "running": (name in cls.processes and cls.processes[name].poll() is None),
                "log": cls.logs.get(name),
            }
            for name in sorted(names)
        }

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
                    res = {
                        "ok": True,
                        "message": "camera stream reset",
                        "camera": camera.status(),
                    }
                else:
                    res = ProcessManager.start(name)
            elif parsed.path == "/api/process/stop":
                res = ProcessManager.stop(data["name"], bool(data.get("force_external")))
            elif parsed.path == "/api/mapping/start":
                res = ProcessManager.start_mapping()
            elif parsed.path == "/api/mapping/clear":
                res = bridge.clear_mapping_state()
            elif parsed.path == "/api/maps/select":
                res = select_map(data["yaml"])
            elif parsed.path == "/api/nav/initial_pose":
                res = bridge.publish_initial_pose(data["x"], data["y"], data.get("theta", 0.0))
            elif parsed.path == "/api/nav/goal":
                res = bridge.publish_goal(data["x"], data["y"], data.get("theta", 0.0))
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

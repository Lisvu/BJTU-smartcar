#!/usr/bin/env python3
import json
import math
import os
import signal
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

try:
    import cv2
except Exception:
    cv2 = None


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

FRONT_CENTER_DEG = 0.0
FRONT_HALF_WIDTH_DEG = 35.0
DEFAULT_STOP_DISTANCE_M = 0.35


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
    def __init__(self, device="/dev/video0"):
        self.device = device
        self.cap = None
        self.lock = threading.Lock()
        self.last_error = None

    def open(self):
        if cv2 is None:
            self.last_error = "OpenCV is not available"
            return False
        with self.lock:
            if self.cap and self.cap.isOpened():
                return True
            self.cap = cv2.VideoCapture(self.device)
            if not self.cap.isOpened():
                self.last_error = f"Cannot open {self.device}"
                self.cap.release()
                self.cap = None
                return False
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.cap.set(cv2.CAP_PROP_FPS, 20)
            self.last_error = None
            return True

    def frame(self):
        if not self.open():
            return None
        with self.lock:
            ok, frame = self.cap.read()
        if not ok:
            self.last_error = "Camera frame read failed"
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
        self.subscriptions = [
            self.create_subscription(Float32, "/voltage", self.on_voltage, 10),
            self.create_subscription(LaserScan, "/scan", self.on_scan, 10),
            self.create_subscription(OccupancyGrid, "/map", self.on_map, 1),
            self.create_subscription(NavPath, "/plan", self.on_plan, 1),
            self.create_subscription(Odometry, "/odom", self.on_odom, 20),
        ]

        self.voltage = None
        self.front_distance = None
        self.map = None
        self.plan = []
        self.scan_points = []
        self.pose = None
        self.obstacle_guard = True
        self.stop_distance = DEFAULT_STOP_DISTANCE_M
        self.last_scan_time = 0.0
        self.last_map_time = 0.0
        self.last_odom_time = 0.0
        self.last_cmd = {"linear_x": 0.0, "linear_y": 0.0, "angular_z": 0.0}
        self.lock = threading.Lock()

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
                "map": None if not self.map else {
                    "width": self.map["width"],
                    "height": self.map["height"],
                    "resolution": self.map["resolution"],
                },
                "plan_len": len(self.plan),
                "pose": self.pose,
                "camera": camera.status(),
                "processes": ProcessManager.status(),
            }

    def visualization(self):
        with self.lock:
            return {
                "map": self.map,
                "scan": self.scan_points,
                "pose": self.pose,
                "plan": self.plan,
                "ages": {
                    "scan": time.time() - self.last_scan_time if self.last_scan_time else None,
                    "map": time.time() - self.last_map_time if self.last_map_time else None,
                    "odom": time.time() - self.last_odom_time if self.last_odom_time else None,
                },
            }

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
        return {"ok": True, "blocked": blocked, "cmd": self.last_cmd}

    def stop(self):
        return self.publish_cmd(0.0, 0.0, 0.0)

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


class ProcessManager:
    processes = {}
    logs = {}
    external_patterns = {
        "bringup": "Mcnamu_driver_X3|base_node_X3|sllidar_node",
        "camera": "astra_camera_node|astra.launch.xml",
        "slam": "slam_gmapping|map_gmapping_launch.py",
        "nav_dwa": "navigation_dwa_launch.py",
        "nav_teb": "navigation_teb_launch.py",
    }

    ROS_ENV = (
        "source /opt/ros/foxy/setup.bash && "
        "source /root/icar_ros2_ws/icar_ws/install/setup.bash && "
        "export ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-32} ROS_LOCALHOST_ONLY=0 "
        "RMW_IMPLEMENTATION=${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp} "
        "ROBOT_TYPE=${ROBOT_TYPE:-x3} RPLIDAR_TYPE=${RPLIDAR_TYPE:-a1}"
    )
    COMMANDS = {
        "bringup": (
            f"{ROS_ENV} && ros2 launch icar_nav laser_bringup_launch.py "
            "robot_type:=x3 rplidar_type:=a1"
        ),
        "camera": f"{ROS_ENV} && ros2 launch astra_camera astra.launch.xml",
        "slam": f"{ROS_ENV} && ros2 launch icar_nav map_gmapping_launch.py",
        "mapping_keyboard": f"{ROS_ENV} && ros2 run icar_ctrl yahboom_keyboard",
        "save_map": f"{ROS_ENV} && ros2 launch icar_nav save_map_launch.py",
        "nav_dwa": f"{ROS_ENV} && ros2 launch icar_nav navigation_dwa_launch.py",
        "nav_teb": f"{ROS_ENV} && ros2 launch icar_nav navigation_teb_launch.py",
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
        return {"ok": True, "pid": proc.pid, "log": log_path}

    @classmethod
    def stop(cls, name):
        proc = cls.processes.get(name)
        if not proc or proc.poll() is not None:
            return {"ok": True, "running": False}
        os.killpg(os.getpgid(proc.pid), signal.SIGINT)
        return {"ok": True, "stopped": name}

    @classmethod
    def status(cls):
        names = set(cls.COMMANDS) | set(cls.processes)
        return {
            name: {
                "running": (
                    (name in cls.processes and cls.processes[name].poll() is None)
                    or cls.external_running(name)
                ),
                "log": cls.logs.get(name),
            }
            for name in sorted(names)
        }

    @classmethod
    def external_running(cls, name):
        if bridge is not None:
            if name == "bringup":
                return (
                    bridge.count_subscribers("/cmd_vel") > 0
                    and bridge.count_publishers("/scan") > 0
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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def json_response(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
                res = ProcessManager.start(data["name"])
            elif parsed.path == "/api/process/stop":
                res = ProcessManager.stop(data["name"])
            elif parsed.path == "/api/nav/goal":
                res = bridge.publish_goal(data["x"], data["y"], data.get("theta", 0.0))
            else:
                self.json_response({"ok": False, "error": "not found"}, 404)
                return
            self.json_response(res)
        except Exception as exc:
            self.json_response({"ok": False, "error": repr(exc)}, 500)


def main():
    global bridge
    rclpy.init()
    bridge = RosBridge()
    spin_thread = threading.Thread(target=rclpy.spin, args=(bridge,), daemon=True)
    spin_thread.start()

    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
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

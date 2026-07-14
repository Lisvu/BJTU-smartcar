"""Tests for project business logic that does not require robot hardware."""

import importlib.util
import math
import sys
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _install_ros_import_stubs():
    """Allow importing pure functions without installing or starting ROS2."""
    rclpy = types.ModuleType("rclpy")
    rclpy_node = types.ModuleType("rclpy.node")
    rclpy_qos = types.ModuleType("rclpy.qos")
    geometry_msgs = types.ModuleType("geometry_msgs.msg")
    sensor_msgs = types.ModuleType("sensor_msgs.msg")
    std_msgs = types.ModuleType("std_msgs.msg")

    class Node:
        pass

    class Twist:
        pass

    class LaserScan:
        pass

    class Bool:
        pass

    rclpy_node.Node = Node
    rclpy_qos.qos_profile_sensor_data = object()
    geometry_msgs.Twist = Twist
    sensor_msgs.LaserScan = LaserScan
    std_msgs.Bool = Bool

    modules = {
        "rclpy": rclpy,
        "rclpy.node": rclpy_node,
        "rclpy.qos": rclpy_qos,
        "geometry_msgs": types.ModuleType("geometry_msgs"),
        "geometry_msgs.msg": geometry_msgs,
        "sensor_msgs": types.ModuleType("sensor_msgs"),
        "sensor_msgs.msg": sensor_msgs,
        "std_msgs": types.ModuleType("std_msgs"),
        "std_msgs.msg": std_msgs,
    }
    for name, module in modules.items():
        sys.modules.setdefault(name, module)


def _load_module(name, relative_path):
    _install_ros_import_stubs()
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sign_logic():
    return _load_module("bjtu_sign_command_node_for_tests", "ROS2-Foxy/scripts/sign_command_node.py")


@pytest.fixture(scope="module")
def fusion_logic():
    return _load_module("bjtu_fusion_node_for_tests", "ROS2-Foxy/scripts/fusion_node.py")


@pytest.fixture(scope="module")
def pid_logic():
    return _load_module(
        "bjtu_laser_common_for_tests",
        "yahboomcar_ws/src/yahboomcar_laser/yahboomcar_laser/common.py",
    )


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [(5.0, 0.0, 10.0, 5.0), (-2.0, 0.0, 10.0, 0.0), (12.0, 0.0, 10.0, 10.0)],
)
def test_sign_clamp(sign_logic, value, low, high, expected):
    assert sign_logic.clamp(value, low, high) == expected


def test_sign_angle_and_float_normalization(sign_logic):
    assert sign_logic.normalize_angle(3.0 * math.pi) == pytest.approx(math.pi)
    assert sign_logic.normalize_angle(-3.0 * math.pi) == pytest.approx(math.pi)
    assert sign_logic.safe_float("0.25") == pytest.approx(0.25)
    assert sign_logic.safe_float("invalid", default=2.0) == 2.0


def test_select_main_sign_filters_invalid_candidates_and_picks_best(sign_logic):
    payload = {
        "h": 1000,
        "dets": [
            {"cls": "unknown", "conf": 0.99, "bw": 500, "bh": 500},
            {"cls": "ahead", "conf": 0.80, "bw": 100, "bh": 100},
            {"cls": "stop", "conf": 0.90, "bw": 50, "bh": 100},
            {"cls": "turn_left", "conf": 0.40, "bw": 500, "bh": 500},
        ],
    }
    result = sign_logic.select_main_sign(payload, conf_thres=0.5, act_ratio=0.06)
    assert result["cls"] == "stop"
    assert result["ratio"] == pytest.approx(0.1)


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        ("ahead", ("AHEAD", 0.15, 0.0)),
        ("turn_left", ("LEFT", 0.0, 0.8)),
        ("turn_right", ("RIGHT", 0.0, -0.8)),
        ("stop", ("STOP", 0.0, 0.0)),
        (None, ("STOP", 0.0, 0.0)),
    ],
)
def test_command_for_sign_is_safe_and_directional(sign_logic, cls, expected):
    assert sign_logic.command_for_sign(cls, 0.3, 1.2, 0.15, 0.8) == expected


def test_fusion_distance_validation_and_estimation(fusion_logic):
    assert fusion_logic.is_valid_distance(1.0)
    assert not fusion_logic.is_valid_distance(0.0)
    assert not fusion_logic.is_valid_distance(float("nan"))
    assert fusion_logic.estimate_distance_from_bbox_height(100, 1000) == pytest.approx(3.0)
    assert fusion_logic.estimate_distance_from_bbox_height(1000, 1000) == pytest.approx(0.45)
    assert fusion_logic.estimate_distance_from_bbox_height(None, 1000) is None


def test_compute_follow_command_applies_deadband_and_limits(fusion_logic):
    assert fusion_logic.compute_follow_command(0.55, 0.1) == pytest.approx((0.0, -0.12))
    assert fusion_logic.compute_follow_command(2.0, 2.0) == pytest.approx((0.15, -0.8))
    assert fusion_logic.compute_follow_command(None, 0.2) == (0.0, 0.0)


def test_compute_recapture_command_turns_toward_last_bearing(fusion_logic):
    assert fusion_logic.compute_recapture_command(0.4) == pytest.approx((0.0, -0.35))
    assert fusion_logic.compute_recapture_command(-2.0) == pytest.approx((0.0, 0.35))
    assert fusion_logic.compute_recapture_command(0.0) == (0.0, 0.0)


def test_follow_target_state_prefers_lidar_then_holds_then_uses_bbox(fusion_logic):
    state = fusion_logic.FollowTargetState(dist_hold_s=0.5)
    assert state.resolve_distance(1.2, 100, 1000, now_s=1.0) == (1.2, "lidar")
    assert state.resolve_distance(None, 100, 1000, now_s=1.4) == (1.2, "hold")
    distance, source = state.resolve_distance(None, 100, 1000, now_s=2.0)
    assert distance == pytest.approx(3.0)
    assert source == "bbox"


def test_single_pid_integrates_error_and_resets(pid_logic):
    pid = pid_logic.SinglePID(P=1.0, I=0.5, D=0.25)
    assert pid.pid_compute(10.0, 8.0) == pytest.approx(3.5)
    assert pid.pid_compute(10.0, 9.0) == pytest.approx(2.25)
    pid.pid_reset()
    assert pid.error == 0
    assert pid.intergral == 0
    assert pid.prevError == 0

# BJTU Smart Car

[![CI](https://github.com/Lisvu/BJTU-smartcar/actions/workflows/ci.yml/badge.svg)](https://github.com/Lisvu/BJTU-smartcar/actions/workflows/ci.yml)

This repository contains the source backup for the BJTU Yahboom/Jetson smart car project.

## Source Layout

- `Rosmaster-App/` - Rosmaster web/control app and direct hardware control scripts.
- `yahboomcar_ws/` - ROS2 smart car workspace source and custom scripts.
- `software/library_ws/` - supporting ROS2 library workspace source.
- `bjtu_ros2_ws/` - BJTU ROS2 workspace files.
- `maps/` - map backup directory from the car.

## Custom Scripts

- `Rosmaster-App/rosmaster/obstacle_drive.py` - manual driving with RPLidar-based obstacle stop.
- `yahboomcar_ws/voice_cmd_vel.py` - speech-module command control via `/cmd_vel`.
- `yahboomcar_ws/battery_monitor.py` - battery voltage monitor with warning output.

## Environment Notes

The original car runs Ubuntu 20.04 on Jetson ARM64/aarch64 with NVIDIA L4T. This code is intended for backup, review, and development. Hardware-dependent ROS nodes require the real car hardware or an equivalent Jetson environment.

## Excluded Large/Generated Files

Generated workspaces and large files are not committed:

- ROS `build/`, `install/`, and `log/` directories.
- Python caches and native binaries such as `*.pyc`, `*.so`, and eggs.
- Large runtime data files such as `yahboomcar_ws/imu.txt`, `yahboomcar_ws/imu_raw.txt`, and `yahboomcar_ws/src/yahboomcar_slam/params/ORBvoc.txt`.

The full raw backup archive is stored on the server at `/root/smartcar_backup/archives/`.

## Continuous Integration

GitHub Actions runs two checks for pushes and pull requests targeting `main`:

- ROS-independent Python syntax checks and unit tests on Python 3.8.
- A `bjtu_comm` build and test in a ROS2 Foxy container.

The CI scope intentionally excludes hardware-dependent packages that require the
Jetson, camera, lidar, serial devices, or NVIDIA L4T libraries.

Run the ROS2 checks locally on Ubuntu 20.04 with ROS2 Foxy installed:

```bash
source /opt/ros/foxy/setup.bash
cd bjtu_ros2_ws
colcon build --packages-select bjtu_comm
colcon test --packages-select bjtu_comm
colcon test-result --verbose
```

Run only the hardware-independent unit tests from the package directory:

```bash
cd bjtu_ros2_ws/src/bjtu_comm
python3 -m pytest test/test_messages.py -v
```

See `docs/CLOUD_PLATFORM_AND_CICD_PLAN.md` for the GitHub workflow, branch
protection, release, and acceptance plan.

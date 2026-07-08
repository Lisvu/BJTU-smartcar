# BJTU Smart Car

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

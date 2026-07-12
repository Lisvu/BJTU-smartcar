#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
SSH=(sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON_USER@$JETSON_HOST")
SCP=(sshpass -p "$JETSON_PASSWORD" scp -o StrictHostKeyChecking=no)

"$ROOT_DIR/scripts/free_devices.sh"
"${SSH[@]}" 'docker stop smartcar_web >/dev/null 2>&1 || true; mkdir -p /home/jetson/bjtu_ros2_ws/d2 /home/jetson/bjtu_ai'
"${SCP[@]}" "$ROOT_DIR/config/d1/slam_toolbox_online_async.yaml" \
  "$JETSON_USER@$JETSON_HOST:/home/jetson/bjtu_ros2_ws/d2/"
"${SCP[@]}" "$ROOT_DIR/scripts/stop_sign_pose_node.py" \
  "$ROOT_DIR/scripts/detect_headless.py" \
  "$JETSON_USER@$JETSON_HOST:/home/jetson/bjtu_ai/"

"${SSH[@]}" 'docker restart bjtu_car >/dev/null'
sleep 2
"${SSH[@]}" 'docker exec bjtu_car mkdir -p /root/bjtu_ai; docker cp /home/jetson/bjtu_ai/stop_sign_pose_node.py bjtu_car:/root/bjtu_ai/'
"${SSH[@]}" 'docker exec bjtu_car bash -lc '\''
set -eo pipefail
ln -sf /dev/ttyUSB0 /dev/rplidar
ln -sf /dev/ttyUSB1 /dev/myserial
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/software/library_ws/install/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy
export ROBOT_TYPE=x3 RPLIDAR_TYPE=a1
nohup ros2 launch yahboomcar_nav laser_bringup_launch.py robot_type:=x3 rplidar_type:=a1 > /tmp/d2_bringup.log 2>&1 &
sleep 8
joy_pids="$(pgrep -f "^/usr/bin/python3 .*/yahboomcar_ctrl/.*/yahboom_joy_X3" || true)"
[ -z "$joy_pids" ] || kill -TERM $joy_pids
sleep 1
if grep -qE "SerialException|Serial Open Failed|device disconnected|multiple access" /tmp/d2_bringup.log; then
  echo "ERROR: chassis serial communication failed" >&2
  tail -40 /tmp/d2_bringup.log >&2
  exit 1
fi
voltage="$(timeout -s INT 5 ros2 topic echo /voltage std_msgs/msg/Float32 2>/dev/null | sed -n "s/^data: //p" | head -1 || true)"
voltage_whole="${voltage%%.*}"
if [ -z "$voltage_whole" ] || [ "$voltage_whole" -le 1 ]; then
  echo "ERROR: invalid chassis voltage ${voltage:-missing}" >&2
  exit 1
fi
nohup ros2 launch slam_toolbox online_async_launch.py params_file:=/root/bjtu_ros2_ws/d2/slam_toolbox_online_async.yaml use_sim_time:=false > /tmp/d2_slam.log 2>&1 &
sleep 6
nohup ros2 launch astra_camera astra.launch.xml depth_registration:=true \
  enable_point_cloud:=false enable_colored_point_cloud:=false enable_ir:=false \
  color_depth_synchronization:=true > /tmp/d2_astra.log 2>&1 &
sleep 8
depth_publishers="$(ros2 topic info /camera/depth/image_raw 2>/dev/null | sed -n "s/^Publisher count: //p")"
if [ "${depth_publishers:-0}" != "1" ]; then
  echo "ERROR: registered Astra depth is unavailable" >&2
  tail -50 /tmp/d2_astra.log >&2
  exit 1
fi
echo "D2 sensing ready: voltage=${voltage}V; registered Astra depth ready; no Nav2 and no cmd_vel command sent"
'\'''

"${SSH[@]}" 'docker exec bjtu_car bash -lc '\''
source /opt/ros/foxy/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy
ros2 topic info /scan
ros2 topic info /map
ros2 topic info /camera/depth/image_raw
ros2 action list | grep navigate_to_pose && exit 2 || true
'\'''

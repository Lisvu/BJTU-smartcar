#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
SSH=(sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON_USER@$JETSON_HOST")
SCP=(sshpass -p "$JETSON_PASSWORD" scp -o StrictHostKeyChecking=no)

"$ROOT_DIR/scripts/free_devices.sh"
"${SSH[@]}" 'docker stop smartcar_web >/dev/null 2>&1 || true; mkdir -p /home/jetson/bjtu_ros2_ws/d1'
"${SCP[@]}" "$ROOT_DIR/config/d1/nav2_online_slam.yaml" \
  "$ROOT_DIR/config/d1/slam_toolbox_online_async.yaml" \
  "$JETSON_USER@$JETSON_HOST:/home/jetson/bjtu_ros2_ws/d1/"

"${SSH[@]}" 'docker restart bjtu_car >/dev/null; docker exec bjtu_car bash -lc '\''
set -eo pipefail
ln -sf /dev/ttyUSB0 /dev/rplidar
ln -sf /dev/ttyUSB1 /dev/myserial
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/software/library_ws/install/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy
export ROBOT_TYPE=x3 RPLIDAR_TYPE=a1
nohup ros2 launch yahboomcar_nav laser_bringup_launch.py robot_type:=x3 rplidar_type:=a1 > /tmp/d1_bringup.log 2>&1 &
sleep 8
if grep -qE "SerialException|Serial Open Failed|device disconnected|multiple access" /tmp/d1_bringup.log; then
  echo "ERROR: chassis serial communication failed" >&2
  tail -40 /tmp/d1_bringup.log >&2
  exit 1
fi
voltage="$(timeout -s INT 5 ros2 topic echo /voltage std_msgs/msg/Float32 2>/dev/null | sed -n "s/^data: //p" | head -1 || true)"
voltage_whole="${voltage%%.*}"
if [ -z "$voltage_whole" ] || [ "$voltage_whole" -le 1 ]; then
  echo "ERROR: invalid chassis voltage ${voltage:-missing}; refusing to start Nav2" >&2
  exit 1
fi
echo "D1 chassis serial healthy: voltage=${voltage}V"
nohup ros2 launch slam_toolbox online_async_launch.py params_file:=/root/bjtu_ros2_ws/d1/slam_toolbox_online_async.yaml use_sim_time:=false > /tmp/d1_slam.log 2>&1 &
sleep 6
nohup ros2 launch nav2_bringup navigation_launch.py params_file:=/root/bjtu_ros2_ws/d1/nav2_online_slam.yaml use_sim_time:=false autostart:=true > /tmp/d1_nav2.log 2>&1 &
'\'''

sleep 12
"${SSH[@]}" 'docker exec bjtu_car bash -lc '\''
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/software/library_ws/install/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy
echo "D1 nodes:"; ros2 node list | sort
echo "D1 topics:"; ros2 topic list | grep -E "^/(map|odom|scan|cmd_vel|tf|tf_static)$" | sort
echo "D1 action:"; ros2 action list -t | grep navigate_to_pose || true
echo "D1 lifecycle states:"
for node in controller_server planner_server recoveries_server bt_navigator waypoint_follower; do
  printf "%s: " "$node"
  ros2 lifecycle get "/$node"
done
'\'''

echo "D1 online SLAM + Nav2 started. No navigation goal was sent."

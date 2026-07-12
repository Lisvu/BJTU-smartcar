#!/usr/bin/env bash
set -euo pipefail
JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
if ! sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON_USER@$JETSON_HOST" \
  'docker ps --format "{{.Names}}" | grep -qx bjtu_car'; then
  echo "D1 nodes already stopped; bjtu_car is not running."
  exit 0
fi
sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON_USER@$JETSON_HOST" 'docker exec bjtu_car bash -lc '\''
source /opt/ros/foxy/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy
ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist "{linear: {x: 0.0}, angular: {z: 0.0}}" >/dev/null 2>&1 || true
'\'''
sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON_USER@$JETSON_HOST" \
  'docker stop -t 5 bjtu_car >/dev/null'
if sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON_USER@$JETSON_HOST" \
  'docker ps --format "{{.Names}}" | grep -qx bjtu_car'; then
  echo "ERROR: bjtu_car is still running" >&2
  exit 1
fi
echo "D1 nodes stopped, zero velocity sent, and bjtu_car stopped."

#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
JETSON_CONTAINER="${JETSON_CONTAINER:-bjtu_car}"

JETSON_SSH="${JETSON_USER}@${JETSON_HOST}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=8)

log() {
  printf '[stop_sign_control] %s\n' "$*"
}

fail() {
  printf '[stop_sign_control] ERROR: %s\n' "$*" >&2
  exit 1
}

ssh_jetson() {
  if ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$JETSON_SSH" 'true' >/dev/null 2>&1 </dev/null; then
    ssh "${SSH_OPTS[@]}" "$JETSON_SSH" "$@"
  elif command -v sshpass >/dev/null 2>&1; then
    sshpass -p "$JETSON_PASSWORD" ssh "${SSH_OPTS[@]}" "$JETSON_SSH" "$@"
  else
    fail "SSH key login is not ready and sshpass is unavailable"
  fi
}

ssh_jetson "JETSON_CONTAINER='$JETSON_CONTAINER' bash -s" <<'REMOTE'
set -euo pipefail

log() {
  printf '[jetson-sign-stop] %s\n' "$*"
}

container_ros_prefix='source /opt/ros/foxy/setup.bash; source /root/yahboomcar_ros2_ws/software/library_ws/install/setup.bash; source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash; export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy'

if docker inspect "$JETSON_CONTAINER" >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' "$JETSON_CONTAINER")" = "true" ]; then
  docker exec "$JETSON_CONTAINER" bash -lc "$container_ros_prefix; timeout 4s ros2 topic pub --once /cmd_vel geometry_msgs/msg/Twist '{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}' >/tmp/bjtu_sign_stop_zero.log 2>&1 || true"
  log "sent zero Twist if /cmd_vel was available"

  docker exec "$JETSON_CONTAINER" bash -lc '
pkill -INT -f "[s]ign_command_node.py" 2>/dev/null || true
sleep 1
pkill -TERM -f "[s]ign_command_node.py" 2>/dev/null || true
pkill -TERM -f "[s]llidar_node" 2>/dev/null || true
pkill -TERM -f "[s]llidar_launch.py" 2>/dev/null || true
pkill -TERM -f "[M]cnamu_driver_X3" 2>/dev/null || true
sleep 1
pkill -KILL -f "[s]ign_command_node.py" 2>/dev/null || true
pkill -KILL -f "[s]llidar_node" 2>/dev/null || true
pkill -KILL -f "[s]llidar_launch.py" 2>/dev/null || true
pkill -KILL -f "[M]cnamu_driver_X3" 2>/dev/null || true
'
else
  log "container ${JETSON_CONTAINER} is not running"
fi

pids="$(pgrep -f '[d]etect_headless.py.*traffic_sign_yolov5s.pt' 2>/dev/null || true)"
if [ -n "$pids" ]; then
  log "stopping traffic-sign detector: $pids"
  kill $pids 2>/dev/null || true
  sleep 1
  pids="$(pgrep -f '[d]etect_headless.py.*traffic_sign_yolov5s.pt' 2>/dev/null || true)"
  [ -z "$pids" ] || kill -9 $pids 2>/dev/null || true
else
  log "traffic-sign detector: none"
fi

if docker inspect "$JETSON_CONTAINER" >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' "$JETSON_CONTAINER")" = "true" ]; then
  remaining="$(
    docker exec "$JETSON_CONTAINER" bash -lc 'ps -ef | grep -E "[s]ign_command_node.py|[s]llidar_node|[M]cnamu_driver_X3" || true'
  )"
  if [ -n "$remaining" ]; then
    printf '%s\n' "$remaining" | sed 's/^/[jetson-sign-stop] still_running: /' >&2
    exit 1
  fi
fi

log "traffic sign control stopped"
REMOTE

log "traffic sign control stopped on ${JETSON_SSH}"

#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
JETSON_CONTAINER="${JETSON_CONTAINER:-bjtu_car}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-11}"
ROS_DISTRO="${ROS_DISTRO:-foxy}"
RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"
SIGN_PORT="${SIGN_PORT:-5002}"

JETSON_SSH="${JETSON_USER}@${JETSON_HOST}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=8)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

log() {
  printf '[start_sign_control] %s\n' "$*"
}

fail() {
  printf '[start_sign_control] ERROR: %s\n' "$*" >&2
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

scp_to_jetson() {
  local src="$1"
  local dst="$2"
  if ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$JETSON_SSH" 'true' >/dev/null 2>&1 </dev/null; then
    scp "${SSH_OPTS[@]}" "$src" "$JETSON_SSH:$dst" >/dev/null
  elif command -v sshpass >/dev/null 2>&1; then
    sshpass -p "$JETSON_PASSWORD" scp "${SSH_OPTS[@]}" "$src" "$JETSON_SSH:$dst" >/dev/null
  else
    fail "SSH key login is not ready and sshpass is unavailable"
  fi
}

ssh_jetson 'mkdir -p /home/jetson/bjtu_ai /home/jetson/yolov5-7.0'
scp_to_jetson "$SCRIPT_DIR/sign_command_node.py" "/home/jetson/bjtu_ai/sign_command_node.py"
scp_to_jetson "$SCRIPT_DIR/detect_headless.py" "/home/jetson/bjtu_ai/detect_headless.py"
if [ -f "$PROJECT_DIR/config/traffic_signs.yaml" ]; then
  scp_to_jetson "$PROJECT_DIR/config/traffic_signs.yaml" "/home/jetson/yolov5-7.0/traffic_signs.yaml"
fi
log "synced local traffic-sign scripts to Jetson"

ssh_jetson \
  "JETSON_CONTAINER='$JETSON_CONTAINER' JETSON_PASSWORD='$JETSON_PASSWORD' ROS_DOMAIN_ID='$ROS_DOMAIN_ID' ROS_DISTRO='$ROS_DISTRO' RMW_IMPLEMENTATION='$RMW_IMPLEMENTATION' SIGN_PORT='$SIGN_PORT' bash -s" <<'REMOTE'
set -euo pipefail

log() {
  printf '[jetson-sign] %s\n' "$*"
}

fail() {
  printf '[jetson-sign] ERROR: %s\n' "$*" >&2
  exit 1
}

run_sudo() {
  if sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
  else
    printf '%s\n' "$JETSON_PASSWORD" | sudo -S -p '' "$@"
  fi
}

kill_host_pattern() {
  local label="$1"
  local pattern="$2"
  local pids
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    log "$label: none"
    return
  fi
  log "$label: $pids"
  kill $pids 2>/dev/null || true
  sleep 1
  pids="$(pgrep -f "$pattern" 2>/dev/null || true)"
  [ -z "$pids" ] || kill -9 $pids 2>/dev/null || true
}

container_ros_prefix='source /opt/ros/foxy/setup.bash; source /root/yahboomcar_ros2_ws/software/library_ws/install/setup.bash; source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash; export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy'

test -f /home/jetson/yolov5-7.0/traffic_sign_yolov5s.pt || fail "missing /home/jetson/yolov5-7.0/traffic_sign_yolov5s.pt"
test -f /home/jetson/yolov5-7.0/traffic_signs.yaml || fail "missing /home/jetson/yolov5-7.0/traffic_signs.yaml"
test -f /home/jetson/bjtu_ai/detect_headless.py || fail "missing /home/jetson/bjtu_ai/detect_headless.py"
test -f /home/jetson/bjtu_ai/sign_command_node.py || fail "missing /home/jetson/bjtu_ai/sign_command_node.py"

docker start "$JETSON_CONTAINER" >/dev/null
log "container ${JETSON_CONTAINER} is running"

if docker ps --format '{{.Names}}' | grep -qx smartcar_web; then
  docker stop smartcar_web >/dev/null || true
  log "stopped smartcar_web to release vendor launcher"
else
  log "smartcar_web not running"
fi

kill_host_pattern "stopping Rosmaster camera UI" "/home/jetson/Rosmaster-App/rosmaster/app.py"
kill_host_pattern "stopping vendor gmapping launcher" "map_gmapping_launch.py"
kill_host_pattern "stopping old traffic-sign detector" "[d]etect_headless.py.*traffic_sign_yolov5s.pt"

docker exec "$JETSON_CONTAINER" bash -lc '
pkill -INT -f "[s]ign_command_node.py" 2>/dev/null || true
pkill -TERM -f "[s]ign_command_node.py" 2>/dev/null || true
pkill -TERM -f "[s]llidar_node" 2>/dev/null || true
pkill -TERM -f "[s]llidar_launch.py" 2>/dev/null || true
pkill -TERM -f "[M]cnamu_driver_X3" 2>/dev/null || true
rm -f /tmp/bjtu_sign_lidar.log /tmp/bjtu_sign_base_driver.log /tmp/bjtu_sign_command.log
' || true
sleep 1

test -e /dev/video0 || fail "missing /dev/video0"
test -e /dev/rplidar || fail "missing /dev/rplidar on Jetson host"
test -e /dev/myserial || fail "missing /dev/myserial on Jetson host"

rplidar_target="$(readlink -f /dev/rplidar)"
myserial_target="$(readlink -f /dev/myserial)"
test -e "$rplidar_target" || fail "rplidar target missing: $rplidar_target"
test -e "$myserial_target" || fail "myserial target missing: $myserial_target"

holders="$(run_sudo fuser "$rplidar_target" "$myserial_target" /dev/video0 2>/dev/null || true)"
if [ -n "$holders" ]; then
  fail "one or more devices are still busy: $holders"
fi

docker exec "$JETSON_CONTAINER" bash -lc "
ln -sf '$rplidar_target' /dev/rplidar
ln -sf '$myserial_target' /dev/myserial
test -e /dev/rplidar
test -e /dev/myserial
test -e /dev/video0
mkdir -p /root/bjtu_ai
"
docker cp /home/jetson/bjtu_ai/sign_command_node.py "$JETSON_CONTAINER":/root/bjtu_ai/sign_command_node.py >/dev/null 2>&1 || true
docker exec "$JETSON_CONTAINER" chmod +x /root/bjtu_ai/sign_command_node.py
log "device links ready: /dev/rplidar -> ${rplidar_target}; /dev/myserial -> ${myserial_target}"

cd /home/jetson/yolov5-7.0
nohup python3 /home/jetson/bjtu_ai/detect_headless.py \
  --weights /home/jetson/yolov5-7.0/traffic_sign_yolov5s.pt \
  --data /home/jetson/yolov5-7.0/traffic_signs.yaml \
  --classes all \
  --source /dev/video0 \
  --conf-thres 0.5 \
  --serve 127.0.0.1:"$SIGN_PORT" \
  > /tmp/bjtu_sign_detect.log 2>&1 &
echo $! > /tmp/bjtu_sign_detect.pid

for _ in $(seq 1 45); do
  if grep -q "serve listening 127.0.0.1:${SIGN_PORT}" /tmp/bjtu_sign_detect.log 2>/dev/null; then
    break
  fi
  if grep -Eq "cannot open camera|Traceback|RuntimeError" /tmp/bjtu_sign_detect.log 2>/dev/null; then
    tail -80 /tmp/bjtu_sign_detect.log >&2 || true
    fail "traffic-sign detector failed"
  fi
  sleep 1
done
grep -q "serve listening 127.0.0.1:${SIGN_PORT}" /tmp/bjtu_sign_detect.log || fail "traffic-sign detector did not open socket ${SIGN_PORT}"
log "YOLO traffic-sign detector is listening on 127.0.0.1:${SIGN_PORT}"

docker exec -d "$JETSON_CONTAINER" bash -lc "
{
  set -eo pipefail
  $container_ros_prefix
  exec ros2 launch sllidar_ros2 sllidar_launch.py serial_port:=/dev/rplidar serial_baudrate:=115200 frame_id:=laser angle_compensate:=true
} > /tmp/bjtu_sign_lidar.log 2>&1
"

for _ in $(seq 1 25); do
  if docker exec "$JETSON_CONTAINER" bash -lc "$container_ros_prefix; timeout 5s ros2 topic info /scan 2>/dev/null | grep -q 'Publisher count: 1'"; then
    break
  fi
  if docker exec "$JETSON_CONTAINER" bash -lc "grep -Eq 'SL_RESULT|process has died|ERROR' /tmp/bjtu_sign_lidar.log 2>/dev/null"; then
    docker exec "$JETSON_CONTAINER" bash -lc "tail -120 /tmp/bjtu_sign_lidar.log" >&2 || true
    fail "sllidar failed"
  fi
  sleep 1
done
docker exec "$JETSON_CONTAINER" bash -lc "$container_ros_prefix; timeout 5s ros2 topic info /scan 2>/dev/null | grep -q 'Publisher count: 1'" || fail "/scan is not publishing"
log "sllidar is publishing /scan"

docker exec -d "$JETSON_CONTAINER" bash -lc "
{
  set -eo pipefail
  $container_ros_prefix
  exec ros2 run yahboomcar_bringup Mcnamu_driver_X3
} > /tmp/bjtu_sign_base_driver.log 2>&1
"

for _ in $(seq 1 20); do
  if docker exec "$JETSON_CONTAINER" bash -lc "$container_ros_prefix; timeout 5s ros2 topic info /cmd_vel 2>/dev/null | grep -q 'Subscription count: 1'"; then
    break
  fi
  sleep 1
done
docker exec "$JETSON_CONTAINER" bash -lc "$container_ros_prefix; timeout 5s ros2 topic info /cmd_vel 2>/dev/null | grep -q 'Subscription count: 1'" || fail "base driver did not subscribe /cmd_vel"
log "Mcnamu_driver_X3 subscribes /cmd_vel"

docker exec -d "$JETSON_CONTAINER" bash -lc "
{
  set -eo pipefail
  $container_ros_prefix
  exec python3 /root/bjtu_ai/sign_command_node.py --drive --ros-args \
    -p host:=127.0.0.1 -p port:=${SIGN_PORT} \
    -p conf_thres:=0.5 -p act_ratio:=0.06 \
    -p linear_speed_mps:=0.12 -p angular_speed_radps:=0.6 \
    -p max_linear_speed_mps:=0.15 -p max_angular_speed_radps:=0.8 \
    -p safety_distance_m:=0.3 -p safety_front_half_angle_deg:=25.0 \
    -p safety_ignore_below_m:=0.15 -p scan_angle_offset_deg:=180.0
} > /tmp/bjtu_sign_command.log 2>&1
"

for _ in $(seq 1 15); do
  if docker exec "$JETSON_CONTAINER" bash -lc "grep -q 'connected to sign socket' /tmp/bjtu_sign_command.log 2>/dev/null"; then
    break
  fi
  sleep 1
done
docker exec "$JETSON_CONTAINER" bash -lc "grep -q 'connected to sign socket' /tmp/bjtu_sign_command.log" || fail "sign_command_node did not connect to YOLO socket"
docker exec "$JETSON_CONTAINER" bash -lc "$container_ros_prefix; timeout 5s ros2 topic info /cmd_vel 2>/dev/null | grep -q 'Publisher count: 1'" || fail "sign_command_node is not publishing /cmd_vel"

log "traffic sign control is READY"
log "logs: /tmp/bjtu_sign_detect.log, /tmp/bjtu_sign_lidar.log, /tmp/bjtu_sign_base_driver.log, /tmp/bjtu_sign_command.log"
log "no sign/stop/no_entry => STOP; ahead => 0.12 m/s; turn_left/right => +/-0.60 rad/s"
log "front obstacle within 0.30m forces STOP"
REMOTE

log "traffic sign control started on ${JETSON_SSH}"

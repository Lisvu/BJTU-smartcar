#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
JETSON="${JETSON_USER}@${JETSON_HOST}"
SSH=(sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON")

cleanup() {
  trap - INT TERM EXIT
  printf '\nStopping D2 static fusion...\n'
  "$ROOT_DIR/scripts/stop_d2_static_fusion.sh" || true
}
trap cleanup INT TERM EXIT

"$ROOT_DIR/scripts/start_d2_static_fusion.sh"

echo "Starting traffic-sign YOLO; model loading normally takes 15-30 seconds..."
"${SSH[@]}" 'pkill -TERM -f "^python3 /home/jetson/bjtu_ai/detect_headless.py.*5002" 2>/dev/null || true; sleep 1; cd /home/jetson/yolov5-7.0; nohup python3 /home/jetson/bjtu_ai/detect_headless.py --weights /home/jetson/yolov5-7.0/traffic_sign_yolov5s.pt --data /home/jetson/yolov5-7.0/traffic_signs.yaml --classes all --source /dev/video0 --conf-thres 0.5 --serve 127.0.0.1:5002 >/tmp/d2_yolo.log 2>&1 & echo $! >/tmp/d2_yolo.pid'

for _ in $(seq 1 30); do
  if "${SSH[@]}" 'ss -lnt | grep -q "127.0.0.1:5002"'; then
    break
  fi
  sleep 1
done
if ! "${SSH[@]}" 'ss -lnt | grep -q "127.0.0.1:5002"'; then
  echo "ERROR: YOLO did not open port 5002" >&2
  "${SSH[@]}" 'tail -50 /tmp/d2_yolo.log' >&2
  exit 1
fi

echo "YOLO is ready. Starting STOP pose fusion..."
"${SSH[@]}" 'docker exec bjtu_car bash -lc '\''
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/software/library_ws/install/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp ROS_DOMAIN_ID=11 ROS_DISTRO=foxy
nohup python3 /root/bjtu_ai/stop_sign_pose_node.py --ros-args \
  -p host:=127.0.0.1 -p port:=5002 -p target_class:=stop \
  -p conf_thres:=0.5 -p hfov_deg:=60.0 -p invert_bearing:=false \
  -p scan_angle_offset_deg:=180.0 -p scan_window_deg:=2.0 \
  >/tmp/d2_fusion.log 2>&1 &
echo $! >/tmp/d2_fusion.pid
sleep 3
publishers="$(ros2 topic info /cmd_vel 2>/dev/null | sed -n "s/^Publisher count: //p")"
if [ "${publishers:-0}" != "0" ]; then
  echo "ERROR: /cmd_vel has ${publishers} publisher(s); refusing D2" >&2
  exit 2
fi
'\'''

echo "D2 ready: hold up a STOP sign. The car will not move. Press Ctrl-C to stop everything."
sshpass -p "$JETSON_PASSWORD" ssh -tt -o StrictHostKeyChecking=no "$JETSON" \
  'docker exec -i bjtu_car stdbuf -oL tail -n +1 -F /tmp/d2_fusion.log'

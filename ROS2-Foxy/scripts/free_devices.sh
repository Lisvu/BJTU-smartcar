#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
JETSON_CONTAINER="${JETSON_CONTAINER:-bjtu_car}"
JETSON_SSH="${JETSON_USER}@${JETSON_HOST}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=8)

log() {
  printf '[free_devices] %s\n' "$*"
}

fail() {
  printf '[free_devices] ERROR: %s\n' "$*" >&2
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

ssh_jetson "JETSON_PASSWORD='$JETSON_PASSWORD' JETSON_CONTAINER='$JETSON_CONTAINER' bash -s" <<'REMOTE'
set -euo pipefail

log() {
  printf '[jetson] %s\n' "$*"
}

run_sudo() {
  if sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
  else
    printf '%s\n' "$JETSON_PASSWORD" | sudo -S -p '' "$@"
  fi
}

kill_pids() {
  local label="$1"
  shift
  local pids=("$@")
  local pid
  if [ "${#pids[@]}" -eq 0 ]; then
    log "$label: none"
    return
  fi
  log "$label: ${pids[*]}"
  for pid in "${pids[@]}"; do
    run_sudo kill "$pid" 2>/dev/null || true
  done
  sleep 1
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      run_sudo kill -9 "$pid" 2>/dev/null || true
    fi
  done
}

collect_pids() {
  local category="$1"
  local proc pid cmd
  for proc in /proc/[0-9]*; do
    pid="${proc##*/}"
    cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
    [ -n "$cmd" ] || continue
    case "$category:$cmd" in
      rosmaster:*"/home/jetson/Rosmaster-App/rosmaster/app.py"*|rosmaster:*"python3 ~/Rosmaster-App/rosmaster/app.py"*)
        printf '%s\n' "$pid"
        ;;
      camera:*"astra_camera"*|camera:*"usb_cam"*|camera:*"colorHSV"*|camera:*"colorTracker"*)
        printf '%s\n' "$pid"
        ;;
      lidar:*"sllidar"*|lidar:*"ydlidar"*)
        printf '%s\n' "$pid"
        ;;
      base:*"Mcnamu_driver_X3"*|base:*"icar_bringup"*)
        printf '%s\n' "$pid"
        ;;
    esac
  done | sort -u
}

mapfile -t rosmaster_pids < <(collect_pids rosmaster)
kill_pids "stopping Rosmaster-App/app.py" "${rosmaster_pids[@]}"

mapfile -t perception_pids < <(collect_pids camera)
kill_pids "stopping camera perception processes" "${perception_pids[@]}"

mapfile -t lidar_pids < <(collect_pids lidar)
kill_pids "stopping lidar perception processes" "${lidar_pids[@]}"

mapfile -t base_pids < <(collect_pids base)
kill_pids "stopping Mcnamu_driver_X3 base holders" "${base_pids[@]}"

if docker inspect "$JETSON_CONTAINER" >/dev/null 2>&1; then
  docker start "$JETSON_CONTAINER" >/dev/null
  docker exec "$JETSON_CONTAINER" bash -lc '
set -euo pipefail
for proc in /proc/[0-9]*; do
  pid="${proc##*/}"
  [ "$pid" != "$$" ] || continue
  cmd="$(tr "\0" " " < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *astra_camera*|*usb_cam*|*colorHSV*|*colorTracker*|*sllidar*|*ydlidar*|*Mcnamu_driver_X3*|*fusion_node.py*)
      kill "$pid" 2>/dev/null || true
      ;;
  esac
done
'
fi

if [ ! -e /dev/myserial ] && [ -e /dev/ttyUSB1 ]; then
  run_sudo ln -sf /dev/ttyUSB1 /dev/myserial
fi

log "device nodes:"
ls -l /dev/video0 /dev/rplidar /dev/myserial 2>/dev/null || true

log "device holders:"
holders="$(run_sudo fuser -v /dev/video0 /dev/rplidar /dev/myserial 2>&1 || true)"
printf '%s\n' "$holders"
if printf '%s\n' "$holders" | grep -Eq '/dev/(video0|rplidar|myserial):[[:space:]]+[[:alnum:]_]'; then
  log "one or more devices still have holders"
  exit 2
fi

log "/dev/video0, /dev/rplidar, and /dev/myserial are idle"
REMOTE

log "Jetson device cleanup finished"

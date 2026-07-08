#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
JETSON_CONTAINER="${JETSON_CONTAINER:-bjtu_car}"
MAC_CONTAINER="${MAC_CONTAINER:-ros2_foxy}"
JETSON_SSH="${JETSON_USER}@${JETSON_HOST}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=8)

log() {
  printf '[stop_all] %s\n' "$*"
}

fail() {
  printf '[stop_all] ERROR: %s\n' "$*" >&2
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

stop_mac_side() {
  if docker inspect "$MAC_CONTAINER" >/dev/null 2>&1; then
    if [ "$(docker inspect -f '{{.State.Running}}' "$MAC_CONTAINER")" = "true" ]; then
      docker exec -i "$MAC_CONTAINER" bash -s <<'IN_CONTAINER' || true
set -euo pipefail
for proc in /proc/[0-9]*; do
  cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"zenoh-bridge-dds"*)
      kill "${proc##*/}" 2>/dev/null || true
      ;;
  esac
done
IN_CONTAINER
      docker stop "$MAC_CONTAINER" >/dev/null
      log "Mac zenoh bridge stopped and ${MAC_CONTAINER} container stopped"
    else
      log "Mac container ${MAC_CONTAINER} was already stopped"
    fi
  else
    log "Mac container ${MAC_CONTAINER} does not exist"
  fi
}

stop_jetson_side() {
  ssh_jetson "JETSON_CONTAINER='$JETSON_CONTAINER' bash -s" <<'REMOTE'
set -euo pipefail

log() {
  printf '[jetson] %s\n' "$*"
}

if docker inspect "$JETSON_CONTAINER" >/dev/null 2>&1 && [ "$(docker inspect -f '{{.State.Running}}' "$JETSON_CONTAINER")" = "true" ]; then
  docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
for proc in /proc/[0-9]*; do
  cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"zenoh-bridge-dds"*|*"Mcnamu_driver_X3"*)
      kill "${proc##*/}" 2>/dev/null || true
      ;;
  esac
done
IN_CONTAINER
  sleep 1
  remaining="$(
    docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
for proc in /proc/[0-9]*; do
  cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"zenoh-bridge-dds"*|*"Mcnamu_driver_X3"*)
      printf '%s %s\n' "${proc##*/}" "$cmd"
      ;;
  esac
done
IN_CONTAINER
  )"
  if [ -n "$remaining" ]; then
    printf '%s\n' "$remaining" | sed 's/^/[jetson] still_running: /' >&2
    exit 1
  fi
  log "Mcnamu_driver_X3 and Jetson zenoh bridge stopped"
else
  log "container ${JETSON_CONTAINER} is not running"
fi
REMOTE
}

main() {
  stop_mac_side
  stop_jetson_side
  log "all stopped; Mac ROS2 container is off for cooling"
}

main "$@"

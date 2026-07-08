#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
JETSON_CONTAINER="${JETSON_CONTAINER:-bjtu_car}"
MAC_CONTAINER="${MAC_CONTAINER:-ros2_foxy}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-11}"
ROS_DISTRO="${ROS_DISTRO:-foxy}"
RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

JETSON_SSH="${JETSON_USER}@${JETSON_HOST}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=8)

log() {
  printf '[start_all] %s\n' "$*"
}

fail() {
  printf '[start_all] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing command: $1"
}

ssh_key_works() {
  ssh "${SSH_OPTS[@]}" -o BatchMode=yes "$JETSON_SSH" 'true' >/dev/null 2>&1
}

select_pubkey() {
  if [ -f "$HOME/.ssh/id_ed25519.pub" ]; then
    printf '%s\n' "$HOME/.ssh/id_ed25519.pub"
    return
  fi
  if [ -f "$HOME/.ssh/id_rsa.pub" ]; then
    printf '%s\n' "$HOME/.ssh/id_rsa.pub"
    return
  fi
  mkdir -p "$HOME/.ssh"
  chmod 700 "$HOME/.ssh"
  ssh-keygen -t ed25519 -N '' -f "$HOME/.ssh/id_ed25519" -C "bjtu-ros2-$(hostname)" >/dev/null
  printf '%s\n' "$HOME/.ssh/id_ed25519.pub"
}

ensure_ssh_key() {
  if ssh_key_works; then
    log "SSH key login already works for ${JETSON_SSH}"
    return
  fi

  require_cmd sshpass
  require_cmd ssh-copy-id

  local pubkey
  pubkey="$(select_pubkey)"
  log "installing SSH key on ${JETSON_SSH}"
  sshpass -p "$JETSON_PASSWORD" ssh-copy-id "${SSH_OPTS[@]}" -i "$pubkey" "$JETSON_SSH" >/dev/null
  ssh_key_works || fail "SSH key login still failed after ssh-copy-id"
}

resolve_jetson_ip() {
  local ip
  ip="$(dscacheutil -q host -a name "$JETSON_HOST" 2>/dev/null | awk '/ip_address:/ && $2 ~ /^[0-9.]+$/ {print $2; exit}')"
  if [ -z "$ip" ]; then
    ip="$(ping -c 1 -t 2 "$JETSON_HOST" 2>/dev/null | sed -n 's/^PING .* (\([0-9.][0-9.]*\)).*/\1/p' | head -1)"
  fi
  if [ -z "$ip" ]; then
    ip="$(ssh "${SSH_OPTS[@]}" "$JETSON_SSH" "hostname -I | tr ' ' '\n' | awk '/^192\\.168\\.43\\./ {print; exit} /^[0-9]+\\./ {candidate=\\$0} END {if (candidate) print candidate}'" 2>/dev/null || true)"
  fi
  [ -n "$ip" ] || fail "could not resolve Jetson IP from ${JETSON_HOST}"
  printf '%s\n' "$ip"
}

ssh_jetson() {
  ssh "${SSH_OPTS[@]}" "$JETSON_SSH" "$@"
}

start_jetson_side() {
  ssh_jetson \
    "JETSON_CONTAINER='$JETSON_CONTAINER' JETSON_PASSWORD='$JETSON_PASSWORD' ROS_DOMAIN_ID='$ROS_DOMAIN_ID' ROS_DISTRO='$ROS_DISTRO' RMW_IMPLEMENTATION='$RMW_IMPLEMENTATION' bash -s" <<'REMOTE'
set -euo pipefail

log() {
  printf '[jetson] %s\n' "$*"
}

fail() {
  printf '[jetson] ERROR: %s\n' "$*" >&2
  exit 1
}

run_sudo() {
  if sudo -n true >/dev/null 2>&1; then
    sudo -n "$@"
    return
  fi
  if [ -n "${JETSON_PASSWORD:-}" ]; then
    printf '%s\n' "$JETSON_PASSWORD" | sudo -S -p '' "$@"
    return
  fi
  fail "sudo requires a password; set JETSON_PASSWORD or configure passwordless sudo"
}

docker_container_for_pid() {
  local pid="$1"
  local name
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    if docker top "$name" -eo pid 2>/dev/null | awk 'NR > 1 {print $1}' | grep -qx "$pid"; then
      printf '%s\n' "$name"
      return
    fi
  done < <(docker ps --format '{{.Names}}')
  printf 'host\n'
}

pid_command() {
  local pid="$1"
  ps -p "$pid" -o args= 2>/dev/null || true
}

ttyusb1_holder_pids() {
  if command -v fuser >/dev/null 2>&1; then
    (run_sudo fuser /dev/ttyUSB1 2>/dev/null || true) | tr ' ' '\n' | awk 'NF {print}'
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    run_sudo lsof -t /dev/ttyUSB1 2>/dev/null || true
    return
  fi
  return 0
}

check_host_serial_ownership() {
  local pids
  local pid
  local owner
  local cmd
  local external=0

  if sudo -n true >/dev/null 2>&1; then
    log "host serial check uses passwordless sudo"
  else
    log "host serial check uses sudo with configured Jetson password"
  fi
  run_sudo true >/dev/null || fail "sudo authentication failed during host serial check"

  if command -v fuser >/dev/null 2>&1; then
    log "host serial check command: sudo fuser /dev/ttyUSB1"
  elif command -v lsof >/dev/null 2>&1; then
    log "host serial check command: sudo lsof -t /dev/ttyUSB1"
  else
    log "host serial check command: docker top fallback for Mcnamu_driver_X3"
  fi

  pids="$(ttyusb1_holder_pids | sort -u)"
  if [ -n "$pids" ]; then
    while IFS= read -r pid; do
      [ -n "$pid" ] || continue
      owner="$(docker_container_for_pid "$pid")"
      cmd="$(pid_command "$pid")"
      printf '[jetson] host_serial_holder: pid=%s container=%s cmd=%s\n' "$pid" "$owner" "$cmd"
      if [ "$owner" != "$JETSON_CONTAINER" ]; then
        external=1
      fi
    done <<EOF
$pids
EOF
    if [ "$external" -ne 0 ]; then
      fail "/dev/ttyUSB1 is held outside ${JETSON_CONTAINER}; ask the other driver owner to stop before running this script"
    fi
    log "host serial /dev/ttyUSB1 is only held by ${JETSON_CONTAINER}"
    return
  fi

  if ! command -v fuser >/dev/null 2>&1 && ! command -v lsof >/dev/null 2>&1; then
    while IFS= read -r owner; do
      [ -n "$owner" ] || continue
      if [ "$owner" = "$JETSON_CONTAINER" ]; then
        continue
      fi
      if docker top "$owner" -eo pid,args 2>/dev/null | grep -q 'Mcnamu_driver_X3'; then
        docker top "$owner" -eo pid,args 2>/dev/null | grep 'Mcnamu_driver_X3' | sed "s/^/[jetson] external_driver container=${owner} /"
        fail "another container is running Mcnamu_driver_X3; ask the other driver owner to stop before running this script"
      fi
    done <<EOF
$(docker ps --format '{{.Names}}')
EOF
  fi

  log "host serial /dev/ttyUSB1 is idle"
}

docker start "$JETSON_CONTAINER" >/dev/null
log "container ${JETSON_CONTAINER} is running"

check_host_serial_ownership

docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
if [ ! -e /dev/myserial ] && [ -e /dev/ttyUSB1 ]; then
  ln -s /dev/ttyUSB1 /dev/myserial
fi
test -e /dev/myserial
test -e /dev/ttyUSB1
test -f /opt/ros/foxy/lib/librmw_cyclonedds_cpp.so
test -x /usr/local/bin/zenoh-bridge-dds
IN_CONTAINER
log "/dev/myserial and cyclone/zenoh dependencies are present"

driver_pids="$(
  docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
for proc in /proc/[0-9]*; do
  cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"/yahboomcar_bringup/Mcnamu_driver_X3"*|*"ros2 run yahboomcar_bringup Mcnamu_driver_X3"*) printf '%s\n' "${proc##*/}" ;;
  esac
done
IN_CONTAINER
)"

serial_holders="$(
  docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
for fd in /proc/[0-9]*/fd/*; do
  target="$(readlink "$fd" 2>/dev/null || true)"
  case "$target" in
    /dev/ttyUSB1|/dev/myserial)
      pid="${fd#/proc/}"
      pid="${pid%%/*}"
      cmd="$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)"
      printf '%s %s\n' "$pid" "$cmd"
      ;;
  esac
done | sort -u
IN_CONTAINER
)"

if [ -n "$serial_holders" ]; then
  if [ -n "$driver_pids" ] && printf '%s\n' "$serial_holders" | grep -q '/yahboomcar_bringup/Mcnamu_driver_X3'; then
    log "serial /dev/myserial is already owned by bjtu_car Mcnamu_driver_X3"
    printf '%s\n' "$serial_holders" | sed 's/^/[jetson] serial_holder: /'
  else
    printf '%s\n' "$serial_holders" | sed 's/^/[jetson] serial_holder: /' >&2
    fail "/dev/myserial is busy; ask the other driver owner to stop before running this script"
  fi
else
  log "serial /dev/myserial is idle"
fi

if [ -z "$driver_pids" ]; then
  docker exec -d "$JETSON_CONTAINER" bash -lc "
set -eo pipefail
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export ROS_DISTRO='${ROS_DISTRO}'
export ROS_DOMAIN_ID='${ROS_DOMAIN_ID}'
export RMW_IMPLEMENTATION='${RMW_IMPLEMENTATION}'
exec ros2 run yahboomcar_bringup Mcnamu_driver_X3 > /tmp/bjtu_base_driver.log 2>&1
"
fi

driver_pids=""
for _ in $(seq 1 20); do
  driver_pids="$(
    docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
for proc in /proc/[0-9]*; do
  cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"/yahboomcar_bringup/Mcnamu_driver_X3"*|*"ros2 run yahboomcar_bringup Mcnamu_driver_X3"*) printf '%s\n' "${proc##*/}" ;;
  esac
done
IN_CONTAINER
  )"
  [ -n "$driver_pids" ] && break
  sleep 1
done
[ -n "$driver_pids" ] || fail "Mcnamu_driver_X3 did not start; see /tmp/bjtu_base_driver.log inside ${JETSON_CONTAINER}"
log "Mcnamu_driver_X3 running with pid(s): $(printf '%s' "$driver_pids" | tr '\n' ' ')"

docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
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

docker exec -d "$JETSON_CONTAINER" bash -lc "
set -euo pipefail
export ROS_DISTRO='${ROS_DISTRO}'
export ROS_DOMAIN_ID='${ROS_DOMAIN_ID}'
export RMW_IMPLEMENTATION='${RMW_IMPLEMENTATION}'
export RUST_LOG=info
exec /usr/local/bin/zenoh-bridge-dds -d '${ROS_DOMAIN_ID}' -l tcp/0.0.0.0:7447 > /tmp/bjtu_jetson_bridge.log 2>&1
"
sleep 2

bridge_pids="$(
  docker exec -i "$JETSON_CONTAINER" bash -s <<'IN_CONTAINER'
set -euo pipefail
for proc in /proc/[0-9]*; do
  cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"zenoh-bridge-dds"*) printf '%s\n' "${proc##*/}" ;;
  esac
done
IN_CONTAINER
)"
[ -n "$bridge_pids" ] || fail "Jetson zenoh bridge did not start; see /tmp/bjtu_jetson_bridge.log inside ${JETSON_CONTAINER}"
log "Jetson zenoh bridge running with pid(s): $(printf '%s' "$bridge_pids" | tr '\n' ' ')"

docker exec "$JETSON_CONTAINER" bash -lc "
source /opt/ros/foxy/setup.bash
source /root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash
export ROS_DISTRO='${ROS_DISTRO}'
export ROS_DOMAIN_ID='${ROS_DOMAIN_ID}'
export RMW_IMPLEMENTATION='${RMW_IMPLEMENTATION}'
ros2 node info /driver_node 2>/dev/null | grep -q '/cmd_vel: geometry_msgs/msg/Twist'
"
log "driver_node subscribes /cmd_vel"
REMOTE
}

start_mac_side() {
  local jetson_ip="$1"
  docker start "$MAC_CONTAINER" >/dev/null
  log "container ${MAC_CONTAINER} is running"

  docker exec "$MAC_CONTAINER" bash -lc "test -f /opt/ros/foxy/lib/librmw_cyclonedds_cpp.so && test -x /usr/local/bin/zenoh-bridge-dds" \
    || fail "Mac container is missing cyclone RMW or zenoh-bridge-dds"

  docker exec -i "$MAC_CONTAINER" bash -s <<'IN_CONTAINER'
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

  docker exec -d "$MAC_CONTAINER" bash -lc "
set -euo pipefail
export ROS_DISTRO='${ROS_DISTRO}'
export ROS_DOMAIN_ID='${ROS_DOMAIN_ID}'
export RMW_IMPLEMENTATION='${RMW_IMPLEMENTATION}'
export RUST_LOG=info
exec /usr/local/bin/zenoh-bridge-dds -d '${ROS_DOMAIN_ID}' -e tcp/${jetson_ip}:7447 > /tmp/bjtu_mac_bridge.log 2>&1
"
  sleep 2

  docker exec -i "$MAC_CONTAINER" bash -s <<'IN_CONTAINER' >/dev/null || fail "Mac zenoh bridge did not start; see /tmp/bjtu_mac_bridge.log inside ros2_foxy"
set -euo pipefail
found=0
for proc in /proc/[0-9]*; do
  cmd="$(tr '\0' ' ' < "$proc/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *"zenoh-bridge-dds"*) found=1 ;;
  esac
done
test "$found" = 1
IN_CONTAINER
  log "Mac zenoh bridge connected to tcp/${jetson_ip}:7447"
}

main() {
  require_cmd docker
  require_cmd ssh
  ensure_ssh_key

  local jetson_ip
  jetson_ip="$(resolve_jetson_ip)"
  log "Jetson IP: ${jetson_ip}"
  ping -c 1 -t 2 "$jetson_ip" >/dev/null 2>&1 || fail "Jetson ${jetson_ip} is not reachable from Mac"

  start_jetson_side
  start_mac_side "$jetson_ip"

  log "ready: jetson_ip=${jetson_ip}; jetson_container=${JETSON_CONTAINER}; mac_container=${MAC_CONTAINER}; driver=running; jetson_bridge=running; mac_bridge=running"
}

main "$@"

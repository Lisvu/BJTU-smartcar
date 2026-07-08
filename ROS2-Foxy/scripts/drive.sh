#!/usr/bin/env bash
set -euo pipefail

MAC_CONTAINER="${MAC_CONTAINER:-ros2_foxy}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-11}"
ROS_DISTRO="${ROS_DISTRO:-foxy}"
RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

if ! docker inspect "$MAC_CONTAINER" >/dev/null 2>&1; then
  printf '[drive] ERROR: Mac ROS2 container %s does not exist. Run scripts/start_all.sh first.\n' "$MAC_CONTAINER" >&2
  exit 1
fi

if [ "$(docker inspect -f '{{.State.Running}}' "$MAC_CONTAINER")" != "true" ]; then
  printf '[drive] ERROR: Mac ROS2 container %s is not running. Run scripts/start_all.sh first.\n' "$MAC_CONTAINER" >&2
  exit 1
fi

cat <<'HELP'
[drive] Keyboard teleop will publish /cmd_vel through ROS_DOMAIN_ID=11.
[drive] Initial speed is low: speed=0.15, turn=0.5.
[drive] Keys: i forward, , backward, j/l turn, u/o/m/. diagonal.
[drive] Stop: press k or space. Emergency exit: press Ctrl+C after stopping.
HELP

exec docker exec -it "$MAC_CONTAINER" bash -lc "
source /opt/ros/foxy/setup.bash
export ROS_DISTRO='${ROS_DISTRO}'
export ROS_DOMAIN_ID='${ROS_DOMAIN_ID}'
export RMW_IMPLEMENTATION='${RMW_IMPLEMENTATION}'
exec ros2 run teleop_twist_keyboard teleop_twist_keyboard --ros-args -p speed:=0.15 -p turn:=0.5
"

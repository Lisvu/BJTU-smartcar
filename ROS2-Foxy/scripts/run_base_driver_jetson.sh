#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export ROS2_WORKSPACE_SETUP="${ROS2_WORKSPACE_SETUP:-/root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash}"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env_cyclonedds.sh"

if [ ! -e /dev/myserial ] && [ -e /dev/ttyUSB1 ]; then
  ln -s /dev/ttyUSB1 /dev/myserial
fi

exec ros2 run yahboomcar_bringup Mcnamu_driver_X3

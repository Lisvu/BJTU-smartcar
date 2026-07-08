#!/usr/bin/env bash
set -eo pipefail

export ROS_DISTRO="${ROS_DISTRO:-foxy}"
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-11}"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_cyclonedds_cpp}"

if [ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]; then
  # shellcheck disable=SC1090
  source "/opt/ros/${ROS_DISTRO}/setup.bash"
fi

if [ -n "${ROS2_WORKSPACE_SETUP:-}" ] && [ -f "${ROS2_WORKSPACE_SETUP}" ]; then
  # shellcheck disable=SC1090
  source "${ROS2_WORKSPACE_SETUP}"
fi

set -u

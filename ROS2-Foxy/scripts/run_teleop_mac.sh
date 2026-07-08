#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env_cyclonedds.sh"

exec ros2 run teleop_twist_keyboard teleop_twist_keyboard "$@"

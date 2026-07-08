#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env_cyclonedds.sh"

export RUST_LOG="${RUST_LOG:-info}"

BRIDGE_BIN="${ZENOH_BRIDGE_DDS_BIN:-/usr/local/bin/zenoh-bridge-dds}"
JETSON_ENDPOINT="${JETSON_ZENOH_ENDPOINT:-tcp/192.168.43.84:7447}"

exec "${BRIDGE_BIN}" -d "${ROS_DOMAIN_ID}" -e "${JETSON_ENDPOINT}"

#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env_cyclonedds.sh"

export RUST_LOG="${RUST_LOG:-info}"

BRIDGE_BIN="${ZENOH_BRIDGE_DDS_BIN:-/usr/local/bin/zenoh-bridge-dds}"
LISTEN_ENDPOINT="${ZENOH_LISTEN_ENDPOINT:-tcp/0.0.0.0:7447}"
DENY_REGEX="${ZENOH_DENY_REGEX:-.*cmd_vel.*}"

exec "${BRIDGE_BIN}" -d "${ROS_DOMAIN_ID}" -l "${LISTEN_ENDPOINT}" --deny "${DENY_REGEX}"

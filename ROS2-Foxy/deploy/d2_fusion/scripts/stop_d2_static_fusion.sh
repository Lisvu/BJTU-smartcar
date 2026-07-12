#!/usr/bin/env bash
set -euo pipefail

JETSON_HOST="${JETSON_HOST:-jetson-desktop.local}"
JETSON_USER="${JETSON_USER:-jetson}"
JETSON_PASSWORD="${JETSON_PASSWORD:-yahboom}"
SSH=(sshpass -p "$JETSON_PASSWORD" ssh -o StrictHostKeyChecking=no "$JETSON_USER@$JETSON_HOST")

"${SSH[@]}" 'pkill -TERM -f "^python3 /home/jetson/bjtu_ai/detect_headless.py.*5002" 2>/dev/null || true; docker stop -t 5 bjtu_car >/dev/null 2>&1 || true'
echo "D2 detector, fusion container, lidar, SLAM, and chassis driver stopped."

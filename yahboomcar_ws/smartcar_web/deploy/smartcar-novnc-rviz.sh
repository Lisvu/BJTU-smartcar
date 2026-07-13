#!/usr/bin/env bash
set -euo pipefail

DISPLAY_NUM=1
GEOMETRY="1440x900"
VNC_PORT=5901
NOVNC_PORT=6080

start_xvnc() {
  pkill -f "Xtigervnc :${DISPLAY_NUM}" 2>/dev/null || true
  rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
  /usr/bin/Xtigervnc ":${DISPLAY_NUM}" \
    -geometry "${GEOMETRY}" \
    -depth 24 \
    -SecurityTypes None \
    -ac \
    >/tmp/smartcar-xvnc-rviz.log 2>&1 &
  sleep 2
  DISPLAY=":${DISPLAY_NUM}" openbox >/tmp/smartcar-openbox-rviz.log 2>&1 &
}

start_websockify() {
  pkill -f "websockify --web=/usr/share/novnc 0.0.0.0:${NOVNC_PORT}" 2>/dev/null || true
  /usr/bin/websockify --web=/usr/share/novnc 0.0.0.0:${NOVNC_PORT} localhost:${VNC_PORT} \
    >/tmp/smartcar-websockify-rviz.log 2>&1 &
}

start_xvnc
start_websockify

while true; do
  if ! ss -ltn | grep -q ":${VNC_PORT} "; then
    start_xvnc
  fi
  if ! ss -ltn | grep -q ":${NOVNC_PORT} "; then
    start_websockify
  fi
  wid=$(DISPLAY=":${DISPLAY_NUM}" wmctrl -l 2>/dev/null | grep -E "RViz|rviz2" | head -n1 | cut -d" " -f1 || true)
  if [ -n "$wid" ]; then
    DISPLAY=":${DISPLAY_NUM}" wmctrl -i -r "$wid" -e 0,0,0,1440,900 >/dev/null 2>&1 || true
  fi
  sleep 3
done

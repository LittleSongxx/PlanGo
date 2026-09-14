#!/usr/bin/env bash
# 桌面演示入口：先起虚拟显示与 VNC 通道，再前台启动 PlanGo 桌面。
# 画面经 websockify 以 noVNC 提供；中文输入请用 noVNC 剪贴板面板粘贴（容器内无输入法）。
set -euo pipefail

SCREEN="${PLANGO_DEMO_SCREEN:-1920x1200x24}"
VNC_PORT="${PLANGO_DEMO_VNC_PORT:-6080}"

Xvfb :99 -screen 0 "$SCREEN" -nolisten tcp &
export DISPLAY=:99
for _ in $(seq 1 50); do
  [ -e /tmp/.X11-unix/X99 ] && break
  sleep 0.2
done

x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet &
websockify -D --web /usr/share/novnc "$VNC_PORT" localhost:5900

exec ./node_modules/.bin/electron . --no-sandbox --disable-dev-shm-usage

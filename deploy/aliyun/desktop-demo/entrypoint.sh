#!/usr/bin/env bash
# 桌面演示入口：先起虚拟显示与 VNC 通道，再前台启动 PlanGo 桌面。
# 画面经 websockify 以 noVNC 提供；中文输入请用 noVNC 剪贴板面板粘贴（容器内无输入法）。
set -uo pipefail

SCREEN="${PLANGO_DEMO_SCREEN:-1920x1200x24}"
VNC_PORT="${PLANGO_DEMO_VNC_PORT:-6080}"

Xvfb :99 -screen 0 "$SCREEN" -nolisten tcp &
export DISPLAY=:99

# x11vnc 在 Xvfb 尚未接受连接时会立刻退出；重试直到它稳定驻留，且不因它失败而拖死桌面。
for _ in $(seq 1 30); do
  [ -e /tmp/.X11-unix/X99 ] || { sleep 0.3; continue; }
  x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet &
  vnc_pid=$!
  sleep 1
  if kill -0 "$vnc_pid" 2>/dev/null; then
    break
  fi
  sleep 0.7
done

websockify -D --web /usr/share/novnc "$VNC_PORT" localhost:5900 || true

exec ./node_modules/.bin/electron . --no-sandbox --disable-dev-shm-usage

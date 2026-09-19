#!/usr/bin/env bash
# B站课程进度追踪 —— 网页版启动器
# 行为：本地服务没在跑就用与桌面版共享的数据目录后台拉起 server.py，
#       然后用系统默认浏览器的普通标签页打开。
# 可重复点击：服务已在跑时不会二次启动，只开标签页。
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ROOT_DIR="${BILI_TRACKER_ROOT:-$HOME/.local/share/bili-course-tracker}"
URL="http://127.0.0.1:8765"

port_up() { (exec 3<>"/dev/tcp/127.0.0.1/8765") 2>/dev/null; }

if ! port_up; then
  mkdir -p "$ROOT_DIR"
  nohup env BILI_TRACKER_ROOT="$ROOT_DIR" python3 "$HERE/../src/server.py" \
    >> "$ROOT_DIR/server-web.log" 2>&1 &
  # 最多等 8 秒等服务就绪
  for _ in $(seq 1 16); do port_up && break; sleep 0.5; done
fi

setsid xdg-open "$URL" >/dev/null 2>&1 &

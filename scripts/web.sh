#!/usr/bin/env bash
# B站课程进度追踪 —— 网页版启动器
# 行为：本地服务没在跑就用与桌面版共享的数据目录后台拉起 server.py，
#       然后用 Edge --app 打开无地址栏的独立窗口；没有 Edge 则用系统默认浏览器。
# 可重复点击：服务已在跑时不会二次启动，只开窗口。
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

if command -v microsoft-edge >/dev/null 2>&1; then
  # --window-size 每次强制尺寸（Edge 应用窗口会记忆上次大小，不传可能变成竖长条）
  # --force-dark-mode 让 Edge 界面（含窗口标题栏）走深色，与页面风格一致
  setsid microsoft-edge --app="$URL" --window-size=1100,760 --force-dark-mode >/dev/null 2>&1 &
else
  setsid xdg-open "$URL" >/dev/null 2>&1 &
fi

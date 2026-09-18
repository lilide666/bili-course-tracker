#!/usr/bin/env bash
# Linux 浏览器模式启动脚本（不需要 pywebview，直接用系统浏览器打开）
# 依赖：python3、pip 包 keyring pillow qrcode
set -e
cd "$(dirname "$0")"

# 检查依赖
python3 -c "import keyring, PIL, qrcode" 2>/dev/null || {
  echo "[*] 安装依赖：keyring pillow qrcode"
  pip3 install keyring pillow qrcode
}

# 启动服务（后台）
python3 src/server.py &
SRV_PID=$!

# 等服务就绪
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8765/ >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

# 用默认浏览器打开
xdg-open http://127.0.0.1:8765/ 2>/dev/null || echo "请手动打开 http://127.0.0.1:8765/"

echo "服务运行中（PID $SRV_PID），按 Ctrl+C 退出"
trap "kill $SRV_PID 2>/dev/null" EXIT
wait

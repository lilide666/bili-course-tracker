#!/usr/bin/env bash
# Linux 启动脚本（原生无边框窗口：GTK3 + WebKit2GTK）
# 系统依赖（deb 名）：python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
# Python 依赖：keyring pillow qrcode（缺失自动装到项目内 .pydeps）
set -e
cd "$(dirname "$0")/.."   # scripts/ -> 项目根目录

DEPS_DIR="$PWD/.pydeps"
export PYTHONPATH="$DEPS_DIR${PYTHONPATH:+:$PYTHONPATH}"

# 检查原生窗口系统库
if ! python3 -c "
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('WebKit2', '4.1')
" 2>/dev/null; then
  echo "[!] 缺少 GTK/WebKit 系统库，请先执行："
  echo "    sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1"
  exit 1
fi

# 检查 Python 依赖；缺失则装到项目内 .pydeps（不污染系统，规避 PEP 668）
python3 -c "import keyring, PIL, qrcode" 2>/dev/null || {
  echo "[*] 安装依赖到 $DEPS_DIR"
  pip3 install --break-system-packages --target="$DEPS_DIR" keyring pillow qrcode
}

# 启动桌面应用（窗口进程，前台运行，关闭窗口即退出）
exec python3 src/app.py

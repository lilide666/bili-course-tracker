# -*- coding: utf-8 -*-
"""一键打包脚本（位于 scripts/，可从任意目录运行）
  Windows : `py -3 scripts\build.py`（或双击 scripts\build.bat）—— PyInstaller onedir + --noconsole
  Linux   : `python3 scripts/build.py` —— 源码组装为 .deb（不打包解释器，依赖走 Depends）

Windows 行为：
  - 应用正在运行时自动关闭（先请求正常退出，卡住才强杀）
  - 打包完成覆盖应用目录后自动启动新版本
  - 产出：B站课程进度追踪\\B站课程进度追踪.exe

Linux 行为：
  - 源码（src/app.py 唯一入口，GTK3+WebKit2GTK）装进 /opt/bili-course-tracker
  - 组装为 .deb：/opt 源码 + /usr/bin 启动脚本 + 应用菜单 + 图标
  - 依赖（python3-gi/gir1.2-webkit2 等）由 control 的 Depends 声明，apt 自动安装
  - 产出：build/bili-course-tracker_<版本>_<架构>.deb
"""
import os
import shutil
import subprocess
import sys
import time

# 本文件在 scripts/ 下：项目根 = 上一级目录
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Windows 产物名（中文）；Linux 二进制/包名用英文，菜单显示名用中文
APP_NAME = "B站课程进度追踪"
APP_DIR = os.path.join(ROOT, APP_NAME)
BUILD_DIR = os.path.join(ROOT, "build")

LINUX_BIN = "bili-course-tracker"
DEB_VERSION = "1.0.0"

# Linux 下自动把项目内 .pydeps 加进路径（与 run.sh 同源，不污染系统 Python）
_DEPS = os.path.join(ROOT, ".pydeps")
if os.path.isdir(_DEPS) and _DEPS not in sys.path:
    sys.path.insert(0, _DEPS)


# ============================ Windows ============================

def ensure_app_closed():
    """应用运行时 exe 被占用无法覆盖：自动关闭（先请求退出，卡住才强杀）。"""
    def running():
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s.exe" % APP_NAME],
                           capture_output=True, text=True)
        return APP_NAME in (r.stdout or "")

    if not running():
        return
    print("[!] 自动关闭 %s.exe ..." % APP_NAME)
    subprocess.run(["taskkill", "/IM", APP_NAME + ".exe"], capture_output=True)  # 请求退出，走正常关闭流程
    time.sleep(1.0)
    if running():
        subprocess.run(["taskkill", "/IM", APP_NAME + ".exe", "/F"], capture_output=True)  # 兜底强杀
        time.sleep(0.5)
    if running():
        print("[!] 无法关闭应用（可能权限不足），请手动关闭后重试。")
        sys.exit(1)


def build_windows():
    ensure_app_closed()

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"      # 不往 Python 安装目录写缓存
    env["PYINSTALLER_CONFIG_DIR"] = os.path.join(BUILD_DIR, "pyi-cache")

    cmd = [
        sys.executable, "-B", "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onedir",        # 目录模式：免解压，启动/关闭都快
        "--noconsole",     # 隐藏 cmd 黑窗口
        "--name", APP_NAME,
        "--icon", os.path.join(ROOT, "assets", "app_icon.ico"),
        "--paths", os.path.join(ROOT, "src"),
        "--add-data", os.path.join(ROOT, "src", "index.html") + ";.",
        "--hidden-import", "qrcode",
        "--hidden-import", "qrcode.main",
        "--hidden-import", "qrcode.generator",
        "--hidden-import", "qrcode.constants",
        "--hidden-import", "keyring",
        "--hidden-import", "keyring.backends",
        "--hidden-import", "keyring.backends.Windows",
        "--hidden-import", "keyring.backends.SecretService",
        "--hidden-import", "keyring.backends.kwallet",
        "--hidden-import", "keyring.backends.macOS",
        "--specpath", os.path.join(BUILD_DIR, "spec"),
        "--distpath", os.path.join(BUILD_DIR, "dist"),
        "--workpath", os.path.join(BUILD_DIR, "work"),
        os.path.join(ROOT, "src", "app.py"),
    ]
    print(">>", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)

    # 只覆盖 exe 与 _internal；应用目录里的数据文件（json/covers 等）不受影响
    shutil.copytree(os.path.join(BUILD_DIR, "dist", APP_NAME), APP_DIR,
                    dirs_exist_ok=True)
    print("=" * 56)
    print(" 打包完成:", os.path.join(APP_DIR, APP_NAME + ".exe"))
    print("=" * 56)

    # 自动启动新版本（os.startfile 以独立进程打开，不随打包脚本退出）
    exe_path = os.path.join(APP_DIR, APP_NAME + ".exe")
    try:
        os.startfile(exe_path)
        print("[*] 已启动新版本")
    except OSError as e:
        print("[!] 自动启动失败，请手动打开: %s（%s）" % (exe_path, e))


# ============================ Linux ============================

def _convert_icon_png(dst_png, size=256):
    """ico 转 PNG（Linux hicolor 图标用）。"""
    from PIL import Image
    img = Image.open(os.path.join(ROOT, "assets", "app_icon.ico"))
    img = img.convert("RGBA")
    if img.size != (size, size):
        img = img.resize((size, size), Image.LANCZOS)
    img.save(dst_png, format="PNG")


def build_linux():
    """组装源码 deb（不打包 Python 解释器/依赖库，体积小、启动快）。

    布局：
      /opt/bili-course-tracker/{src,assets}  程序源码（系统 Python 直跑）
      /usr/bin/bili-course-tracker           启动脚本
      /usr/share/applications/               应用菜单
      /usr/share/icons/hicolor/              图标
    运行时依赖由 Depends 声明，apt 安装时自动配齐（GTK/WebKit/keyring/PIL/qrcode）。
    """
    # 已安装到系统的旧版本若在运行，先请求退出（不匹配本构建脚本自身）
    subprocess.run(["pkill", "-f", "/opt/%s/src/app.py" % LINUX_BIN],
                   capture_output=True)

    arch = subprocess.run(["dpkg", "--print-architecture"],
                          capture_output=True, text=True, check=True).stdout.strip()

    deb_root = os.path.join(BUILD_DIR, "deb-root")
    if os.path.exists(deb_root):
        shutil.rmtree(deb_root)

    # ---- /opt 源码 ----
    opt_dir = os.path.join(deb_root, "opt", LINUX_BIN)
    src_dst = os.path.join(opt_dir, "src")
    os.makedirs(src_dst)
    for fn in ("app.py", "server.py", "index.html", "make_icon.py"):
        shutil.copy2(os.path.join(ROOT, "src", fn), os.path.join(src_dst, fn))
    shutil.copytree(os.path.join(ROOT, "assets"),
                    os.path.join(opt_dir, "assets"))

    # ---- /usr/bin 启动脚本 ----
    bin_dir = os.path.join(deb_root, "usr", "bin")
    os.makedirs(bin_dir)
    bin_file = os.path.join(bin_dir, LINUX_BIN)
    with open(bin_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("#!/bin/sh\n"
                "exec python3 /opt/%s/src/app.py \"$@\"\n" % LINUX_BIN)
    os.chmod(bin_file, 0o755)

    # ---- 应用菜单 ----
    apps_dir = os.path.join(deb_root, "usr", "share", "applications")
    os.makedirs(apps_dir)
    with open(os.path.join(apps_dir, LINUX_BIN + ".desktop"), "w",
              encoding="utf-8") as f:
        f.write("""[Desktop Entry]
Type=Application
Name=%s
Comment=B站课程观看进度追踪
Exec=/usr/bin/%s
Icon=%s
Categories=Utility;Education;
Terminal=false
""" % (APP_NAME, LINUX_BIN, LINUX_BIN))

    # ---- hicolor 图标 ----
    icon_dir = os.path.join(deb_root, "usr", "share", "icons",
                            "hicolor", "256x256", "apps")
    os.makedirs(icon_dir)
    _convert_icon_png(os.path.join(icon_dir, LINUX_BIN + ".png"))

    # ---- DEBIAN/control ----
    installed_kb = sum(os.lstat(os.path.join(dp, n)).st_size
                       for dp, _, fns in os.walk(deb_root) for n in fns) // 1024
    os.makedirs(os.path.join(deb_root, "DEBIAN"))
    with open(os.path.join(deb_root, "DEBIAN", "control"), "w",
              encoding="utf-8") as f:
        f.write("""Package: %s
Version: %s
Architecture: %s
Maintainer: lilide666 <lilide666@users.noreply.github.com>
Section: utils
Priority: optional
Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1, python3-keyring, python3-pil, python3-qrcode
Installed-Size: %d
Description: B站课程观看进度追踪
  本地运行的 B站课程观看进度追踪应用。通过 B站历史接口实时读取观看进度，
  支持多课程追踪、跳跃/回看检测、合集合并、番茄钟等功能。
""" % (LINUX_BIN, DEB_VERSION, arch, installed_kb))

    # ---- 构建 .deb（文件所有者归 root:root，无需 sudo 打包）----
    deb_file = os.path.join(BUILD_DIR,
                            "%s_%s_%s.deb" % (LINUX_BIN, DEB_VERSION, arch))
    if os.path.exists(deb_file):
        os.remove(deb_file)
    subprocess.run(["dpkg-deb", "--build", "--root-owner-group",
                    deb_root, deb_file], check=True)

    print("=" * 56)
    print(" 打包完成:", deb_file)
    print(" 安装命令 : sudo apt install ./%s" % os.path.basename(deb_file))
    print("=" * 56)


# ============================ 入口 ============================

def main():
    if sys.platform.startswith("win"):
        build_windows()
    elif sys.platform.startswith("linux"):
        build_linux()
    else:
        print("暂不支持的平台:", sys.platform)
        sys.exit(1)


if __name__ == "__main__":
    main()

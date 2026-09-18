# -*- coding: utf-8 -*-
"""一键打包脚本：在项目根目录运行 `py -3 build.py`，或双击 打包.bat。

行为：
  - 应用正在运行时自动关闭（先请求正常退出，卡住才强杀）
  - 打包完成覆盖应用目录后自动启动新版本

产出：B站课程进度追踪\\B站课程进度追踪.exe
  - onedir 模式：无需每次解压，启动和关闭都很快
  - --noconsole：不弹 cmd 黑窗口
  - index.html 内嵌进包内，应用目录只保留数据文件
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "B站课程进度追踪"
APP_DIR = os.path.join(ROOT, APP_NAME)
BUILD_DIR = os.path.join(ROOT, "build")


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


def main():
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


if __name__ == "__main__":
    main()

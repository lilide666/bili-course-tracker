# -*- coding: utf-8 -*-
"""一键打包脚本：在项目根目录运行 `py -3 build.py`，或双击 打包.bat。

产出：B站课程进度追踪\\B站课程进度追踪.exe
  - onedir 模式：无需每次解压，启动和关闭都很快
  - --noconsole：不弹 cmd 黑窗口
  - index.html 内嵌进包内，应用目录只保留数据文件
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "B站课程进度追踪"
APP_DIR = os.path.join(ROOT, APP_NAME)
BUILD_DIR = os.path.join(ROOT, "build")


def main():
    # 应用正在运行时 exe 被占用，无法覆盖，提前提示
    r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s.exe" % APP_NAME],
                       capture_output=True, text=True)
    if APP_NAME in (r.stdout or ""):
        print("[!] %s.exe 正在运行，请先关闭应用再打包。" % APP_NAME)
        sys.exit(1)

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


if __name__ == "__main__":
    main()

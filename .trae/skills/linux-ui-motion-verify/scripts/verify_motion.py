#!/usr/bin/env python3
"""Linux 动效采样：真实 HTTP 服务 + GTK 离屏窗口加载真实页面，
触发一段 JS 动效后按时间偏移多帧截图。

示例：
  python3 verify_motion.py --trigger "playSheen()" --wait 3500 --samples 260,680

截图保存为 <outdir>/motion_<ms>.png。
"""
import argparse
import os
import sys
import threading

import gi
os.environ.setdefault("GSETTINGS_BACKEND", "memory")  # 测试环境可能无权访问 dconf
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, GLib, WebKit2  # noqa: E402

# scripts/ -> linux-ui-motion-verify/ -> skills/ -> .trae/ -> 项目根（4 级）
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

ap = argparse.ArgumentParser()
ap.add_argument("--trigger", default="playSheen()", help="页面加载稳定后执行的 JS")
ap.add_argument("--wait", type=int, default=3500, help="load-finished 后触发前等待毫秒")
ap.add_argument("--samples", default="260,680", help="触发后截图的毫秒偏移，逗号分隔")
ap.add_argument("--outdir", default="/tmp", help="截图输出目录")
ap.add_argument("--width", type=int, default=1120)
ap.add_argument("--height", type=int, default=840)
args = ap.parse_args()

# 数据目录：默认 XDG（/opt 与源码目录不可写）
os.environ.setdefault(
    "BILI_TRACKER_ROOT",
    os.path.join(os.path.expanduser("~"), ".local", "share", "bili-course-tracker"))

import server  # noqa: E402  必须在 BILI_TRACKER_ROOT 就位后导入

os.makedirs(server.ROOT, exist_ok=True)
server.migrate_from_course_data()
httpd, port = server.make_server()
threading.Thread(target=httpd.serve_forever, daemon=True).start()

web = WebKit2.WebView()
win = Gtk.OffscreenWindow()
win.set_default_size(args.width, args.height)
win.add(web)
win.show_all()
web.load_uri("http://%s:%d/" % (server.HOST, port))

os.makedirs(args.outdir, exist_ok=True)
offsets = sorted(int(x) for x in args.samples.split(",") if x.strip())
fired = False
files = []


def capture(offset_ms):
    pb = win.get_pixbuf()
    f = os.path.join(args.outdir, "motion_%d.png" % offset_ms)
    pb.savev(f, "png", [], [])
    files.append(f)


def fire():
    web.run_javascript(args.trigger, None, None, None)
    for ms in offsets:
        # 每个 offset 独立的一次性定时器
        GLib.timeout_add(ms, lambda ms=ms: (capture(ms), False)[1])
    GLib.timeout_add(max(offsets) + 400, Gtk.main_quit)
    return False


def on_changed(_v, status):
    global fired
    if status == WebKit2.LoadEvent.FINISHED and not fired:
        fired = True
        GLib.timeout_add(args.wait, fire)


web.connect("load-changed", on_changed)
GLib.timeout_add(args.wait + max(offsets) + 20000, Gtk.main_quit)  # 兜底
Gtk.main()

for f in files:
    print(f)
if not files:
    print("未产出截图", file=sys.stderr)
    sys.exit(1)

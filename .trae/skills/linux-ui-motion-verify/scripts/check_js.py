#!/usr/bin/env python3
"""用系统 WebKit 的真实解析器校验 src/index.html 内 <script> 的语法。

输出 OK 或 FAIL <错误信息>。退出码：通过 0，失败 1。
"""
import json
import os
import re
import sys

import gi
os.environ.setdefault("GSETTINGS_BACKEND", "memory")  # 测试环境可能无权访问 dconf
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, GLib, WebKit2  # noqa: E402

# scripts/ -> linux-ui-motion-verify/ -> skills/ -> .trae/ -> 项目根（4 级）
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

html = open(os.path.join(PROJECT_ROOT, "src", "index.html"), encoding="utf-8").read()
m = re.search(r"<script>(.*?)</script>", html, re.S)
if not m:
    print("FAIL 未找到 <script> 块")
    sys.exit(1)

payload = (
    "<html><body><script>"
    "try{new Function(%s);document.title='OK';}"
    "catch(e){document.title='FAIL '+e.message;}"
    "</script></body></html>"
) % json.dumps(m.group(1))

web = WebKit2.WebView()
win = Gtk.OffscreenWindow()
win.add(web)
win.show_all()
web.load_html(payload, None)

result = {}


def read_title():
    result["title"] = web.get_title()
    Gtk.main_quit()


def on_changed(_v, status):
    if status == WebKit2.LoadEvent.FINISHED:
        GLib.timeout_add(300, read_title)


web.connect("load-changed", on_changed)
GLib.timeout_add(8000, Gtk.main_quit)
Gtk.main()

title = result.get("title", "FAIL 解析超时")
print(title)
sys.exit(0 if title == "OK" else 1)

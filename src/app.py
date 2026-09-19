# -*- coding: utf-8 -*-
"""桌面应用唯一入口（Windows / Linux 共用本文件）。

【AI 助手注意】开发前必读项目根目录 AGENTS.md（架构约定、窗口缩放方案、踩坑记录）；
改动架构/约定后必须同步更新 AGENTS.md，它是随仓库转移的项目记忆。

平台分支（功能与交互保持一致：无边框 + 深色自绘标题栏 + 拖动 + 双击最大化
+ 八向缩放 + 最小化/关闭）：
  - Windows：pywebview(WebView2) + Win32 —— 见本文件「Windows」节
  - Linux  ：GTK3 + WebKit2GTK 4.1（系统库，源码直跑，不打包解释器）
             —— 见本文件「Linux」节
共用部分：本地 HTTP 服务（server.py）、界面（index.html）、启动常量。

用法：
  - Windows：双击打包后的 exe；或开发模式 py -3 src\\app.py
  - Linux  ：bash scripts/run.sh；或 python3 src/app.py
"""
import http.server
import os
import sys
import threading
import time
import urllib.request

APP_TITLE = "B站课程进度追踪"
BG = "#0b1020"  # 与 index.html 的 --bg 一致，避免启动期白闪
WIN_SIZE = (1120, 840)
WIN_MIN = (720, 560)


# ===================== Linux：数据目录环境（必须在 import server 前完成） =====================
# /opt 系统级安装普通用户无写权限，运行时数据统一放用户 XDG 数据目录；
# server.py 在 import 时计算 ROOT，所以 BILI_TRACKER_ROOT 必须先就位。
def _linux_data_root():
    _xdg = os.environ.get("XDG_DATA_HOME", "").strip() or \
        os.path.join(os.path.expanduser("~"), ".local", "share")
    root = os.path.join(_xdg, "bili-course-tracker")
    os.makedirs(root, exist_ok=True)
    return root


if sys.platform.startswith("linux"):
    os.environ["BILI_TRACKER_ROOT"] = _linux_data_root()

import server as srv  # 复用同目录 server.py 的 Handler / HOST / PORT


# ===================== 共用：本地 HTTP 服务 =====================
def start_http_server():
    """后台线程用：绑定本地服务，端口被占（双开）时自动向后回退。

    回写 srv.PORT 为实际端口，界面加载/健康检查都用它。
    """
    srv.migrate_from_course_data()
    for port in range(srv.PORT, srv.PORT + 10):
        try:
            httpd = http.server.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
            srv.PORT = port
            return httpd
        except OSError:
            continue
    raise OSError(f"无法绑定本地端口 {srv.PORT}-{srv.PORT + 9}（可能已有实例在运行）")


def wait_ready(timeout=6.0):
    """轮询直到服务可访问。"""
    url = f"http://{srv.HOST}:{srv.PORT}/"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


# #########################################################################
# ##############################  Windows  ################################
# #########################################################################
# 技术栈：pywebview 承载 WebView2，frameless 无边框；边缘缩放走 Win32。
# #########################################################################

def open_in_browser():
    """Windows 回退方案：未装 pywebview 时用默认浏览器打开并保持服务运行。"""
    import webbrowser
    url = f"http://{srv.HOST}:{srv.PORT}/"
    webbrowser.open(url)
    print(f"已在浏览器打开：{url}")
    print("关闭服务请按 Ctrl+C。")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已退出")


# ---- Windows 启动画面（原生小窗，主窗口就绪后自动关闭） ----

def _run_splash(stop_evt):
    """在独立线程运行 tkinter 启动画面；stop_evt 置位或超时后自动关闭。"""
    try:
        import tkinter as tk
        root = tk.Tk()
        root.overrideredirect(True)  # 无边框
        root.attributes("-topmost", True)
        root.configure(bg=BG)
        w, h = 320, 110
        x = (root.winfo_screenwidth() - w) // 2
        y = (root.winfo_screenheight() - h) // 2
        root.geometry(f"{w}x{h}+{x}+{y}")
        tk.Label(root, text=APP_TITLE, bg=BG, fg="#7ea6ff",
                 font=("Microsoft YaHei UI", 13, "bold")).pack(pady=(26, 4))
        tk.Label(root, text="正在启动，请稍候…", bg=BG, fg="#9aa6cc",
                 font=("Microsoft YaHei UI", 10)).pack()
        deadline = time.time() + 20  # 兜底：异常时 20 秒后自动消失

        def pump():
            if stop_evt.is_set() or time.time() > deadline:
                try:
                    root.destroy()
                except Exception:
                    pass
                return
            root.after(60, pump)
        root.after(60, pump)
        root.mainloop()
    except Exception:
        pass  # 无 tkinter 等异常时静默跳过启动画面


def show_splash():
    stop = threading.Event()
    threading.Thread(target=_run_splash, args=(stop,), daemon=True).start()
    return stop


# ---- Windows JS API：窗口最小化 / 隐藏 / 边缘缩放 ----

class WinApi:
    """暴露给前端 window.pywebview.api 的窗口控制接口（Windows）。"""

    def __init__(self):
        self._win = None

    def bind(self, win):
        self._win = win

    def minimize(self):
        try:
            self._win.minimize()
        except Exception:
            pass

    def hide_window(self):
        """立即隐藏原生窗口（关闭时先调用，实现视觉秒关）。"""
        try:
            self._win.hide()
        except Exception:
            pass

    def resize_edge(self, edge=""):
        """边缘缩放：把窗口对应边/角直接贴合当前鼠标位置（绝对语义）。

        JS 只传方向；尺寸与位置在调用瞬间由 GetCursorPos + GetWindowRect
        以物理像素同坐标系计算，无 CSS 换算、无 JS 几何滞后，不会累积漂移。
        """
        if not self._win:
            return None
        try:
            import ctypes
            from ctypes import wintypes
            form = self._win.native
            if form is None:
                return None
            hwnd = form.Handle.ToInt32()  # .NET IntPtr 不能直接 int()
            u32 = ctypes.windll.user32
            pt = wintypes.POINT()
            if not u32.GetCursorPos(ctypes.byref(pt)):
                return None

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
            rect = RECT()
            if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None

            L, T, R, B = rect.left, rect.top, rect.right, rect.bottom
            x, y, w, h = L, T, R - L, B - T
            e = edge or ""
            if "e" in e:
                w = max(60, pt.x - L)
            if "s" in e:
                h = max(40, pt.y - T)
            if "w" in e:
                w = max(60, R - pt.x)
                x = R - w
            if "n" in e:
                h = max(40, B - pt.y)
                y = B - h
            # 最小尺寸由 pywebview 的 MinimumSize（物理像素）兜底
            u32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0010 | 0x0004)
        except Exception:
            pass
        return None


def run_windows():
    splash_stop = show_splash()
    httpd = start_http_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ready = wait_ready()
    try:
        import webview  # 需 pywebview
    except ImportError:
        splash_stop.set()
        print("未安装 pywebview，改用默认浏览器打开。")
        print("如需独立窗口体验：py -3 -m pip install pywebview")
        if ready:
            open_in_browser()
        return

    api = WinApi()
    # hidden=True：等页面加载完成再显示，避免出现空白窗口；
    # frameless=True：去掉系统标题栏与边框，由页面内自定义标题栏接管拖动/按钮；
    # easy_drag=False：仅带 pywebview-drag-region 类的元素可拖动，避免误选文字
    window = webview.create_window(
        APP_TITLE, f"http://{srv.HOST}:{srv.PORT}/",
        width=WIN_SIZE[0], height=WIN_SIZE[1], min_size=WIN_MIN,
        frameless=True, easy_drag=False, hidden=True,
        background_color=BG, js_api=api)
    api.bind(window)

    def on_loaded():
        try:
            window.show()
        except Exception:
            pass
        splash_stop.set()  # 就绪：关闭启动画面

    def on_closing():
        # Alt+F4 / 任务栏关闭：先隐藏窗口，让退出过程不可见
        try:
            window.hide()
        except Exception:
            pass

    window.events.loaded += on_loaded
    window.events.closing += on_closing

    # 兜底：万一 loaded 事件未触发，8 秒后强制显示
    def force_show():
        if not splash_stop.is_set():
            try:
                window.show()
            except Exception:
                pass
            splash_stop.set()
    t = threading.Timer(8, force_show)
    t.daemon = True
    t.start()

    # 窗口关闭后主线程退出。立即结束进程（_exit 跳过解释器清理），保证秒关
    webview.start()
    os._exit(0)


# #########################################################################
# ###############################   Linux   ###############################
# #########################################################################
# 技术栈：GTK3 无边框窗口 + 系统 WebKit2GTK；Wayland 下交互移动/缩放必须由
# 合成器接管且需要按下事件的 serial（JS 异步消息拿不到），所以边缘热区命中
# 判断全部在原生 button-press-event 中用事件坐标完成，不走 JS。
# #########################################################################

LX_TITLEBAR_H = 38    # 与页面 .titlebar 高度一致
LX_EDGE = 7           # 边缘缩放热区宽度（px）
LX_BTN_ZONE_W = 92    # 标题栏右侧按钮区宽度（最小化/关闭），区内不拖动

# 注入到页面的 pywebview shim：必须在文档最开始注入，
# 页面 bootstrap 据 window.pywebview 是否存在判断"原生/浏览器"环境
_LX_SHIM_JS = r"""
document.documentElement.classList.add('linux-native');
window.pywebview = window.pywebview || {};
window.pywebview.api = {
  minimize: function () {
    try { webkit.messageHandlers.native.postMessage('minimize'); } catch (e) {}
  },
  hide_window: function () {
    try { webkit.messageHandlers.native.postMessage('close'); } catch (e) {}
  },
  resize_edge: function () { /* 边缘缩放由原生 button-press 统一处理 */ }
};
"""

# 首帧占位：WebView 透明，真实页面首帧绘制前避免露出桌面
_LX_PLACEHOLDER_HTML = (
    '<html><body style="margin:0;height:100vh;background:#0a1024;'
    'border-radius:12px"></body></html>')


def _lx_state_path():
    return os.path.join(srv.ROOT, "ui_state.json")


def _lx_load_state():
    import json
    try:
        with open(_lx_state_path(), "r", encoding="utf-8") as f:
            s = json.load(f)
        if isinstance(s, dict):
            return s
    except (OSError, ValueError):
        pass
    return {}


def _lx_save_state(state):
    import json
    try:
        with open(_lx_state_path(), "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError:
        pass


def _lx_close_app(win, state):
    """统一退出路径：保存窗口状态后进程退出（HTTP 服务随进程结束）。"""
    if win.is_maximized():
        state["maximized"] = True
    else:
        w, h = win.get_size()
        state["w"] = w
        state["h"] = h
        state["maximized"] = False
    _lx_save_state(state)
    os._exit(0)


def _lx_on_message(um, result, ctx):
    win, state = ctx
    val = result.get_js_value()
    msg = val.to_string() if val else ""
    if msg == "minimize":
        win.iconify()
    elif msg == "close":
        _lx_close_app(win, state)


def _lx_on_button_press(view, ev, win):
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    if ev.button != 1:
        return False
    ww, wh = win.get_size()
    x, y = ev.x, ev.y

    # 1) 边缘缩放（7px 边带优先；同时靠边时取角）
    left = x <= LX_EDGE
    right = x >= ww - LX_EDGE - 1
    top = y <= LX_EDGE
    bottom = y >= wh - LX_EDGE - 1
    edge = None
    if top and left:
        edge = Gdk.WindowEdge.NORTH_WEST
    elif top and right:
        edge = Gdk.WindowEdge.NORTH_EAST
    elif bottom and left:
        edge = Gdk.WindowEdge.SOUTH_WEST
    elif bottom and right:
        edge = Gdk.WindowEdge.SOUTH_EAST
    elif top:
        edge = Gdk.WindowEdge.NORTH
    elif bottom:
        edge = Gdk.WindowEdge.SOUTH
    elif left:
        edge = Gdk.WindowEdge.WEST
    elif right:
        edge = Gdk.WindowEdge.EAST
    if edge is not None:
        # PyGObject 暴露的是 5 参数老 API：(edge, button, root_x, root_y, time)
        win.begin_resize_drag(edge, ev.button,
                              int(ev.x_root), int(ev.y_root), ev.time)
        return False

    # 2) 标题栏区域：右侧按钮区不处理，点击照常下发给按钮
    if y < LX_TITLEBAR_H and x < ww - LX_BTN_ZONE_W:
        if ev.type == Gdk.EventType._2BUTTON_PRESS:
            if win.is_maximized():
                win.unmaximize()
            else:
                win.maximize()
            return False
        if ev.type == Gdk.EventType.BUTTON_PRESS:
            # 4 参数老 API：(button, root_x, root_y, time)
            win.begin_move_drag(ev.button,
                                int(ev.x_root), int(ev.y_root), ev.time)
    return False


def _lx_on_window_state(win, ev, view):
    """最大化时通知页面取消圆角（铺满屏幕），还原后恢复。"""
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk
    if ev.new_window_state & Gdk.WindowState.MAXIMIZED:
        view.run_javascript(
            "document.body.classList.add('native-maximized')", None, None, None)
    else:
        view.run_javascript(
            "document.body.classList.remove('native-maximized')", None, None, None)


def run_linux():
    import gi
    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gdk, GLib, Gtk, WebKit2

    os.makedirs(srv.ROOT, exist_ok=True)
    httpd = start_http_server()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    win = Gtk.Window()
    win.set_decorated(False)
    win.set_title(APP_TITLE)

    # RGBA 视觉：圆角外侧透明，露出桌面
    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)
    win.set_app_paintable(True)

    state = _lx_load_state()
    if state.get("maximized"):
        win.maximize()
    else:
        win.set_default_size(int(state.get("w", 1100)),
                             int(state.get("h", 760)))

    # 最小尺寸
    geo = Gdk.Geometry()
    geo.min_width = 860
    geo.min_height = 560
    win.set_geometry_hints(None, geo, Gdk.WindowHints.MIN_SIZE)

    um = WebKit2.UserContentManager()
    um.add_script(WebKit2.UserScript(
        _LX_SHIM_JS,
        WebKit2.UserContentInjectedFrames.TOP_FRAME,
        WebKit2.UserScriptInjectionTime.START,
        None, None))
    um.register_script_message_handler("native")
    um.connect("script-message-received::native", _lx_on_message,
               (win, state))

    view = WebKit2.WebView.new_with_user_content_manager(um)
    view.set_name("mainview")
    # WebView 背景透明：圆角由页面 body 自身绘制，角外像素透出桌面
    view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    win.add(view)

    # GTK 窗口整体透明，不画任何矩形底（底色全部来自页面）
    provider = Gtk.CssProvider()
    provider.load_from_data(b"window, #mainview { background: transparent; }")
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    view.connect("button-press-event", _lx_on_button_press, win)
    win.connect("window-state-event", _lx_on_window_state, view)
    win.connect("destroy", lambda w: _lx_close_app(w, state))

    # 先上深色占位（避免透明穿帮），再加载真实页面
    view.load_html(_LX_PLACEHOLDER_HTML, None)
    url = "http://%s:%d/" % (srv.HOST, srv.PORT)
    GLib.timeout_add(80, lambda: (view.load_uri(url), False)[1])
    win.show_all()
    Gtk.main()


# ===================== 平台分发 =====================
def main():
    if sys.platform.startswith("win"):
        run_windows()
    elif sys.platform.startswith("linux"):
        run_linux()
    else:
        # 其他系统兜底：仅启动服务并提示浏览器访问
        os.makedirs(srv.ROOT, exist_ok=True)
        httpd = start_http_server()
        print(f"请用浏览器打开 http://{srv.HOST}:{srv.PORT}/，Ctrl+C 退出")
        httpd.serve_forever()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""桌面应用入口：pywebview 窗口 + 内嵌本地服务。

用法：
  - 双击 B站课程进度追踪\\B站课程进度追踪.exe（推荐，免装环境）
  - 或开发模式：py -3 src\\app.py

体验设计：
  - 启动：先弹出原生「正在启动」小窗，主窗口加载完成后无缝切换
  - 关闭：点击 X 先立即隐藏窗口再退出进程（视觉秒关，不受退出清理影响）
  - 缩放：窗口八条边/角可自由拉伸（JS 边缘热区只报方向 -> Python 端让边缘贴合鼠标）
若未安装 pywebview，回退用默认浏览器打开。
"""
import http.server
import os
import threading
import time
import urllib.request

import server as srv  # 复用同目录 server.py 的 Handler / HOST / PORT

APP_TITLE = "B站课程进度追踪"
BG = "#0b1020"  # 与 index.html 的 --bg 一致，避免启动期白闪
WIN_SIZE = (1120, 840)
WIN_MIN = (720, 560)


def start_server():
    """在后台线程启动本地 HTTP 服务；端口被占（如双开）时自动向后回退。"""
    srv.migrate_from_course_data()
    httpd = None
    for port in range(srv.PORT, srv.PORT + 10):
        try:
            httpd = http.server.ThreadingHTTPServer((srv.HOST, port), srv.Handler)
            srv.PORT = port  # 回写实际端口，wait_ready / 浏览器回退都用它
            break
        except OSError:
            continue
    if httpd is None:
        raise OSError(f"无法绑定本地端口 {srv.PORT}-{srv.PORT + 9}（可能已有实例在运行）")
    httpd.serve_forever()


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


def open_in_browser():
    """回退方案：用默认浏览器打开并保持服务运行。"""
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


# ===================== 启动画面（原生小窗，主窗口就绪后自动关闭） =====================

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


# ===================== JS API：窗口最小化 / 隐藏 / 边缘缩放 =====================

class Api:
    """暴露给前端 window.pywebview.api 的窗口控制接口。"""

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


def main():
    splash_stop = show_splash()
    threading.Thread(target=start_server, daemon=True).start()
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

    api = Api()
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


if __name__ == "__main__":
    main()

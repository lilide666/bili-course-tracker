# -*- coding: utf-8 -*-
"""pywebview 动效实测模板（可运行示例：番茄钟时间滚轮 + 按钮状态切换验证）。

用法：复制到 %TEMP% 后改三处，不要放仓库内——
  ① PROJECT_SRC：本仓库 src 目录的绝对路径
  ② JS_SAMPLE：要采样的字段（getComputedStyle/querySelector，读属性前判空）
  ③ on_loaded 里「动作时间轴」：点击/触发什么、在哪些时刻采样和截图
  ④ SCREENSHOT_PREFIX：截图文件名前缀（默认已用 %TEMP%，可不改）
"""
import threading, time, sys, os, ctypes
PROJECT_SRC = r"C:\path\to\bilibili_work\src"   # ← ① 改成你的仓库 src 目录
sys.path.insert(0, PROJECT_SRC)
import webview, importlib.util
spec = importlib.util.spec_from_file_location("srv", os.path.join(PROJECT_SRC, "server.py"))
srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)
PORT = 8790 + (os.getpid() % 100)          # 随机高端口，避免和运行中的应用冲突
srv.PORT = PORT
import http.server
httpd = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

SCREENSHOT_PREFIX = os.path.join(os.environ.get("TEMP", "."), "ft")   # ④ 截图前缀（%TEMP%）

def grab(hwnd, path):
    """PrintWindow 截图（WebView2 必须 PW_RENDERFULLCONTENT=2）"""
    import ctypes.wintypes as wt
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect = wt.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right-rect.left, rect.bottom-rect.top
    hdc = user32.GetWindowDC(hwnd); mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h); gdi32.SelectObject(mdc, bmp)
    user32.PrintWindow(hwnd, mdc, 2)
    class BMIH(ctypes.Structure):
        _fields_=[("biSize",ctypes.c_uint32),("biWidth",ctypes.c_int32),("biHeight",ctypes.c_int32),
                  ("biPlanes",ctypes.c_uint16),("biBitCount",ctypes.c_uint16),("biCompression",ctypes.c_uint32),
                  ("biSizeImage",ctypes.c_uint32),("biXPPM",ctypes.c_int32),("biYPPM",ctypes.c_int32),
                  ("biClrUsed",ctypes.c_uint32),("biClrImportant",ctypes.c_uint32)]
    bmi=BMIH(ctypes.sizeof(BMIH),w,-h,1,32,0,0,0,0,0,0)   # 负高度=top-down
    buf=ctypes.create_string_buffer(w*h*4)
    gdi32.GetDIBits(mdc,bmp,0,h,buf,ctypes.byref(bmi),0)
    from PIL import Image
    Image.frombuffer('RGBA',(w,h),buf.raw,'raw','BGRA',0,1).convert('RGB').save(path)
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(mdc); user32.ReleaseDC(hwnd,hdc)

# ① 采样脚本：返回 JSON 字符串；访问元素属性前先判空（null 上读属性会让整轮采样失败）
JS_SAMPLE = r"""
(function(){
  var b = document.getElementById('btnFocus');
  var cs = getComputedStyle(b);
  var after = getComputedStyle(b, '::after');
  var ft = document.getElementById('focusTime');
  var rolls = ft.querySelectorAll('.droll');
  var lastRollTf = rolls.length ? getComputedStyle(rolls[rolls.length-1]).transform : null;
  return JSON.stringify({
    focusMode: document.body.classList.contains('focus-mode'),
    btnBg: cs.backgroundColor,
    btnBorder: cs.borderTopColor,
    btnColor: cs.color,
    afterOp: after.opacity,
    slotCount: ft.querySelectorAll('.dslot').length,
    lastRollTf: lastRollTf,
    ftText: ft.textContent,
    btnTxt: b.textContent
  });
})()
"""

win = webview.create_window("test", f'http://127.0.0.1:{PORT}/', width=1120, height=1000)

def on_loaded():
    hwnd = win.native.Handle.ToInt32()    # .NET IntPtr 不能直接 int()
    time.sleep(3.0)                       # 等首屏 + 首次同步
    t_start = time.time()                 # 时间轴基准：while time.time()-t_start<t: sleep(0.02)
    def since(t):
        while time.time() - t_start < t: time.sleep(0.02)
    def sample(tag):
        print('STATE', tag, win.evaluate_js(JS_SAMPLE), flush=True)

    # ② 动作时间轴（示例：番茄钟开始→秒位滚动→落定→结束；用时 <5s 避免写入真实 focus.json）
    win.evaluate_js("document.getElementById('btnFocus').scrollIntoView({block:'center'})")
    time.sleep(0.4)
    sample('initial'); grab(hwnd, SCREENSHOT_PREFIX + "_initial.png")
    win.evaluate_js("document.getElementById('btnFocus').click()")
    since(0.05);  sample('t+050ms')    # 状态切换瞬间：背景是否恒定
    since(0.30);  sample('t+300ms')    # 过渡应已完成
    since(0.45);  grab(hwnd, SCREENSHOT_PREFIX + "_flyin.png")
    since(1.60);  sample('t+1600ms')   # 滚动中：transform≠落定值
    grab(hwnd, SCREENSHOT_PREFIX + "_midroll.png")
    since(2.25);  sample('t+2250ms')   # 落定：transform 回归
    since(4.30);  win.evaluate_js("document.getElementById('btnFocus').click()")
    since(4.70);  sample('after_stop') # 终值断言：结构保留、状态复原
    grab(hwnd, SCREENSHOT_PREFIX + "_after.png")
    os._exit(0)                        # 必须 os._exit，否则 webview 线程挂住不退出

win.events.loaded += on_loaded
webview.start()

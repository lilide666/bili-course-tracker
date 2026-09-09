---
name: "ui-motion-verify"
description: "Verifies UI animations in the bili-tracker pywebview app via real-window screenshots (PrintWindow) and evaluate_js state sampling. Invoke after changing any animation in src/index.html (number reels, sheen, confetti, transitions)."
---

# 前端动效实测验证（bili-course-tracker）

修改 `src/index.html` 的任何动效（老虎机数字、流光、纸屑、过渡、呼吸灯等）后，**必须用真实窗口实测**，不能只靠静态分析或"代码看起来对"。本项目动效踩过的坑（数字落位错、动画被中间帧打断、状态灯矛盾）全都是静态分析没看出来的。

## 验证三步走

### 1. JS 语法校验（必做）

```powershell
py -3 -B -c "import io,re; s=io.open(r'C:\Users\18509\Desktop\bilibili_work\src\index.html',encoding='utf-8').read(); m=re.search(r'<script>(.*?)</script>', s, re.S); io.open(r'C:\Users\18509\AppData\Local\Temp\check.js','w',encoding='utf-8').write(m.group(1))"
node --check C:\Users\18509\AppData\Local\Temp\check.js
```

### 2. 纯逻辑用 node 单测（有算法时）

数字滚轮落位、状态机派生等不依赖 DOM 的逻辑，直接 `node -e` 跑全组合（例：0→0 到 9→9 共 100 组落位断言），比起窗口快。

### 3. pywebview 真实窗口实测（动效必做）

写临时脚本到 `%TEMP%`（不要放仓库内），骨架：

```python
# -*- coding: utf-8 -*-
import threading, time, sys, os, ctypes
sys.path.insert(0, r"C:\Users\18509\Desktop\bilibili_work\src")
import webview, importlib.util
spec = importlib.util.spec_from_file_location("srv", r"C:\Users\18509\Desktop\bilibili_work\src\server.py")
srv = importlib.util.module_from_spec(spec); spec.loader.exec_module(srv)
PORT = 8790 + (os.getpid() % 100)   # 随机高端口，避免和运行中的应用冲突
srv.PORT = PORT
import http.server
httpd = http.server.ThreadingHTTPServer(('127.0.0.1', PORT), srv.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()

def grab(hwnd, path):
    """PrintWindow 截图（WebView2 必须用 PW_RENDERFULLCONTENT=2）"""
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
    bmi=BMIH(ctypes.sizeof(BMIH),w,-h,1,32,0,0,0,0,0,0)  # 负高度=top-down
    buf=ctypes.create_string_buffer(w*h*4)
    gdi32.GetDIBits(mdc,bmp,0,h,buf,ctypes.byref(bmi),0)
    from PIL import Image
    Image.frombuffer('RGBA',(w,h),buf.raw,'raw','BGRA',0,1).convert('RGB').save(path)
    gdi32.DeleteObject(bmp); gdi32.DeleteDC(mdc); user32.ReleaseDC(hwnd,hdc)

win = webview.create_window("test", f'http://127.0.0.1:{PORT}/', width=1120, height=1000)
def on_loaded():
    hwnd = win.native.Handle.ToInt32()   # .NET IntPtr 不能直接 int()
    time.sleep(3.0)                      # 等首屏加载+首次同步
    # 采样：getComputedStyle 读 transform/opacity/stroke-dashoffset；querySelectorAll 计数
    print('STATE', win.evaluate_js("JSON.stringify({...})"), flush=True)
    # 截图后用 Read 工具看 png 确认视觉
    grab(hwnd, r"C:\Users\18509\AppData\Local\Temp\shot.png")
    os._exit(0)                          # 必须 os._exit，否则 webview 线程挂住不退出
win.events.loaded += on_loaded
webview.start()
```

运行（GUI + 本地服务，需关沙箱）：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'; py -3 -B C:\Users\18509\AppData\Local\Temp\xxx.py 2>&1 | Select-String "STATE|Error|Traceback"
```

命令可能转入后台，读 output.log；PowerShell profile 的 PSSecurityException 报错可忽略。

## 采样要点

- **动画中段和结束都要采**：用 `t0=time.time()` 基准，循环 `while time.time()-t0 < at: sleep(0.02)` 等到目标时刻；不要用 sleep 差值累加（误差累积）。
- **CSS 动画状态**：`getComputedStyle(el).transform / .opacity / .strokeDashoffset`，动画结束后 opacity 应回到设计值（如 0）。
- **触发类动效**：evaluate_js 里 `el.classList.remove('x'); void el.getBoundingClientRect(); el.classList.add('x')` 等效重启 CSS 动画，可直接测试不必等真实同步。
- **落位类动效**：结束后采样文本/属性断言最终值正确（如数字定格 100.0、金色环 class 存在）。
- **多轮切换**：切 tab 用 `document.querySelectorAll('#tabs .tab')[i].click()`，每轮等 1.5~3s（同步/淡入/碎屑时长）。
- **动画结束后必须不可见**：一次性动效（流光、爆发）依赖"动画结束回基础态"隐藏——基础态 CSS 必须是 `opacity:0`/transform 归位，且动画不要 fill forwards 到可见态；class 要加在选择器匹配的元素上（如 `.ring.sheen-on .sheen-g` 要求 class 加在 .ring 而非 g 本身，加错则淡入淡出整个不生效，g 常驻 opacity:1 光条永不消失）。采样时动画前/中/后三点都要采 opacity 断言 0→1→0。
- **避免视觉台阶**：多段拼接的尾迹/渐变，段数要 ≥10 且透明度连续（幂函数渐增）、段间 butt 相接、仅头段 round；3 段固定透明度会出现明显三档台阶。
- **高光动效不要实压底色**：叠在既有 UI（进度环/条）上的白色高光，峰值透明度要与同类效果同档（横条流光用 .75，环彗星用 .6），近纯白(.95)+强 drop-shadow 会盖住底色、在元素起止处尤其难看；采样时加拍动效末段（头部到达起止位置）截图确认。
- evaluate_js 脚本里**访问元素属性前先判空**（`const r=el.querySelector(...); r ? r.style.transform : null`），null 上读属性抛 TypeError 会让整轮采样失败。
- 测试环境 localStorage 每次全新：`celebrated:xxx` 标记不存在，首次切到完成视频会爆发 12 片纸屑，属预期。

## 截图判读

PrintWindow 截到的是动画某一帧，模糊/半透明拖尾正是动效进行中的证据；配合采样数值（offset 单调变化、opacity 淡入淡出）一起判断，不要只看单张截图。

## 收尾

- 验证通过后再 `py -3 build.py` 打包（应用运行中会打包失败，提示先关闭）。
- 动效实现细节/踩坑同步写进 `AGENTS.md` 的"其他动效"段落。
- 临时脚本和截图留在 `%TEMP%`，不进仓库、不提交。

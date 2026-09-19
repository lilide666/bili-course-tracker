# -*- coding: utf-8 -*-
"""
本地代理服务：托管界面 + 实时读取B站观看进度（多视频追踪）。

【AI 助手注意】开发前必读项目根目录 AGENTS.md（架构约定、隐私红线、踩坑记录）；
改动架构/约定后必须同步更新 AGENTS.md，它是随仓库转移的项目记忆。

用法：
  1. 在应用内设置面板粘贴 SESSDATA（加密存于 .sessdata.bin；或设环境变量 BILI_SESSDATA）
  2. py -3 server.py
  3. 浏览器打开 http://127.0.0.1:8765/

说明：
  - 仅绑定 127.0.0.1，不对外暴露。
  - SESSDATA 只在本进程内存中用于请求B站API，不会写入日志、不会回显。
  - SESSDATA 存于系统密钥环（keyring，跨平台）；.sessdata* 与隐藏文件被静态服务拒绝访问。
  - 追踪视频列表与跳跃记录持久化到 tracked_videos.json，无网络也能加载。
"""
import base64
import hashlib
import http.client
import http.cookiejar
import http.server
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

HOST = "127.0.0.1"
PORT = 8765
# 数据目录（tracked_videos.json / course_data.json / covers / .sessdata.bin）：
# 打包后用 exe 所在目录，保证数据持久化且与 exe 一起移动；
# 源码运行时用项目内的应用目录，与打包版共用同一份数据。
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "B站课程进度追踪"))
# 系统级安装（Linux .deb 装到 /opt）普通用户对 exe 目录无写权限：
# 运行时数据回退到用户 XDG 数据目录（$XDG_DATA_HOME/bili-course-tracker）
if getattr(sys, "frozen", False) and sys.platform.startswith("linux") \
        and not os.access(ROOT, os.W_OK):
    _xdg_data = os.environ.get("XDG_DATA_HOME", "").strip() or \
        os.path.join(os.path.expanduser("~"), ".local", "share")
    ROOT = os.path.join(_xdg_data, "bili-course-tracker")
# 外部入口可显式指定数据目录（Linux 原生窗口 app_linux_native.py 用，
# /opt 下源码运行不是 frozen，前面的 XDG 回退不生效，只能走环境变量）
_env_root = os.environ.get("BILI_TRACKER_ROOT", "").strip()
if _env_root:
    ROOT = os.path.abspath(os.path.expanduser(_env_root))
# 前端资源目录：打包后 index.html 等内嵌进 _internal（PyInstaller 会把 _MEIPASS 指向它）
if getattr(sys, "frozen", False):
    FRONTEND_DIR = sys._MEIPASS
else:
    FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKED_FILE = os.path.join(ROOT, "tracked_videos.json")
FOCUS_FILE = os.path.join(ROOT, "focus.json")  # 番茄钟专注统计（按天累计，只存数字）
FOCUS_SESSION_FILE = os.path.join(ROOT, ".focus_session.json")  # 番茄钟运行态（开始/暂停/心跳持久化，防退出丢失；点开头 HTTP 404）
COURSE_FILE = os.path.join(ROOT, "course_data.json")
COVER_CACHE_DIR = os.path.join(ROOT, "covers")  # 封面图本地缓存目录

_IMG_TYPE = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
             ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".py": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

# 写操作锁，避免并发请求互相覆盖 tracked_videos.json
_wlock = threading.Lock()


def fmt_sec(sec):
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _icon_ico_to_png(ico_path, size=256):
    """ico 转 PNG（字节）；PIL 不可用或失败返回 None。"""
    try:
        import io
        from PIL import Image
        img = Image.open(ico_path).convert("RGBA")
        if img.size != (size, size):
            img = img.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# ===================== SESSDATA 加密存储（跨平台 keyring） =====================
# 凭据不落盘明文：优先用 keyring 库调系统密钥环（Windows DPAPI / macOS Keychain /
# Linux Secret Service/libsecret）；keyring 不可用时回退到文件存储（chmod 600 仅当前用户可读）。
# 旧版 Windows DPAPI 的 .sessdata.bin 与旧明文 .sessdata.txt 在读取时自动迁移。
SESSDATA_BIN = os.path.join(ROOT, ".sessdata.bin")  # 旧 DPAPI 文件 / keyring 回退文件
KEYRING_SERVICE = "bili-tracker"
KEYRING_USER = "sessdata"


def _sessdata_txt_path():
    return os.path.join(ROOT, ".sessdata.txt")


def _kr_available():
    """检测 keyring 是否可用（已装库 + 有可用后端）。"""
    try:
        import keyring
        # get_keyring() 在无后端时抛 NoKeyringError，不抛即可用
        keyring.get_keyring()
        return True
    except Exception:
        return False


def _kr_set(value):
    c = _get_kr_persistent()
    if c is not None:
        c.set_password(KEYRING_SERVICE, KEYRING_USER, value)
    else:
        import keyring
        keyring.set_password(KEYRING_SERVICE, KEYRING_USER, value)


def _kr_get():
    c = _get_kr_persistent()
    if c is not None:
        return c.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
    import keyring
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""


def _kr_delete():
    c = _get_kr_persistent()
    try:
        if c is not None:
            c.delete_password(KEYRING_SERVICE, KEYRING_USER)
        else:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        pass


# ===== 持久 keyring 客户端（防 gnome-keyring 崩溃）=====
# 坑：keyring SecretService 后端每次 get_password 都新建 D-Bus 连接，返回后
# 立即销毁。gnome-keyring 在异步读取属性途中遇到客户端断开会触发 GIO 断言
# (gdbusconnection.c invoke_get_property_in_idle_cb: error != NULL) 直接 SIGABRT。
# 修法：进程内复用同一个 SecretService 客户端，连接常驻不关闭。
_kr_persistent = None        # SecretService Keyring 实例；False=创建失败/不可用
_kr_persistent_lock = threading.Lock()


def _get_kr_persistent():
    global _kr_persistent
    if _kr_persistent is not None:
        return _kr_persistent or None
    with _kr_persistent_lock:
        if _kr_persistent is None:
            try:
                from keyring.backends.SecretService import Keyring as _SSKeyring
                c = _SSKeyring()
                # 首次探测：确认后端真正可用（无 D-Bus/collection 会抛异常）
                c.get_password(KEYRING_SERVICE, KEYRING_USER)
                _kr_persistent = c
            except Exception:
                _kr_persistent = False
    return _kr_persistent or None


# 凭据值进程内缓存：get_sessdata 首次读取后缓存，避免每个 HTTP 请求都联系 keyring。
# None=未读取；""/str=已读取的值。write/delete 时同步维护。
_sessdata_cache = None
_sessdata_cache_lock = threading.Lock()


def _clean_sessdata(v):
    if v.startswith("SESSDATA="):
        v = v[len("SESSDATA="):]
    return v.rstrip(";").strip().strip('"').strip("'")


def _file_set(value):
    """keyring 不可用时的回退：明文写文件，chmod 600 仅当前用户可读。"""
    tmp = SESSDATA_BIN + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(value)
    os.replace(tmp, SESSDATA_BIN)
    try:
        os.chmod(SESSDATA_BIN, 0o600)
    except OSError:
        pass


def _file_get():
    if not os.path.exists(SESSDATA_BIN):
        return ""
    with open(SESSDATA_BIN, "r", encoding="utf-8") as f:
        return f.read().strip()


def _store_set(value):
    if _kr_available():
        try:
            _kr_set(value)
            return
        except Exception:
            pass
    _file_set(value)


def _store_get():
    if _kr_available():
        try:
            return _kr_get()
        except Exception:
            # keyring 后端运行时不可用（如 Linux 无 D-Bus/Secret Service），回退明文
            return _file_get()
    return _file_get()


def _store_delete():
    if _kr_available():
        _kr_delete()
    if os.path.exists(SESSDATA_BIN):
        try:
            os.remove(SESSDATA_BIN)
        except OSError:
            pass


def _dpapi_unprotect(blob: bytes) -> bytes:
    """旧版 Windows DPAPI 解密，仅用于迁移旧 .sessdata.bin。"""
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    bin_ = BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob),
                                       ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(bin_), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("CryptUnprotectData 失败（可能换了 Windows 账户）")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _migrate_old():
    """从旧 .sessdata.bin（DPAPI）或旧明文 .sessdata.txt 迁移到新存储。"""
    # 优先 keyring 已有值，不迁移
    if _store_get():
        return
    v = ""
    if os.path.exists(SESSDATA_BIN):
        try:
            with open(SESSDATA_BIN, "rb") as f:
                raw = base64.b64decode(f.read().strip())
            v = _dpapi_unprotect(raw).decode("utf-8", "ignore").strip()
            if v:
                _store_set(v)
                print("[凭据] 已将旧 DPAPI 的 .sessdata.bin 迁移到系统密钥环", flush=True)
        except Exception as e:
            print(f"[凭据] 读取旧 .sessdata.bin 失败（可能非 Windows 或换了账户）：{e}", flush=True)
    if not v and os.path.exists(_sessdata_txt_path()):
        try:
            with open(_sessdata_txt_path(), "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        v = line
                        break
            if v:
                _store_set(v)
                print("[凭据] 已将明文 SESSDATA 迁移到系统密钥环，并删除明文文件", flush=True)
        except Exception as e:
            print(f"[凭据] 旧明文迁移失败：{e}", flush=True)


def get_sessdata():
    """优先环境变量 BILI_SESSDATA；其次系统密钥环（keyring）；
    兼容旧 DPAPI 的 .sessdata.bin 与旧明文 .sessdata.txt：读到后自动迁移。
    首次读取后进程内缓存，避免每个请求都新建 keyring D-Bus 连接。"""
    global _sessdata_cache
    s = os.environ.get("BILI_SESSDATA", "").strip()
    if s:
        return _clean_sessdata(s)
    with _sessdata_cache_lock:
        if _sessdata_cache is None:
            _migrate_old()
            _sessdata_cache = _clean_sessdata(_store_get())
        return _sessdata_cache


def write_sessdata(value):
    """清洗并写入系统密钥环。清洗规则与 get_sessdata() 一致。"""
    global _sessdata_cache
    v = _clean_sessdata((value or "").strip())
    if not v:
        return False
    _store_set(v)
    with _sessdata_cache_lock:
        _sessdata_cache = v
    # keyring 可用时，旧 .sessdata.bin（DPAPI）和 .sessdata.txt 都可以删；
    # keyring 不可用时 .sessdata.bin 就是回退存储本身，不能删，只删 .sessdata.txt。
    old_files = [_sessdata_txt_path()]
    if _kr_available():
        old_files.append(SESSDATA_BIN)
    for p in old_files:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    return True


def delete_sessdata():
    """删除密钥环中的 SESSDATA（及旧 .sessdata.bin / .sessdata.txt）。登出时调用。"""
    global _sessdata_cache
    _store_delete()
    with _sessdata_cache_lock:
        _sessdata_cache = ""
    for p in (_sessdata_txt_path(),):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    return True


# 全局强制B站相关域名只走 IPv4（用户网络 IPv6 解析会让 Python 挂起几十秒）。
# 模块级一次性打补丁、永不恢复，避免多线程并发请求时的竞态。
# 覆盖：API 域名 + 图片/视频 CDN 域名（hdslb.com / bilivideo.com / bilibili.com）。
_orig_getaddrinfo = socket.getaddrinfo


def _is_bili_host(host):
    if not host:
        return False
    h = host.lower()
    return (h == "api.bilibili.com" or h.endswith(".hdslb.com")
            or h.endswith(".bilivideo.com") or h.endswith(".biliapi.net")
            or h.endswith(".bilibili.com"))


def _bili_ipv4_getaddrinfo(host, port, *a, **k):
    if _is_bili_host(host):
        return _orig_getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    return _orig_getaddrinfo(host, port, *a, **k)


socket.getaddrinfo = _bili_ipv4_getaddrinfo


# ===================== HTTPS 连接复用 =====================
# 每线程每域名一条 HTTPS 长连接：省去每次请求的 TCP+TLS 握手，
# 单次 API 请求从 ~300-500ms 降到 ~100ms。线程内串行使用，线程安全。
_tls_ctx = ssl.create_default_context()
_conn_local = threading.local()


def _get_conn(host):
    pool = getattr(_conn_local, "pool", None)
    if pool is None:
        pool = _conn_local.pool = {}
    conn = pool.get(host)
    if conn is None:
        conn = http.client.HTTPSConnection(host, timeout=15, context=_tls_ctx)
        pool[host] = conn
    return conn


def _drop_conn(host):
    """连接被服务端关闭/出错时丢弃重建。"""
    pool = getattr(_conn_local, "pool", None)
    if pool and host in pool:
        try:
            pool[host].close()
        except Exception:
            pass
        del pool[host]


def _https_get(url, sessdata=None):
    """HTTPS GET，返回 (body_bytes, content_type, err)。连接复用，自动重试一次。"""
    u = urllib.parse.urlparse(url)
    host = u.hostname
    path = u.path + (("?" + u.query) if u.query else "")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Host": host,
    }
    if sessdata is not None:
        headers["Cookie"] = "SESSDATA=" + sessdata
        headers["Referer"] = "https://www.bilibili.com/"
        headers["Origin"] = "https://www.bilibili.com"
    else:
        headers["Referer"] = "https://www.bilibili.com/"
    last_err = None
    for _ in range(2):  # 长连接可能已被服务端关闭，丢弃重建再试一次
        try:
            conn = _get_conn(host)
            conn.request("GET", path, headers=headers)
            r = conn.getresponse()
            body = r.read()
            return body, r.headers.get("Content-Type", ""), None
        except (http.client.HTTPException, OSError) as e:
            last_err = e
            _drop_conn(host)
    return None, None, last_err


def bilibili_get(url, sessdata):
    """请求B站 JSON API（连接复用 + 全局 IPv4）。带浏览器风格请求头。"""
    body, _ct, err = _https_get(url, sessdata=sessdata)
    if err is not None:
        raise err
    return json.loads(body.decode("utf-8"))


def bilibili_get_bounded(url, sessdata, timeout=15):
    """带硬性总超时的请求：工作线程 + join，保证最多 timeout 秒一定返回。"""
    box = {}

    def worker():
        try:
            box["data"] = bilibili_get(url, sessdata)
        except BaseException as e:  # noqa
            box["err"] = e

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return None, TimeoutError(f"请求B站超过 {timeout} 秒无响应（已放弃）")
    if "err" in box:
        return None, box["err"]
    return box.get("data"), None


# ===================== WBI 签名（B站合集等接口需要） =====================
# B站部分接口（如合集信息 /x/web-interface/wbi/season/info）需要 wbi 签名。
# 流程：从 nav 接口取 img_url/sub_url 提取 key → 固定重排表混出 mixin_key →
# 对请求参数（加 wts 时间戳）排序拼接后 md5 得 w_rid。
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]
_wbi_keys = {"img": None, "sub": None, "ts": 0}
_wbi_lock = threading.Lock()


def _get_wbi_keys(sessdata):
    """获取并缓存 wbi 的 img_key/sub_key（缓存 1 小时）。"""
    now = time.time()
    with _wbi_lock:
        if _wbi_keys["img"] and _wbi_keys["sub"] and now - _wbi_keys["ts"] < 3600:
            return _wbi_keys["img"], _wbi_keys["sub"]
    nav, err = bilibili_get_bounded(
        "https://api.bilibili.com/x/web-interface/nav", sessdata, timeout=10)
    if err is not None or not nav:
        return None, None
    wbi_img = ((nav.get("data") or {}).get("wbi_img")) or {}
    img_url = wbi_img.get("img_url") or ""
    sub_url = wbi_img.get("sub_url") or ""
    if not img_url or not sub_url:
        return None, None
    img_key = os.path.basename(img_url).split(".")[0]
    sub_key = os.path.basename(sub_url).split(".")[0]
    with _wbi_lock:
        _wbi_keys.update({"img": img_key, "sub": sub_key, "ts": now})
    return img_key, sub_key


def _mixin_key(img_key, sub_key):
    """按 B站固定重排表从 img+sub 中取前 32 位字符作为签名密钥。"""
    orig = img_key + sub_key
    return "".join(orig[i] for i in _MIXIN_KEY_ENC_TAB)[:32]


def wbi_sign(params, sessdata):
    """对参数做 wbi 签名，返回带 wts/w_rid 的新参数字典；失败返回 None。"""
    img_key, sub_key = _get_wbi_keys(sessdata)
    if not img_key:
        return None
    mixin = _mixin_key(img_key, sub_key)
    p = dict(params)
    p["wts"] = int(time.time())
    p = dict(sorted(p.items()))
    p = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in p.items()}
    query = urllib.parse.urlencode(p)
    p["w_rid"] = hashlib.md5((query + mixin).encode("utf-8")).hexdigest()
    return p


# ===================== 扫码登录（B站官方 WEB 二维码通道） =====================
# 流程：start 申请二维码内容（B站同时 Set-Cookie buvid3 等，用 cookiejar 保持）→
# 前端渲染二维码、轮询 poll；手机 App 扫码确认后 poll 的 data.url 带 SESSDATA，
# 复用 write_sessdata() 走系统密钥环落盘——与手动粘贴殊途同归，凭据不回传前端、不落日志。
# 走 WEB 通道（非 TV 通道）：拿到的是网页端会话，不占 TV/手机端登录设备名额。
# qrcode_key 只存进程内存（180 秒有效），进程退出即消失。
_QR_BASE = "https://passport.bilibili.com/x/passport-login/web/qrcode"
_QR_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_qr_lock = threading.Lock()
_qr_state = {}   # {key, opener, created_at}


def _qr_request(opener, url):
    """扫码通道专用请求：独立 opener + cookiejar，浏览器风格头（登录与后续接口同一 UA 画像）。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": _QR_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://passport.bilibili.com/login?",
    })
    with opener.open(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _make_qr_matrix(url):
    """用 qrcode 库生成矩阵（含 4 模块静区），返回 [[0/1,...],...]。编码正确性由成熟库保证。"""
    import qrcode
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=0)
    qr.add_data(url)
    qr.make(fit=True)
    n = len(qr.modules)
    # 手动加 4 模块静区（全 0）
    q = 4
    total = n + q * 2
    mat = [[0] * total for _ in range(total)]
    for r in range(n):
        row = qr.modules[r]
        for c in range(n):
            if row[c]:
                mat[r + q][c + q] = 1
    return mat


def qrlogin_start():
    """申请二维码。返回 ({"url": 二维码内容, "matrix": [[0/1,...],...]}, None) 或 (None, 错误)。"""
    try:
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_tls_ctx),
            urllib.request.HTTPCookieProcessor(jar))
        j = _qr_request(opener, _QR_BASE + "/generate?source=main-fe-header")
        d = j.get("data") or {}
        url, key = d.get("url"), d.get("qrcode_key")
        if j.get("code") != 0 or not url or not key:
            return None, "B站二维码接口返回异常：%s" % (j.get("message") or j.get("code"))
        with _qr_lock:
            _qr_state.clear()
            _qr_state.update(key=key, opener=opener, jar=jar, created_at=time.time())
        return {"url": url, "matrix": _make_qr_matrix(url)}, None
    except Exception as e:
        return None, _friendly_err(e)


def qrlogin_poll():
    """轮询扫码状态。返回 ({"status": wait|scanned|expired|success}, None) 或 (None, 错误)。"""
    with _qr_lock:
        st = dict(_qr_state)
    if not st.get("key"):
        return None, "no-session"   # 前端据此重新 start
    try:
        j = _qr_request(st["opener"],
                        _QR_BASE + "/poll?qrcode_key=" + urllib.parse.quote(st["key"]) +
                        "&source=main-fe-header")
    except Exception as e:
        return None, _friendly_err(e)
    d = j.get("data") or {}
    code = d.get("code")
    status_map = {86101: "wait", 86090: "scanned", 86038: "expired"}
    if code in status_map:
        if code == 86038:
            with _qr_lock:
                _qr_state.clear()
        return {"status": status_map[code]}, None
    if code == 0:
        with _qr_lock:
            jar_ref = st.get("jar")
            _qr_state.clear()
        # SESSDATA 可能来自两处：
        # 1) data.url query 参数（旧版通道）
        # 2) cookie jar（新版通道，B 站通过 Set-Cookie 下发）
        q = urllib.parse.parse_qs(urllib.parse.urlparse(d.get("url") or "").query)
        sd = (q.get("SESSDATA") or [None])[0]
        if not sd and jar_ref:
            for c in jar_ref:
                if c.name == "SESSDATA" and c.domain.endswith("bilibili.com"):
                    sd = c.value; break
        if not sd:
            print("[登录] B站返回 success 但未取得 SESSDATA，url=", d.get("url"),
                  "refresh_token=", d.get("refresh_token"), flush=True)
            return None, "登录成功但未取得 SESSDATA"
        with _wlock:
            ok = write_sessdata(sd)
        if not ok:
            return None, "SESSDATA 写入失败"
        print("[配置] SESSDATA 已通过扫码登录保存", flush=True)
        return {"status": "success"}, None
    return None, "B站返回未知状态：%s" % code


# ===================== 本地存储 =====================

# ===================== 隐私：本地数据最小化 =====================
# 磁盘上的 tracked_videos.json 只保留"数字型"字段（bvid/集数/秒数/时间戳），
# 课程标题、UP主、封面、分P列表等描述性信息只存内存（_meta_cache），
# 每次启动实时从B站拉取——即使文件被误上传，也看不出用户在学什么。
_DISK_KEYS = ("bvid", "totalEpisodes", "totalDuration", "lastProgress", "lastSynced", "added_at", "is_season", "season_id")
_meta_cache = {}          # bvid -> {url,title,owner,cover,episodes,totalDurationText,is_season}
_meta_lock = threading.Lock()


def _strip_to_disk(video):
    """只保留数字型/标识型字段用于落盘（标题、封面、集数标题等描述性信息不落盘）。"""
    if not isinstance(video, dict):
        return {}
    return {k: video[k] for k in _DISK_KEYS if video.get(k) is not None}


def _cache_meta(video):
    """把从B站拉到的完整信息放进内存缓存（不落盘）。"""
    if not video or not video.get("bvid"):
        return
    with _meta_lock:
        _meta_cache[video["bvid"]] = {
            "url": video.get("url"),
            "title": video.get("title"),
            "owner": video.get("owner"),
            "cover": video.get("cover"),
            "episodes": video.get("episodes"),
            "totalDurationText": video.get("totalDurationText"),
            "is_season": video.get("is_season"),
        }


def _enrich_video(v, sessdata=None):
    """给磁盘记录合并内存元数据；缺失且有 SESSDATA 时实时从B站拉取。
    合集条目（is_season）拉取合集视频列表而非单个视频信息。"""
    bvid = v.get("bvid")
    with _meta_lock:
        meta = _meta_cache.get(bvid)
    if meta is None and sessdata:
        try:
            if v.get("is_season") and v.get("season_id"):
                info, _err = fetch_season_archives(v["season_id"], sessdata, ref_bvid=bvid)
            else:
                info, _err = fetch_video_info(bvid, sessdata)
        except Exception:
            info = None
        if info is not None:
            _cache_meta(info)
            v["totalEpisodes"] = info.get("totalEpisodes", v.get("totalEpisodes"))
            v["totalDuration"] = info.get("totalDuration", v.get("totalDuration"))
            with _meta_lock:
                meta = _meta_cache.get(bvid)
    m = meta or {}
    out = dict(v)
    out["title"] = m.get("title")
    out["owner"] = m.get("owner")
    out["cover"] = m.get("cover")
    out["episodes"] = m.get("episodes")
    out["is_season"] = v.get("is_season") or m.get("is_season")
    try:
        out["totalDurationText"] = m.get("totalDurationText") or fmt_sec(int(v.get("totalDuration") or 0))
    except (TypeError, ValueError):
        out["totalDurationText"] = m.get("totalDurationText") or "00:00:00"
    return out


def default_tracked():
    return {"videos": [], "jumps": [], "active_bvid": None}


def load_tracked():
    """读 tracked_videos.json；文件不存在或损坏返回默认结构。用 utf-8-sig 容忍 BOM。
    读取时即做"脱敏归一化"：旧文件里的标题/UP主/封面/分P列表等描述性字段会被剥离，
    下次保存后磁盘上只剩数字型字段。"""
    if not os.path.exists(TRACKED_FILE):
        return default_tracked()
    try:
        with open(TRACKED_FILE, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return default_tracked()
    if not isinstance(data, dict):
        return default_tracked()
    data.setdefault("videos", [])
    data["videos"] = [_strip_to_disk(v) for v in (data.get("videos") or [])
                      if isinstance(v, dict) and v.get("bvid")]
    # 跳跃记录去重：相同（视频/类型/起终点）的跳跃只保留最早一条，
    # 清理旧版本因基准错误反复误报堆积的重复回看。
    seen = set()
    deduped = []
    for j in (data.get("jumps") or []):
        if not isinstance(j, dict):
            continue
        # 丢弃旧版本把"看完(progress=-1)"误判成回看的脏数据
        if j.get("kind") == "time" and (j.get("to_progress") or 0) < 0:
            continue
        sig = (j.get("bvid"), j.get("kind"), j.get("from_page"), j.get("to_page"),
               j.get("from_progress"), j.get("to_progress"))
        if sig in seen:
            continue
        seen.add(sig)
        j.pop("title", None)  # 旧版本曾把课程标题落盘到 jumps，统一剥离（标题实时从B站取）
        deduped.append(j)
    data["jumps"] = deduped[-100:]
    data.setdefault("active_bvid", None)
    return data


def save_tracked(data):
    """原子写：先写临时文件再 os.replace。写入前脱敏——磁盘只存数字型字段。"""
    data = dict(data)
    data["videos"] = [_strip_to_disk(v) for v in (data.get("videos") or [])]
    tmp = TRACKED_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TRACKED_FILE)


def _find_video(data, bvid):
    """在 data['videos'] 中按 bvid 查找，返回 (index, video) 或 (None, None)。"""
    for i, v in enumerate(data.get("videos") or []):
        if v.get("bvid") == bvid:
            return i, v
    return None, None


# ===================== 番茄钟专注统计（数据最小化：只存日期+秒数） =====================

def load_focus():
    """读今日专注秒数；跨天（date 不匹配）或文件损坏返回 0。"""
    try:
        with open(FOCUS_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("date") == time.strftime("%Y-%m-%d"):
            return max(0, int(d.get("seconds") or 0))
    except Exception:
        pass
    return 0


def add_focus(seconds):
    """把本轮专注秒数累加到今天；跨天自动清零重计。返回今日累计。"""
    today = time.strftime("%Y-%m-%d")
    seconds = max(0, int(seconds))
    with _wlock:
        try:
            with open(FOCUS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            cur = max(0, int(d.get("seconds") or 0)) if (
                isinstance(d, dict) and d.get("date") == today) else 0
        except Exception:
            cur = 0
        tmp = FOCUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"date": today, "seconds": cur + seconds}, f)
        os.replace(tmp, FOCUS_FILE)
    return cur + seconds


# ---------- 番茄钟运行态持久化（关闭/崩溃/断电不丢时间，服务端时钟为权威） ----------
# 会话模型与前端 base/startedAt 一致：state=running 时秒数=base+(now-started_at)；
# paused 时秒数=base（已固化）。心跳 updated_at 是异常退出后封口的时间边界。

FOCUS_HEARTBEAT_GRACE = 60  # 崩溃恢复：最后心跳后最多再认 60 秒（心跳间隔 15s，崩溃最多损失约 15s）


def _load_focus_session():
    try:
        with open(FOCUS_SESSION_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and d.get("state") in ("running", "paused"):
            return d
    except Exception:
        pass
    return None


def _save_focus_session(d):
    tmp = FOCUS_SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, FOCUS_SESSION_FILE)


def _clear_focus_session():
    for p in (FOCUS_SESSION_FILE, FOCUS_SESSION_FILE + ".tmp"):
        try:
            os.remove(p)
        except OSError:
            pass


def _settle_focus_seconds(d, now):
    """按会话状态算本轮秒数；running 以 now 封口（调用方负责崩溃恢复时钳制 now）。"""
    secs = max(0, int(d.get("base") or 0))
    if d.get("state") == "running":
        start = float(d.get("started_at") or now)
        secs += max(0, int(now - start))
    return secs


def start_focus_session():
    """开始一轮：新建运行态会话。返回服务端当前时间戳（前端据此对齐时钟）。"""
    now = int(time.time())
    d = {"state": "running", "base": 0, "started_at": now, "updated_at": now}
    with _wlock:
        _save_focus_session(d)
    return now


def pause_focus_session():
    """暂停：把当前运行段折算进 base 并固化。"""
    with _wlock:
        d = _load_focus_session()
        if d and d.get("state") == "running":
            now = time.time()
            d["base"] = max(0, int(d.get("base") or 0)) + max(
                0, int(now - float(d["started_at"])))
            d["state"] = "paused"
            _save_focus_session(d)
            return True
    return False


def resume_focus_session():
    """继续：重开运行段。返回服务端时间戳。"""
    with _wlock:
        d = _load_focus_session()
        if d and d.get("state") == "paused":
            now = int(time.time())
            d["state"] = "running"
            d["started_at"] = now
            d["updated_at"] = now
            _save_focus_session(d)
            return now
    return None


def heartbeat_focus_session():
    """心跳：仅更新 updated_at，作为崩溃恢复的封口边界。"""
    with _wlock:
        d = _load_focus_session()
        if d and d.get("state") == "running":
            d["updated_at"] = int(time.time())
            _save_focus_session(d)
            return True
    return False


def stop_focus_session():
    """正常结束（含关闭软件时自动结算）：摘走会话→结算入账→删除。
    返回 (今日累计, 本轮入账秒数)。摘走在锁内原子完成，重复/并发请求不会重复入账。"""
    with _wlock:
        d = _load_focus_session()
        if not d:
            return load_focus(), 0
        credited = _settle_focus_seconds(d, time.time())
        _clear_focus_session()
    if credited > 0:  # 任何正数时长都入账（2026-09 取消"不足5秒不记录"门槛）
        total = add_focus(credited)
    else:
        total = load_focus()
    return total, credited


def recover_focus_session():
    """启动时恢复异常退出残留的会话（kill/崩溃/断电/关机）：
    running 封口不晚于最后心跳+宽限，避免几天后才打开却凭空计入数天；
    paused 按已固化的 base 结算。返回恢复入账的秒数。"""
    with _wlock:
        d = _load_focus_session()
        if not d:
            return 0
        now = time.time()
        if d.get("state") == "running":
            cap = float(d.get("updated_at") or d.get("started_at") or now) + FOCUS_HEARTBEAT_GRACE
            if now > cap:
                now = cap
        credited = _settle_focus_seconds(d, now)
        _clear_focus_session()
    if credited > 0:
        add_focus(credited)
    return credited


def _video_summary(v):
    """前端 tab 列表用的精简结构（不含 episodes）。v 应先经 _enrich_video 合并元数据。"""
    return {
        "bvid": v.get("bvid"),
        "title": v.get("title"),
        "owner": v.get("owner"),
        "cover": v.get("cover"),
        "totalEpisodes": v.get("totalEpisodes"),
        "totalDurationText": v.get("totalDurationText"),
        "lastProgress": v.get("lastProgress"),
        "added_at": v.get("added_at"),
    }


def migrate_from_course_data():
    """首次启动：若 tracked_videos.json 不存在但 course_data.json 存在，迁移为单视频。"""
    if os.path.exists(TRACKED_FILE):
        return False
    if not os.path.exists(COURSE_FILE):
        return False
    try:
        with open(COURSE_FILE, "r", encoding="utf-8-sig") as f:
            course = json.load(f)
    except Exception as e:
        print(f"[迁移] 读取 course_data.json 失败：{e}", flush=True)
        return False
    bvid = course.get("bvid")
    if not bvid:
        return False
    video = {
        "bvid": bvid,
        "url": course.get("url", f"https://www.bilibili.com/video/{bvid}"),
        "title": course.get("title", ""),
        "owner": course.get("owner", ""),
        "cover": course.get("cover", ""),
        "totalEpisodes": course.get("totalEpisodes", len(course.get("episodes", []))),
        "totalDuration": course.get("totalDuration", 0),
        "totalDurationText": course.get("totalDurationText", "00:00:00"),
        "episodes": course.get("episodes", []),
        "lastProgress": None,
        "added_at": int(time.time()),
    }
    data = {"videos": [video], "jumps": [], "active_bvid": bvid}
    _cache_meta(video)   # 描述性信息进内存缓存，磁盘只落数字型字段
    save_tracked(data)
    print(f"[迁移] 已从 course_data.json 迁移 1 个视频：{bvid}", flush=True)
    return True


# ===================== B站 API =====================

def _match_item(item, bvid):
    """命中指定 bvid 则返回结果 dict，否则 None。bvid 可能在顶层或 history 内。"""
    hist = item.get("history") or {}
    if item.get("bvid") != bvid and hist.get("bvid") != bvid:
        return None
    page = hist.get("page")
    cid = hist.get("cid")
    return {
        "ok": True,
        "page": page,
        "cid": str(cid) if cid else None,
        "progress": item.get("progress", 0) or 0,
        "view_at": item.get("view_at"),
        "title": item.get("title") or hist.get("title"),
    }


def _friendly_err(err):
    """把底层异常翻译成用户能懂的话。"""
    s = f"{type(err).__name__}: {err}"
    if isinstance(err, socket.gaierror) or "getaddrinfo failed" in s:
        return "域名解析失败：请检查电脑是否已联网"
    if isinstance(err, (socket.timeout, TimeoutError)) or "timed out" in s:
        return "网络请求超时：请检查电脑是否已联网，或稍后重试"
    if "SSLError" in s or "CERTIFICATE" in s.upper():
        return "HTTPS 证书校验失败：" + s
    return "请求B站失败：" + s


# ===================== 统一状态码（供前端状态模块派生提示） =====================
# kind 取值：
#   network  网络不通/超时/证书（可重试，可回退本地缓存）
#   sessdata SESSDATA 未配置或失效（needSessdata=True，引导去设置）
#   notfound B站历史里扫不到该视频（不是错误，近期无观看记录）
#   bili     B站返回其他业务错误
def _classify_err(err):
    s = f"{type(err).__name__}: {err}"
    if "SESSDATA" in s or "未登录" in s:
        return "sessdata"
    if (isinstance(err, (socket.gaierror, socket.timeout, TimeoutError, ssl.SSLError))
            or "getaddrinfo failed" in s or "timed out" in s
            or "SSLError" in s or "CERTIFICATE" in s.upper()
            or "Connection" in s or "Remote end" in s):
        return "network"
    return "bili"


def _kind_from_msg(msg):
    msg = msg or ""
    if "SESSDATA" in msg or "未登录" in msg:
        return "sessdata"
    if "B站返回" in msg:
        return "bili"
    return "network"


def _err_result(kind, message, **extra):
    """统一错误响应：ok=False + kind（机器可读）+ message（人可读）。"""
    res = {"ok": False, "kind": kind, "message": message}
    if kind == "sessdata":
        res["needSessdata"] = True
    res.update(extra)
    return res


def _fetch_cursor_pages(sessdata, max_pages=10, stop_bvid=None):
    """分页拉取历史 cursor，返回 (items_list, error_or_None)。

    翻页协议（实测 2026-09）：
      - 响应游标在 data.cursor.view_at（旧版 data.page 已下线，max 参数已失效）
      - 下一页请求参数 view_at=<上一页 cursor.view_at>（秒级时间戳）
    stop_bvid 给定时，某页出现该视频即停止（首条命中就是该视频最新的观看记录），
    通常 1 页（30 条）即可返回。
    """
    base = "https://api.bilibili.com/x/web-interface/history/cursor"
    items = []
    vat = None
    for page_i in range(max_pages):
        params = {"ps": "30", "type": "archive"}
        if vat:
            params["view_at"] = str(vat)
        url = base + "?" + urllib.parse.urlencode(params)
        data, err = bilibili_get_bounded(url, sessdata, timeout=20)
        if err is not None:
            return items, err
        code = data.get("code")
        if code == -101:
            return items, RuntimeError("SESSDATA 已失效或未登录：请在右上角 ⚙ 设置中重新粘贴新的 SESSDATA")
        if code != 0:
            return items, RuntimeError(f"B站返回 code={code} message={data.get('message')!r}")
        d = data.get("data") or {}
        lst = d.get("list") or []
        items.extend(lst)
        if stop_bvid and any(
                (it.get("bvid") == stop_bvid or (it.get("history") or {}).get("bvid") == stop_bvid)
                for it in items):
            break  # 命中即停
        if not lst:
            break
        cur = d.get("cursor") or {}
        nxt = cur.get("view_at") or min((it.get("view_at") or 0) for it in lst)
        if not nxt or (vat is not None and nxt >= vat):
            break  # 无进展（重复页）则停，防死循环
        vat = nxt
    return items, None


def fetch_progress(sessdata, bvid, episode_bvids=None):
    """读取B站历史(cursor接口)，查找指定视频的进度。
    bvid: 追踪条目标识 bvid；episode_bvids: 合集时传入所有集的 bvid 列表（多 bvid 搜索）。"""
    bvid_set = set(episode_bvids) if episode_bvids else {bvid}
    print("=" * 48, flush=True)
    print(f"[进度] 开始读取B站历史 (cursor) 目标={bvid} 集数={len(bvid_set)}", flush=True)
    t0 = time.time()
    # 合集多 bvid 时不设 stop_bvid（需扫完整页找所有命中），单 bvid 命中即停
    items, err = _fetch_cursor_pages(sessdata, max_pages=10,
                                     stop_bvid=(None if episode_bvids else bvid))
    if err is not None:
        kind = _classify_err(err)
        msg = str(err) if kind == "sessdata" else _friendly_err(err)
        print("[进度] " + msg, flush=True)
        return _err_result(kind, msg)

    scanned = len(items)
    found = None
    for item in items:
        hist = item.get("history") or {}
        ib = item.get("bvid") or hist.get("bvid")
        if ib in bvid_set:
            if found is None or (item.get("view_at") or 0) > (found.get("view_at") or 0):
                found = item

    if not found:
        print(f"[进度] 未找到 {bvid}，共扫描{scanned}条，耗时{time.time()-t0:.2f}s", flush=True)
        return _err_result(
            "notfound",
            f"在最近{scanned}条B站历史中未找到该视频。"
            f"cursor接口可能过滤了短时观看。请在B站网页点开该视频某集，播放30秒以上再刷新。",
            scanned=scanned)

    hit = _match_item(found, found.get("bvid") or (found.get("history") or {}).get("bvid"))
    hist = found.get("history") or {}
    # 合集：把命中的 bvid 映射回合集内的集序号（B站历史的 page 是单视频内分P号，不是合集集号）
    if episode_bvids:
        found_bv = found.get("bvid") or hist.get("bvid")
        try:
            hit["page"] = episode_bvids.index(found_bv) + 1
        except ValueError:
            pass
    print(f"[进度] >>> 命中！page={hit.get('page')} cid={hist.get('cid')} "
          f"progress={hit.get('progress')}s view_at={found.get('view_at')}", flush=True)
    return hit


def fetch_video_info(bvid, sessdata):
    """调 /x/web-interface/view?bvid= 拉取视频元数据与分P，构造完整 video dict。
    若视频属于 B站合集（ugc_season），返回的 video 中附带 season 信息供前端确认是否合并。"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    data, err = bilibili_get_bounded(url, sessdata, timeout=20)
    if err is not None:
        return None, _friendly_err(err)
    code = data.get("code")
    if code == -101:
        return None, "SESSDATA 已失效或未登录：请在右上角 ⚙ 设置中重新粘贴新的 SESSDATA"
    if code != 0:
        return None, f"B站返回错误：{data.get('message')} (code {code})"
    d = data.get("data") or {}
    real_bvid = d.get("bvid", bvid)
    pages = d.get("pages") or []
    episodes = []
    cumulative = 0
    for p in pages:
        dur = int(p.get("duration") or 0)
        episodes.append({
            "bvid": real_bvid,        # 多P视频所有集 bvid 相同；合集每集 bvid 不同
            "page": p.get("page", 1),
            "cid": str(p.get("cid", "")),
            "title": (p.get("part") or "").strip(),
            "duration": dur,
            "durationText": fmt_sec(dur),
            "cumulativeStart": cumulative,
            "cumulativeEnd": cumulative + dur,
        })
        cumulative += dur
    video = {
        "bvid": real_bvid,
        "url": f"https://www.bilibili.com/video/{real_bvid}",
        "title": d.get("title", ""),
        "owner": (d.get("owner") or {}).get("name", ""),
        "cover": d.get("pic", ""),
        "totalEpisodes": len(episodes),
        "totalDuration": cumulative,
        "totalDurationText": fmt_sec(cumulative),
        "episodes": episodes,
        "lastProgress": None,
        "added_at": int(time.time()),
    }
    # 检测是否属于 B站合集（ugc_season）：有则附带 season 信息
    ugc = d.get("ugc_season") or {}
    if ugc.get("id"):
        video["season"] = {
            "season_id": ugc.get("id"),
            "title": ugc.get("title") or "",
            "cover": ugc.get("cover") or "",
            "ep_count": ugc.get("ep_count") or len(ugc.get("archives") or []),
        }
    return video, None


def fetch_season_archives(season_id, sessdata, ref_bvid=None):
    """拉取 B站合集（ugc_season）内的所有视频，合并为一个多集追踪条目。
    通过 view 接口的 ugc_season.sections 获取全部剧集（无需额外合集接口）。
    ref_bvid 为合集中任一视频 bvid，用于调用 view 接口。"""
    if not ref_bvid:
        return None, "缺少参考 bvid，无法拉取合集信息"
    data, err = bilibili_get_bounded(
        f"https://api.bilibili.com/x/web-interface/view?bvid={ref_bvid}", sessdata, timeout=20)
    if err is not None:
        return None, _friendly_err(err)
    code = data.get("code")
    if code == -101:
        return None, "SESSDATA 已失效或未登录：请在右上角 ⚙ 设置中重新粘贴新的 SESSDATA"
    if code != 0:
        return None, f"B站返回错误：{data.get('message')} (code {code})"
    d = data.get("data") or {}
    ugc = d.get("ugc_season") or {}
    if not ugc.get("id") or str(ugc.get("id")) != str(season_id):
        return None, "该视频不再属于指定合集，无法合并"
    season_title = ugc.get("title") or ""
    # 合集封面用首集视频的封面（ugc_season.cover 常为纯黑合集封面图，体验差）
    season_cover = d.get("pic") or ugc.get("cover") or ""
    # 从 sections 展平所有 episodes
    episodes = []
    cumulative = 0
    first_bvid = None
    for sec in (ugc.get("sections") or []):
        for ep in (sec.get("episodes") or []):
            bv = ep.get("bvid") or ""
            if not first_bvid:
                first_bvid = bv
            pg = ep.get("page") or {}
            dur = int(pg.get("duration") or 0)
            cid = str(pg.get("cid") or ep.get("cid") or "")
            episodes.append({
                "bvid": bv,
                "page": len(episodes) + 1,  # 合集内集序号（不是视频内部分P page，合集每集都是独立视频）
                "cid": cid,
                "title": (ep.get("title") or pg.get("part") or "").strip(),
                "duration": dur,
                "durationText": fmt_sec(dur),
                "cumulativeStart": cumulative,
                "cumulativeEnd": cumulative + dur,
            })
            cumulative += dur
    video = {
        "bvid": first_bvid or f"season_{season_id}",
        "url": f"https://www.bilibili.com/video/{first_bvid}" if first_bvid else "",
        "title": season_title,
        "owner": (d.get("owner") or {}).get("name") or "",
        "cover": season_cover,
        "totalEpisodes": len(episodes),
        "totalDuration": cumulative,
        "totalDurationText": fmt_sec(cumulative),
        "episodes": episodes,
        "lastProgress": None,
        "added_at": int(time.time()),
        "is_season": True,
        "season_id": season_id,
    }
    return video, None


def fetch_history_list(sessdata, max_count=50):
    """拉取B站历史，返回简化列表（不过滤 bvid），供前端添加弹窗。"""
    print("[历史] 开始拉取B站历史列表", flush=True)
    t0 = time.time()
    items, err = _fetch_cursor_pages(sessdata, max_pages=3)  # 3页约90条
    if err is not None:
        kind = _classify_err(err)
        msg = str(err) if kind == "sessdata" else _friendly_err(err)
        return None, _err_result(kind, msg)
    out = []
    seen_bvid = set()
    for item in items:
        hist = item.get("history") or {}
        bvid = item.get("bvid") or hist.get("bvid")
        if not bvid:
            continue
        # 同一 bvid 只保留最新一条（view_at 最大）
        if bvid in seen_bvid:
            # 找到已加入的同 bvid 项，若新的 view_at 更大则替换
            for ex in out:
                if ex["bvid"] == bvid and (item.get("view_at") or 0) > ex.get("view_at", 0):
                    ex.update({
                        "title": item.get("title") or hist.get("title") or "",
                        "cover": item.get("cover") or hist.get("cover") or "",
                        "owner": item.get("author_name") or hist.get("author") or "",
                        "page": hist.get("page"),
                        "progress": item.get("progress", 0) or 0,
                        "view_at": item.get("view_at"),
                        "total_pages": item.get("videos") or 1,
                    })
                    break
            continue
        seen_bvid.add(bvid)
        out.append({
            "bvid": bvid,
            "title": item.get("title") or hist.get("title") or "",
            "cover": item.get("cover") or hist.get("cover") or "",
            "owner": item.get("author_name") or hist.get("author") or "",
            "page": hist.get("page"),
            "progress": item.get("progress", 0) or 0,
            "view_at": item.get("view_at"),
            "total_pages": item.get("videos") or 1,
        })
        if len(out) >= max_count:
            break
    print(f"[历史] 取到 {len(out)} 条不同视频，耗时{time.time()-t0:.2f}s", flush=True)
    return out, None


def fetch_image_cached(url, timeout=15):
    """本地缓存 + IPv4 拉取图片，返回 (bytes, content_type, err)。

    首次拉取后缓存到 covers/ 目录，后续（含离线）直接返回本地文件。
    仅允许B站图片域名，避免被滥用为开放代理。
    """
    # 域名白名单
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not (host.endswith(".hdslb.com") or host.endswith(".bilivideo.com")
            or host.endswith(".bilibili.com")):
        return None, None, "非白名单域名"
    os.makedirs(COVER_CACHE_DIR, exist_ok=True)
    # 用 URL path 的 basename 作为缓存文件名，清理非法字符
    raw_name = os.path.basename(urllib.parse.urlparse(url).path) or "img"
    name = "".join(c for c in raw_name if c.isalnum() or c in "._-")
    if not name:
        name = "img_" + str(abs(hash(url)) % 100000)
    cache = os.path.join(COVER_CACHE_DIR, name)
    # 命中缓存
    if os.path.exists(cache) and os.path.getsize(cache) > 0:
        try:
            with open(cache, "rb") as f:
                data = f.read()
            ext = os.path.splitext(name)[1].lower()
            return data, _IMG_TYPE.get(ext, "image/jpeg"), None
        except Exception as e:
            pass  # 缓存读失败，回退到拉取
    # 拉取（强制 IPv4 已通过全局补丁覆盖 hdslb.com；连接复用）
    body, ct, err = _https_get(url)
    if err is not None:
        return None, None, err
    if not body:
        return None, None, "空响应"
    data, box_ct = body, (ct or "image/jpeg")
    # 写缓存（失败不影响返回）
    try:
        with open(cache, "wb") as f:
            f.write(data)
    except Exception:
        pass
    return data, box_ct, None


def record_jump_if_needed(data, bvid, new_page, new_progress, new_view_at):
    """对比该 bvid 的【上次B站同步值 lastSynced】，若差距较大则记录跳跃。返回跳跃记录或 None。

    注意：基准用 lastSynced（仅B站同步写入），不用 lastProgress——后者会被用户在应用内
    拖进度条/切集/标记已看等手动操作覆盖，若拿它和B站历史比，B站值静止时会每次启动都
    误判出同一条"回看"。手动编辑进度是用户主观行为，不算跳跃，也不调用本函数。

    "差距较大"判定（对应用户口中的"如29集直接跳到第一集"）：
      - 跨集：向后跳（to < from）且 |gap| >= 3：记录（如 29→1）
      - 跨集：向前跳（to > from）且 gap >= 5：记录（如 1→10 跳过5集）
      - 集内：同一集进度大幅倒退 >= 180 秒（如 10 分钟跳回 1 分钟，回看复习）
    单集的正常来回（如 29↔28，gap=1）与集内自然前进不记录。
    调用方需先确认 new_view_at 比 lastSynced.view_at 新（B站端确有新观看事件），
    否则 B站历史静止时不应重复检测。
    """
    idx, video = _find_video(data, bvid)
    if not video:
        return None
    old = video.get("lastSynced")  # 基准：上一次B站同步值
    if not old or not old.get("page"):
        return None
    from_p = old.get("page") or 0
    to_p = new_page or 0
    old_prog = old.get("progress") or 0
    new_prog = new_progress or 0
    gap = abs(to_p - from_p)
    is_backward = to_p < from_p
    # 跨集：向后跳需 >=3，向前跳需 >=5
    notable = (is_backward and gap >= 3) or ((not is_backward) and gap >= 5)
    kind = "ep"
    # 集内：进度大幅倒退（回看），如 10 分钟跳回 1 分钟。
    # new_prog<0（B站 progress=-1 表示"本集已看完"）不算回看，否则看完会被误判成大幅倒退。
    if not notable and to_p == from_p and new_prog >= 0 and old_prog - new_prog >= 180:
        notable = True
        kind = "time"
    if not notable:
        return None
    jump = {
        "bvid": bvid,
        "kind": kind,
        "from_page": from_p,
        "to_page": to_p,
        "from_progress": old_prog,
        "to_progress": new_prog,
        "view_at": new_view_at,
        "detected_at": int(time.time()),
    }
    jumps = data.setdefault("jumps", [])
    # 去重兜底：完全相同的跳跃（同视频/类型/起终点）只记一次，防止重复刷屏
    sig = (bvid, kind, from_p, to_p, old_prog, new_prog)
    for prev in jumps[-5:]:
        if (prev.get("bvid"), prev.get("kind"), prev.get("from_page"),
                prev.get("to_page"), prev.get("from_progress"),
                prev.get("to_progress")) == sig:
            return None
    jumps.append(jump)
    # cap 100 条，保留最近的
    if len(jumps) > 100:
        del jumps[: len(jumps) - 100]
    if kind == "time":
        print(f"[进度] 检测到集内回看：第{from_p}集 {fmt_sec(old_prog)}→{fmt_sec(new_prog)} "
              f"(bvid={bvid})", flush=True)
    else:
        print(f"[进度] 检测到跳跃：第{from_p}集→第{to_p}集 (gap={gap}, bvid={bvid})", flush=True)
    return jump


def _update_shortcut_icon(ico_path):
    """更新桌面快捷方式图标（调 PowerShell WScript.Shell ComObject）。"""
    desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    lnk = os.path.join(desktop, "B站课程追踪.lnk")
    if not os.path.exists(lnk):
        return False, "桌面快捷方式不存在"
    if not os.path.exists(ico_path):
        return False, "图标文件不存在"
    cmd = ("$ws=New-Object -ComObject WScript.Shell; "
           "$l=$ws.CreateShortcut('{lnk}'); "
           "$l.IconLocation='{ico},0'; $l.Save()").format(lnk=lnk, ico=ico_path)
    try:
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-Command", cmd], timeout=12, capture_output=True)
    except Exception as e:
        return False, str(e)
    return True, None


# ===================== 诊断 =====================

def diag_connectivity(sessdata):
    """分阶段诊断到B站的连通性。"""
    out = {"steps": []}
    host = "api.bilibili.com"

    t0 = time.time()
    try:
        ip = socket.gethostbyname(host)
        out["steps"].append({"step": "DNS", "ok": True, "ip": ip, "ms": int((time.time() - t0) * 1000)})
    except Exception as e:
        out["steps"].append({"step": "DNS", "ok": False, "error": str(e)})
        return out

    t0 = time.time()
    try:
        s = socket.create_connection((ip, 443), timeout=8)
        s.close()
        out["steps"].append({"step": "TCP_443", "ok": True, "ip": ip, "ms": int((time.time() - t0) * 1000)})
    except Exception as e:
        out["steps"].append({"step": "TCP_443", "ok": False, "error": str(e)})
        return out

    t0 = time.time()
    data, err = bilibili_get_bounded("https://api.bilibili.com/x/v2/history", sessdata, timeout=15)
    ms = int((time.time() - t0) * 1000)
    if err is not None:
        out["steps"].append({"step": "HTTPS_history", "ok": False,
                             "error": f"{type(err).__name__}: {err}", "ms": ms})
        return out
    code = data.get("code")
    message = data.get("message")
    raw = data.get("data")
    dtype = type(raw).__name__
    count = len(raw) if isinstance(raw, list) else None
    out["steps"].append({"step": "HTTPS_history", "ok": True, "ms": ms, "code": code,
                         "message": message, "data_type": dtype, "count": count})
    return out


# ===================== HTTP Handler =====================

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "CourseTracker/2.0"

    def log_message(self, *a):
        pass  # 静默

    def _send(self, code, body=b"", ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _read_body_json(self):
        """读取 POST body 并解析 JSON，失败返回 (None, err_msg)。"""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return None, "无效的 Content-Length"
        if length <= 0:
            return {}, None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8")), None
        except Exception as e:
            return None, f"JSON 解析失败：{e}"

    def _guard(self):
        """本机端口防护：
        1) Host 必须是 127.0.0.1/localhost —— 防 DNS rebinding（恶意域名解析到 127.0.0.1 后借浏览器调用本地端口）；
        2) POST 请求若带浏览器特征头（Origin / Sec-Fetch-Site），必须是同源 —— 防其它网页 CSRF。
        非浏览器客户端（curl 等）不带这些头，允许通过（端口本就只绑 127.0.0.1）。"""
        host = (self.headers.get("Host") or "").lower()
        hostname = host.rsplit(":", 1)[0] if host else ""
        if hostname not in ("127.0.0.1", "localhost"):
            self._send(403, b'{"ok":false,"message":"forbidden host"}')
            return False
        if self.command in ("POST", "PUT", "DELETE"):
            origin = (self.headers.get("Origin") or "").lower()
            if origin:
                try:
                    o = urllib.parse.urlparse(origin)
                except ValueError:
                    o = None
                if o is None or o.hostname not in ("127.0.0.1", "localhost"):
                    self._send(403, b'{"ok":false,"message":"forbidden origin"}')
                    return False
            sfs = (self.headers.get("Sec-Fetch-Site") or "").lower()
            if sfs in ("cross-site", "same-site"):
                self._send(403, b'{"ok":false,"message":"forbidden site"}')
                return False
        return True

    # -------- GET --------
    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                self._send_json({"ok": False, "message": f"server error: {e}"})
            except Exception:
                pass

    def _do_GET(self):
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # 追踪列表（精简；描述性元数据实时从B站补齐）
        if path == "/api/tracked":
            detail = qs.get("detail", ["0"])[0] == "1"
            with _wlock:
                data = load_tracked()
            sd = get_sessdata()
            if detail:
                bvid = (qs.get("bvid") or [None])[0]
                if not bvid:
                    bvid = data.get("active_bvid")
                idx, video = _find_video(data, bvid) if bvid else (None, None)
                if not video:
                    self._send_json({"ok": False, "message": "未找到该视频"}, 404)
                    return
                self._send_json({"ok": True, "video": _enrich_video(video, sd)})
                return
            # 精简列表：先把内存里缺元数据的视频并行拉齐（仅首次，约1秒）
            missing = [v for v in data.get("videos", []) if v.get("bvid") not in _meta_cache]
            if missing and sd:
                try:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=min(4, len(missing))) as ex:
                        list(ex.map(lambda v: _enrich_video(v, sd), missing))
                except Exception:
                    pass
            summaries = [_video_summary(_enrich_video(v)) for v in data.get("videos", [])]
            self._send_json({
                "ok": True,
                "videos": summaries,
                "jumps": data.get("jumps", []),
                "active_bvid": data.get("active_bvid"),
            })
            return

        # PWA manifest：独立窗口/PWA 模式的标题栏配色与图标
        if path == "/manifest.webmanifest":
            self._send_json({
                "name": "B站课程进度追踪",
                "short_name": "课程追踪",
                "display": "standalone",
                "background_color": "#0a1024",
                "theme_color": "#0a1024",
                "icons": [{"src": "/api/icon/file", "sizes": "256x256",
                           "type": "image/png", "purpose": "any"}],
            })
            return

        # 心跳：Linux 兜底标签页模式据此判断页面是否全部关闭
        if path == "/api/ping":
            self._send_json({"ok": True})
            return

        # B站历史列表（添加弹窗用）
        if path == "/api/history":
            sd = get_sessdata()
            if not sd:
                self._send_json({"ok": False, "needSessdata": True,
                                 "message": "未配置 SESSDATA：请在右上角 ⚙ 设置中粘贴 SESSDATA"})
                return
            max_count = int((qs.get("max", ["50"])[0] or "50"))
            lst, err = fetch_history_list(sd, max_count=max_count)
            if err is not None:
                self._send_json(err)
                return
            self._send_json({"ok": True, "items": lst})
            return

        # 实时进度同步（按 bvid 参数）
        if path == "/api/progress":
            sd = get_sessdata()
            if not sd:
                self._send_json(_err_result(
                    "sessdata", "未配置 SESSDATA：请在右上角 ⚙ 设置中粘贴 SESSDATA"))
                return
            bvid = (qs.get("bvid") or [None])[0]
            if not bvid:
                self._send_json({"ok": False, "message": "缺少 bvid 参数"})
                return
            # 合集条目需传入所有集的 bvid 做多 bvid 搜索
            episode_bvids = None
            with _wlock:
                data = load_tracked()
                idx, video = _find_video(data, bvid)
            if video and video.get("is_season"):
                ev = _enrich_video(video, sd)
                eps = ev.get("episodes") or []
                episode_bvids = [e.get("bvid") for e in eps if e.get("bvid")] or None
            res = fetch_progress(sd, bvid, episode_bvids=episode_bvids)
            if not res.get("ok"):
                self._send_json(res)
                return
            # 写入本地 + 跳跃检测
            with _wlock:
                data = load_tracked()
                idx, video = _find_video(data, bvid)
                if not video:
                    # 视频不在追踪列表，仅返回进度不存储
                    self._send_json(res)
                    return
                new_va = res.get("view_at")
                old_synced = video.get("lastSynced") or {}
                # 仅当 B站端出现更新的观看事件（view_at 变新）才做跳跃检测；
                # B站历史静止（用户没在B站新看）时不检测，避免每次启动重复误报同一条回看。
                jump = None
                if old_synced.get("page") and new_va and new_va > (old_synced.get("view_at") or 0):
                    jump = record_jump_if_needed(
                        data, bvid,
                        new_page=res.get("page"),
                        new_progress=res.get("progress", 0),
                        new_view_at=new_va,
                    )
                # lastSynced：B站同步基准（仅此处写入，跳跃检测用）
                video["lastSynced"] = {
                    "page": res.get("page"),
                    "progress": res.get("progress", 0),
                    "view_at": new_va,
                }
                # lastProgress：界面显示/本地回退用
                video["lastProgress"] = {
                    "page": res.get("page"),
                    "progress": res.get("progress", 0),
                    "view_at": new_va,
                    "synced_at": int(time.time()),
                }
                save_tracked(data)
            res["jump"] = jump
            self._send_json(res)
            return

        # 番茄钟：读今日专注秒数；先恢复异常退出残留的会话并自动入账
        if path == "/api/focus":
            recovered = recover_focus_session()
            self._send_json({"ok": True, "date": time.strftime("%Y-%m-%d"),
                             "seconds": load_focus(), "recovered": recovered})
            return

        # 连通性诊断
        if path == "/api/diag":
            sd = get_sessdata()
            res = {"sessdata_configured": bool(sd),
                   "diag": diag_connectivity(sd) if sd else None}
            self._send(200, json.dumps(res, ensure_ascii=False, indent=2).encode("utf-8"))
            return

        # 查询 SESSDATA 是否已配置（不返回明文，仅示状态）；加 ?value=1 时返回明文（供设置弹窗回填）
        if path == "/api/sessdata":
            show_value = qs.get("value", [""])[0] == "1"
            sd = get_sessdata()
            resp = {"configured": bool(sd)}
            if show_value and sd:
                resp["value"] = sd
            self._send_json(resp)
            return

        # DEBUG：查看扫码登录状态和 cookie jar（排查用）
        if path == "/api/_debug/qrstate":
            with _qr_lock:
                st = dict(_qr_state)
            cookies = []
            if st.get("opener"):
                for c in st["opener"].cookiejar:
                    cookies.append({"domain": c.domain, "name": c.name,
                                    "value": c.value[:30] if c.value else None})
            self._send_json({"has_key": bool(st.get("key")), "key": st.get("key"),
                             "cookies": cookies})
            return

        # 扫码登录：轮询二维码状态（密钥保存在服务端内存，前端只拿状态）
        if path == "/api/qrlogin/poll":
            res, err = qrlogin_poll()
            if err is not None:
                # 扫码模块独立于 AppStatus，kind 仅作协议完整性（network 含超时/DNS）
                kind = "network" if any(w in err for w in ("超时", "网络", "DNS", "连接")) else "bili"
                self._send_json(_err_result(kind, err))
                return
            self._send_json({"ok": True, **res})
            return

        # 图标预览图（png，供设置弹窗显示当前图标）
        if path == "/api/icon/file":
            p = os.path.join(ROOT, "app_icon_preview.png")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    self._send(200, f.read(), "image/png")
                return
            # 无自定义图标时回退应用默认图标（数据目录迁移到 XDG 后，
            # ROOT 下不再有 preview 文件；标题栏 logo 不能因此变空）
            default_ico = os.path.join(os.path.dirname(FRONTEND_DIR),
                                       "assets", "app_icon.ico")
            if os.path.exists(default_ico):
                png = _icon_ico_to_png(default_ico, 256)
                if png:
                    self._send(200, png, "image/png")
                    return
                with open(default_ico, "rb") as f:
                    self._send(200, f.read(), "image/x-icon")
                return
            self._send(404, b"no icon", "text/plain; charset=utf-8")
            return

        # 图片代理（IPv4 拉取 + 本地缓存，解决浏览器直连B站图片 IPv6 挂起）
        if path == "/api/img":
            u = (qs.get("u") or [None])[0]
            if not u or not (u.startswith("http://") or u.startswith("https://")):
                self._send(404, b"bad url", "text/plain; charset=utf-8")
                return
            data, ct, err = fetch_image_cached(u, timeout=15)
            if data is None:
                # 返回 1x1 透明占位图，避免前端 onerror 隐藏占位框
                blank = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
                        b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03"
                        b"\x00\x01\x5d\xcc\xdb\xd2\x00\x00\x00\x00IEND\xaeB`\x82")
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(blank)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(blank)
                return
            self.send_response(200)
            self.send_header("Content-Type", ct or "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            # 图片稳定，允许浏览器缓存
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return

        # 静态文件
        if path in ("/", "/index.html"):
            rel = "index.html"
        else:
            rel = path.lstrip("/")

        # 禁止访问凭据文件与隐藏文件
        if rel == ".sessdata.txt" or rel.startswith(".") or ".." in path:
            self._send(404, b"404 not found", "text/plain; charset=utf-8")
            return

        f = os.path.realpath(os.path.join(FRONTEND_DIR, rel))
        root_real = os.path.realpath(FRONTEND_DIR)
        if not (f == root_real or f.startswith(root_real + os.sep)) or not os.path.isfile(f):
            self._send(404, b"404 not found", "text/plain; charset=utf-8")
            return

        ext = os.path.splitext(rel)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        with open(f, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # -------- POST --------
    def do_POST(self):
        if not self._guard():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 添加追踪视频
        if path == "/api/tracked/add":
            body, err = self._read_body_json()
            if err is not None:
                self._send_json({"ok": False, "message": err})
                return
            bvid = (body or {}).get("bvid", "").strip()
            if not bvid:
                self._send_json({"ok": False, "message": "缺少 bvid"})
                return
            merge_season = (body or {}).get("merge_season")  # None=未决定, True=合并合集, False=仅追踪此视频
            season_id = (body or {}).get("season_id")
            sd = get_sessdata()
            if not sd:
                self._send_json(_err_result(
                    "sessdata", "未配置 SESSDATA：无法拉取视频信息"))
                return
            # 合并合集模式：直接拉取合集所有视频
            if merge_season is True and season_id:
                video, ferr = fetch_season_archives(season_id, sd, ref_bvid=bvid)
                if video is None:
                    self._send_json({"ok": False, "message": ferr or "拉取合集信息失败"})
                    return
                add_bvid = video["bvid"]
            else:
                # 普通模式：拉取单视频信息
                video, ferr = fetch_video_info(bvid, sd)
                if video is None:
                    self._send_json({"ok": False, "message": ferr or "拉取视频信息失败"})
                    return
                add_bvid = bvid
                # 若视频属于合集且用户尚未决定，返回 season 信息供前端确认
                season = video.get("season")
                if season and merge_season is None:
                    self._send_json({
                        "ok": True,
                        "need_confirm": True,
                        "season": season,
                        "video": {"bvid": bvid, "title": video.get("title", "")},
                    })
                    return
            with _wlock:
                data = load_tracked()
                # 合并合集时：移除已被单独追踪的合集内视频（避免与合集条目冲突），
                # 并迁移它们的 lastProgress（取 view_at 最新的一个，page 映射为合集内集序号）
                if merge_season is True:
                    eps = video.get("episodes") or []
                    ep_bvids = {e.get("bvid") for e in eps if e.get("bvid")}
                    bvid_to_page = {e["bvid"]: i + 1 for i, e in enumerate(eps) if e.get("bvid")}
                    migrated_lp = None
                    migrated_bvid = None
                    remaining = []
                    for v in data.get("videos", []):
                        if v.get("bvid") in ep_bvids:
                            lp = v.get("lastProgress")
                            if lp and (not migrated_lp or (lp.get("view_at") or 0) > (migrated_lp.get("view_at") or 0)):
                                migrated_lp = lp
                                migrated_bvid = v.get("bvid")
                            continue
                        remaining.append(v)
                    data["videos"] = remaining
                    if migrated_lp and migrated_bvid and migrated_bvid in bvid_to_page:
                        migrated_lp = dict(migrated_lp)
                        migrated_lp["page"] = bvid_to_page[migrated_bvid]
                        video["lastProgress"] = migrated_lp
                idx, existing = _find_video(data, add_bvid)
                if existing:
                    self._send_json({"ok": False, "message": "该视频已在追踪列表中",
                                     "video": _video_summary(existing)})
                    return
            _cache_meta(video)   # 标题/封面/分P 只进内存缓存，磁盘仅落数字
            with _wlock:
                data = load_tracked()
                # 再次清理（并发时可能有变化）
                if merge_season is True:
                    ep_bvids = {e.get("bvid") for e in (video.get("episodes") or []) if e.get("bvid")}
                    data["videos"] = [v for v in data.get("videos", []) if v.get("bvid") not in ep_bvids]
                idx, existing = _find_video(data, add_bvid)
                if existing:
                    self._send_json({"ok": False, "message": "该视频已在追踪列表中",
                                     "video": _video_summary(existing)})
                    return
                data.setdefault("videos", []).append(video)
                if not data.get("active_bvid"):
                    data["active_bvid"] = add_bvid
                save_tracked(data)
            print(f"[追踪] 新增视频：{add_bvid} - {video.get('title', '')[:30]}", flush=True)
            self._send_json({"ok": True, "video": _video_summary(video)})
            return

        # 移除追踪视频
        if path == "/api/tracked/remove":
            body, err = self._read_body_json()
            if err is not None:
                self._send_json({"ok": False, "message": err})
                return
            bvid = (body or {}).get("bvid", "").strip()
            if not bvid:
                self._send_json({"ok": False, "message": "缺少 bvid"})
                return
            with _wlock:
                data = load_tracked()
                idx, video = _find_video(data, bvid)
                if not video:
                    self._send_json({"ok": False, "message": "该视频不在追踪列表"})
                    return
                data["videos"].pop(idx)
                if data.get("active_bvid") == bvid:
                    data["active_bvid"] = data["videos"][0]["bvid"] if data["videos"] else None
                save_tracked(data)
            self._send_json({"ok": True, "active_bvid": data.get("active_bvid")})
            return

        # 设置当前活跃视频
        if path == "/api/tracked/active":
            body, err = self._read_body_json()
            if err is not None:
                self._send_json({"ok": False, "message": err})
                return
            bvid = (body or {}).get("bvid", "").strip()
            if not bvid:
                self._send_json({"ok": False, "message": "缺少 bvid"})
                return
            with _wlock:
                data = load_tracked()
                idx, video = _find_video(data, bvid)
                if not video:
                    self._send_json({"ok": False, "message": "该视频不在追踪列表"})
                    return
                data["active_bvid"] = bvid
                save_tracked(data)
            self._send_json({"ok": True})
            return

        # 保存 SESSDATA（系统密钥环，无需重启，下次请求即生效）
        if path == "/api/sessdata":
            body, err = self._read_body_json()
            if err is not None:
                self._send_json({"ok": False, "message": err})
                return
            sd = (body or {}).get("sessdata", "")
            with _wlock:
                ok = write_sessdata(sd)
            if not ok:
                self._send_json({"ok": False, "message": "SESSDATA 不能为空"})
                return
            print("[配置] SESSDATA 已通过设置面板保存", flush=True)
            self._send_json({"ok": True, "configured": True})
            return

        # 扫码登录：申请二维码（返回二维码内容 URL + 矩阵，前端渲染成二维码图片）
        if path == "/api/qrlogin/start":
            res, err = qrlogin_start()
            if err is not None:
                kind = "network" if any(w in err for w in ("超时", "网络", "DNS", "连接")) else "bili"
                self._send_json(_err_result(kind, err))
                return
            self._send_json({"ok": True, **res})
            return

        # 登出：删除 .sessdata.bin（DPAPI 加密凭据），下次请求即判为未配置
        if path == "/api/sessdata/logout":
            with _wlock:
                delete_sessdata()
            print("[配置] SESSDATA 已通过登出删除", flush=True)
            self._send_json({"ok": True, "configured": False})
            return

        # 手动保存进度（拖进度条/切集/标记已看）：只更新 lastProgress，不做跳跃检测、
        # 不碰 lastSynced——用户主动编辑自己的进度不是"意外跳跃"，否则会与B站历史值
        # 互相覆盖，导致每次启动同步都误报同一条回看。
        if path == "/api/progress/save":
            body, err = self._read_body_json()
            if err is not None:
                self._send_json({"ok": False, "message": err})
                return
            bvid = (body or {}).get("bvid", "").strip()
            page = (body or {}).get("page")
            progress = (body or {}).get("progress", 0)
            if not bvid or page is None:
                self._send_json({"ok": False, "message": "缺少 bvid 或 page"})
                return
            now = int(time.time())
            with _wlock:
                data = load_tracked()
                idx, video = _find_video(data, bvid)
                if not video:
                    self._send_json({"ok": False, "message": "该视频不在追踪列表"})
                    return
                old_va = (video.get("lastProgress") or {}).get("view_at") or now
                video["lastProgress"] = {
                    "page": int(page),
                    "progress": int(progress),
                    "view_at": old_va,
                    "synced_at": now,
                    "manual": True,
                }
                save_tracked(data)
            self._send_json({"ok": True, "jump": None})
            return

        # 番茄钟：开始 / 暂停 / 继续 / 心跳 / 结束（运行态全程持久化，防退出丢失）
        if path == "/api/focus/start":
            now = start_focus_session()
            self._send_json({"ok": True, "now": now})
            return
        if path == "/api/focus/pause":
            ok = pause_focus_session()
            self._send_json({"ok": ok})
            return
        if path == "/api/focus/resume":
            now = resume_focus_session()
            self._send_json({"ok": now is not None, "now": now})
            return
        if path == "/api/focus/heartbeat":
            heartbeat_focus_session()
            self._send_json({"ok": True})
            return
        if path == "/api/focus/stop":
            total, credited = stop_focus_session()
            self._send_json({"ok": True, "date": time.strftime("%Y-%m-%d"),
                             "seconds": total, "credited": credited})
            return
        # 兼容旧版：直接累加指定秒数
        if path == "/api/focus":
            body, err = self._read_body_json()
            if err is not None:
                self._send_json({"ok": False, "message": err})
                return
            total = add_focus((body or {}).get("add", 0))
            self._send_json({"ok": True, "date": time.strftime("%Y-%m-%d"),
                             "seconds": total})
            return

        # 自定义应用图标：上传图片(base64)生成 ico + 更新桌面快捷方式；或重置为默认
        if path == "/api/icon":
            body, err = self._read_body_json()
            if err is not None:
                self._send_json({"ok": False, "message": err})
                return
            try:
                import make_icon
            except Exception as e:
                self._send_json({"ok": False, "message": f"图标模块加载失败：{e}"})
                return
            action = (body or {}).get("action", "")
            try:
                if action == "reset":
                    make_icon.generate_default_icon()
                else:
                    img_b64 = (body or {}).get("image", "")
                    if not img_b64:
                        self._send_json({"ok": False, "message": "缺少 image 数据或 action=reset"})
                        return
                    # 兼容 data URL（data:image/png;base64,xxxx）
                    if "," in img_b64 and img_b64.startswith("data:"):
                        img_b64 = img_b64.split(",", 1)[1]
                    raw = base64.b64decode(img_b64)
                    if len(raw) < 64:
                        self._send_json({"ok": False, "message": "图片数据过小或无效"})
                        return
                    make_icon.image_to_ico(raw)
            except Exception as e:
                self._send_json({"ok": False, "message": f"图标生成失败：{e}"})
                return
            ok, merr = _update_shortcut_icon(make_icon.ICON_FILE)
            msg = "图标已恢复为默认" if action == "reset" else "图标已应用"
            if not ok and merr:
                msg += f"（桌面快捷方式未更新：{merr}，需手动重启应用生效）"
            print(f"[图标] {msg}（action={action or 'upload'}）", flush=True)
            self._send_json({"ok": True, "message": msg, "shortcut_updated": ok})
            return

        # 关闭应用：标题栏关闭按钮 POST 调用，立即返回后退出整个进程
        if path == "/api/quit":
            self._send_json({"ok": True})
            # 延迟 50ms 退出，确保响应已发送给客户端
            threading.Timer(0.05, lambda: os._exit(0)).start()
            return

        self._send_json({"ok": False, "message": "未知的 POST 路径"}, 404)


def make_server():
    """绑定本地 HTTP 服务：8765 被占时回退 8775。返回 (httpd, port)。"""
    last_err = None
    for p in (8765, 8775):
        try:
            httpd = http.server.ThreadingHTTPServer((HOST, p), Handler)
            return httpd, p
        except OSError as e:
            last_err = e
    raise last_err


def main():
    # 确保数据目录存在（源码运行时 ROOT 可能尚未创建）
    os.makedirs(ROOT, exist_ok=True)
    # 首次启动迁移
    migrate_from_course_data()

    print("=" * 56)
    print(" 具身智能机械臂课程 · 多视频进度追踪服务")
    print("=" * 56)
    httpd, port = make_server()
    print(f" 访问地址  : http://{HOST}:{port}/")
    sd = get_sessdata()
    print(f" SESSDATA  : {'已配置（进度同步可用）' if sd else '未配置（请在右上角 ⚙ 设置中粘贴 SESSDATA）'}")
    data = load_tracked()
    print(f" 追踪视频  : {len(data.get('videos', []))} 个")
    print(f" 活跃视频  : {data.get('active_bvid') or '（无）'}")
    print(f" 跳跃记录  : {len(data.get('jumps', []))} 条")
    print(" 按 Ctrl+C 退出")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()

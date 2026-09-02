# -*- coding: utf-8 -*-
"""
本地代理服务：托管界面 + 实时读取B站观看进度（多视频追踪）。
用法：
  1. 把你的 SESSDATA 填入同目录 .sessdata.txt（或设环境变量 BILI_SESSDATA）
  2. py -3 server.py
  3. 浏览器打开 http://127.0.0.1:8765/

说明：
  - 仅绑定 127.0.0.1，不对外暴露。
  - SESSDATA 只在本进程内存中用于请求B站API，不会写入日志、不会回显。
  - .sessdata.txt 被静态服务拒绝访问，不会泄露。
  - 追踪视频列表与跳跃记录持久化到 tracked_videos.json，无网络也能加载。
"""
import base64
import http.client
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
# 数据目录（tracked_videos.json / course_data.json / covers / .sessdata.txt）：
# 打包后用 exe 所在目录，保证数据持久化且与 exe 一起移动；
# 源码运行时用项目内的应用目录，与打包版共用同一份数据。
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(os.path.abspath(sys.executable))
else:
    ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         "..", "B站课程进度追踪"))
# 前端资源目录：打包后 index.html 等内嵌进 _internal（PyInstaller 会把 _MEIPASS 指向它）
if getattr(sys, "frozen", False):
    FRONTEND_DIR = sys._MEIPASS
else:
    FRONTEND_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_BVID = "BV1nz6KBHEc9"  # 仅用于启动迁移默认值，运行时不再硬编码
TRACKED_FILE = os.path.join(ROOT, "tracked_videos.json")
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


def get_sessdata():
    """优先环境变量 BILI_SESSDATA，其次 .sessdata.txt 第一个非注释行。"""
    s = os.environ.get("BILI_SESSDATA", "").strip()
    if s:
        v = s
    else:
        p = os.path.join(ROOT, ".sessdata.txt")
        v = ""
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        v = line
                        break
    if v.startswith("SESSDATA="):
        v = v[len("SESSDATA="):]
    v = v.rstrip(";").strip().strip('"').strip("'")
    return v


def write_sessdata(value):
    """清洗并写入 .sessdata.txt（带注释）。清洗规则与 get_sessdata() 一致。"""
    v = (value or "").strip()
    if v.startswith("SESSDATA="):
        v = v[len("SESSDATA="):]
    v = v.rstrip(";").strip().strip('"').strip("'")
    if not v:
        return False
    p = os.path.join(ROOT, ".sessdata.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# Bilibili SESSDATA（从浏览器 Cookie 获取，仅本地使用，不上传）\n")
        f.write("# 获取：登录 bilibili.com -> F12 -> Application/应用 -> Cookies -> 复制 SESSDATA 的值\n")
        f.write(v + "\n")
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


# ===================== 本地存储 =====================

def default_tracked():
    return {"videos": [], "jumps": [], "active_bvid": None}


def load_tracked():
    """读 tracked_videos.json；文件不存在或损坏返回默认结构。用 utf-8-sig 容忍 BOM。"""
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
    data.setdefault("jumps", [])
    data.setdefault("active_bvid", None)
    return data


def save_tracked(data):
    """原子写：先写临时文件再 os.replace。"""
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


def _video_summary(v):
    """前端 tab 列表用的精简结构（不含 episodes）。"""
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


def fetch_progress(sessdata, bvid):
    """读取B站历史(cursor接口)，查找指定 bvid 的分P进度。"""
    print("=" * 48, flush=True)
    print(f"[进度] 开始读取B站历史 (cursor) 目标={bvid}", flush=True)
    t0 = time.time()
    items, err = _fetch_cursor_pages(sessdata, max_pages=10, stop_bvid=bvid)
    if err is not None:
        msg = _friendly_err(err)
        print("[进度] " + msg, flush=True)
        res = {"ok": False, "message": msg}
        if "SESSDATA" in msg:
            res["needSessdata"] = True
        return res

    scanned = len(items)
    found = None
    for item in items:
        hist = item.get("history") or {}
        if item.get("bvid") == bvid or hist.get("bvid") == bvid:
            if found is None or (item.get("view_at") or 0) > (found.get("view_at") or 0):
                found = item

    if not found:
        print(f"[进度] 未找到 {bvid}，共扫描{scanned}条，耗时{time.time()-t0:.2f}s", flush=True)
        return {"ok": False,
                "message": f"在最近{scanned}条B站历史中未找到该视频。"
                           f"cursor接口可能过滤了短时观看。请在B站网页点开该视频某集，播放30秒以上再刷新。"}

    hit = _match_item(found, bvid)
    hist = found.get("history") or {}
    print(f"[进度] >>> 命中！page={hit.get('page')} cid={hist.get('cid')} "
          f"progress={hit.get('progress')}s view_at={found.get('view_at')}", flush=True)
    return hit


def fetch_video_info(bvid, sessdata):
    """调 /x/web-interface/view?bvid= 拉取视频元数据与分P，构造完整 video dict。"""
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
    pages = d.get("pages") or []
    episodes = []
    cumulative = 0
    for p in pages:
        dur = int(p.get("duration") or 0)
        episodes.append({
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
        "bvid": d.get("bvid", bvid),
        "url": f"https://www.bilibili.com/video/{d.get('bvid', bvid)}",
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
    return video, None


def fetch_history_list(sessdata, max_count=50):
    """拉取B站历史，返回简化列表（不过滤 bvid），供前端添加弹窗。"""
    print("[历史] 开始拉取B站历史列表", flush=True)
    t0 = time.time()
    items, err = _fetch_cursor_pages(sessdata, max_pages=3)  # 3页约90条
    if err is not None:
        res = {"ok": False, "message": _friendly_err(err)}
        if "SESSDATA" in str(res["message"]):
            res["needSessdata"] = True
        return None, res
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
    """对比该 bvid 的 lastProgress，若差距较大则记录跳跃。返回本次跳跃记录或 None。

    "差距较大"判定（对应用户口中的"如29集直接跳到第一集"）：
      - 跨集：向后跳（to < from）且 |gap| >= 3：记录（如 29→1）
      - 跨集：向前跳（to > from）且 gap >= 5：记录（如 1→10 跳过5集）
      - 集内：同一集进度大幅倒退 >= 180 秒（如 10 分钟跳回 1 分钟，回看复习）
    单集的正常来回（如 29↔28，gap=1）与集内自然前进不记录。
    """
    idx, video = _find_video(data, bvid)
    if not video:
        return None
    old = video.get("lastProgress")
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
    # 集内：进度大幅倒退（回看），如 10 分钟跳回 1 分钟
    if not notable and to_p == from_p and old_prog - new_prog >= 180:
        notable = True
        kind = "time"
    jump = None
    if notable:
        jump = {
            "bvid": bvid,
            "title": video.get("title", ""),
            "kind": kind,
            "from_page": from_p,
            "to_page": to_p,
            "from_progress": old_prog,
            "to_progress": new_prog,
            "view_at": new_view_at,
            "detected_at": int(time.time()),
        }
        jumps = data.setdefault("jumps", [])
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

    # -------- GET --------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # 追踪列表（精简）
        if path == "/api/tracked":
            detail = qs.get("detail", ["0"])[0] == "1"
            with _wlock:
                data = load_tracked()
            if detail:
                bvid = (qs.get("bvid") or [None])[0]
                if not bvid:
                    bvid = data.get("active_bvid")
                idx, video = _find_video(data, bvid) if bvid else (None, None)
                if not video:
                    self._send_json({"ok": False, "message": "未找到该视频"}, 404)
                    return
                self._send_json({"ok": True, "video": video})
                return
            # 精简列表
            summaries = [_video_summary(v) for v in data.get("videos", [])]
            self._send_json({
                "ok": True,
                "videos": summaries,
                "jumps": data.get("jumps", []),
                "active_bvid": data.get("active_bvid"),
            })
            return

        # B站历史列表（添加弹窗用）
        if path == "/api/history":
            sd = get_sessdata()
            if not sd:
                self._send_json({"ok": False, "needSessdata": True,
                                 "message": "未配置 SESSDATA：请在 .sessdata.txt 填入后重启服务"})
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
                self._send_json({"ok": False, "needSessdata": True,
                                 "message": "未配置 SESSDATA：请在 .sessdata.txt 填入后重启服务"})
                return
            bvid = (qs.get("bvid") or [None])[0]
            if not bvid:
                self._send_json({"ok": False, "message": "缺少 bvid 参数"})
                return
            res = fetch_progress(sd, bvid)
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
                jump = record_jump_if_needed(
                    data, bvid,
                    new_page=res.get("page"),
                    new_progress=res.get("progress", 0),
                    new_view_at=res.get("view_at"),
                )
                video["lastProgress"] = {
                    "page": res.get("page"),
                    "progress": res.get("progress", 0),
                    "view_at": res.get("view_at"),
                    "synced_at": int(time.time()),
                }
                save_tracked(data)
            res["jump"] = jump
            self._send_json(res)
            return

        # 连通性诊断
        if path == "/api/diag":
            sd = get_sessdata()
            res = {"sessdata_configured": bool(sd),
                   "diag": diag_connectivity(sd) if sd else None}
            self._send(200, json.dumps(res, ensure_ascii=False, indent=2).encode("utf-8"))
            return

        # 查询 SESSDATA 是否已配置（不返回明文，仅示状态）
        if path == "/api/sessdata":
            self._send_json({"configured": bool(get_sessdata())})
            return

        # 图标预览图（png，供设置弹窗显示当前图标）
        if path == "/api/icon/file":
            p = os.path.join(ROOT, "app_icon_preview.png")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    self._send(200, f.read(), "image/png")
            else:
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
            sd = get_sessdata()
            if not sd:
                self._send_json({"ok": False, "needSessdata": True,
                                 "message": "未配置 SESSDATA：无法拉取视频信息"})
                return
            with _wlock:
                data = load_tracked()
                idx, existing = _find_video(data, bvid)
                if existing:
                    self._send_json({"ok": False, "message": "该视频已在追踪列表中",
                                     "video": _video_summary(existing)})
                    return
            # 拉取视频信息（耗时，放在锁外）
            video, ferr = fetch_video_info(bvid, sd)
            if video is None:
                self._send_json({"ok": False, "message": ferr or "拉取视频信息失败"})
                return
            with _wlock:
                data = load_tracked()
                # 再次检查（并发时可能已被加）
                idx, existing = _find_video(data, bvid)
                if existing:
                    self._send_json({"ok": False, "message": "该视频已在追踪列表中",
                                     "video": _video_summary(existing)})
                    return
                data.setdefault("videos", []).append(video)
                if not data.get("active_bvid"):
                    data["active_bvid"] = bvid
                save_tracked(data)
            print(f"[追踪] 新增视频：{bvid} - {video.get('title', '')[:30]}", flush=True)
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

        # 保存 SESSDATA 到 .sessdata.txt（无需重启，下次请求即生效）
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

        # 手动保存进度（也触发跳跃检测：手动大跨度切集算跳跃；同集拖滑块 page 不变不误报）
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
                # 先用旧 lastProgress 做跳跃检测，再更新（基于 page 变化）
                old_va = (video.get("lastProgress") or {}).get("view_at") or now
                jump = record_jump_if_needed(
                    data, bvid,
                    new_page=int(page), new_progress=int(progress),
                    new_view_at=old_va,
                )
                video["lastProgress"] = {
                    "page": int(page),
                    "progress": int(progress),
                    "view_at": old_va,
                    "synced_at": now,
                    "manual": True,
                }
                save_tracked(data)
            self._send_json({"ok": True, "jump": jump})
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


def main():
    # 首次启动迁移
    migrate_from_course_data()

    print("=" * 56)
    print(" 具身智能机械臂课程 · 多视频进度追踪服务")
    print("=" * 56)
    print(f" 访问地址  : http://{HOST}:{PORT}/")
    sd = get_sessdata()
    print(f" SESSDATA  : {'已配置（进度同步可用）' if sd else '未配置（请在 .sessdata.txt 填入后重启）'}")
    data = load_tracked()
    print(f" 追踪视频  : {len(data.get('videos', []))} 个")
    print(f" 活跃视频  : {data.get('active_bvid') or '（无）'}")
    print(f" 跳跃记录  : {len(data.get('jumps', []))} 条")
    print(" 按 Ctrl+C 退出")
    print("=" * 56)
    try:
        http.server.ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")


if __name__ == "__main__":
    main()

# AGENTS.md —— AI 开发协作约定与项目记忆

> **给 AI 助手的硬性规则（每次会话必读、必遵守）：**
>
> 1. **开始动手前**：通读本文件，再读对应源码。不要凭猜测修改。
> 2. **完成改动后**：如果改动涉及架构、约定、踩坑经验或本文件记录的任何条目，**必须同步更新本文件**（新增/修正/删除过时内容）。这是项目记忆，随仓库转移，目的是让下一位 AI 不重复踩坑。
> 3. **只写稳定知识**：目录结构、关键约定、协议细节、踩坑教训、红线规则。不写临时进度、日志、一次性对话内容。
> 4. **隐私红线**：严禁把 SESSDATA、真实观看记录、真实课程标题/UP 主、cookie、个人路径等写进本文件或任何会提交的文件。举例一律用虚构数据（如"高等数学基础班 · BV1DemoMath01"）。
> 5. **与用户沟通**：使用中文；先给方案/分析，用户确认后再动手；不要使用"（推荐）""最快上手（3 步）"这类营销腔。
> 6. **bat 脚本必须纯 ASCII**（GBK cmd 下中文乱码）；中文文件名操作放 build.py。
> 7. 打包命令：`py -3 build.py`（onedir + --noconsole）。沙箱环境打包需 `PYTHONDONTWRITEBYTECODE=1`，`PYINSTALLER_CONFIG_DIR` 指向项目内 `build/pyi-cache`。

---

## 项目概览

B站课程观看进度追踪桌面应用：pywebview（本地 HTTP 服务 + 内嵌 WebView2 窗口），纯本地运行，通过 B站历史接口实时读取观看进度，支持多课程追踪、跳跃/回看检测、100% 庆祝动效。

- 后端：`src/server.py`（http.server，仅绑定 127.0.0.1，端口 8765，被占自动回退 8775）
- 前端：`src/index.html`（单文件，原生 JS + CSS，无框架）
- 入口：`src/app.py`（pywebview 窗口、启动画面、八向缩放、托盘式关闭）
- 打包：`build.py` + `打包.bat`（PyInstaller **onedir**，`--noconsole`，index.html 内嵌进 `_internal`）。**禁止 onefile**（启动解压慢、退出清理慢，用户明确拒绝过）
- 开发启动：`启动.bat`（`pyw -3 src\app.py`）；依赖安装：`安装依赖.bat`

## 目录结构

- `src/`：app.py、server.py、index.html、make_icon.py
- `assets/app_icon.ico`：打包图标
- `B站课程进度追踪/`：成品应用（onedir 产物）+ 全部运行时数据
- `build/`：打包中间产物（可删）
- `docs/screenshot.png`：README 截图，**必须用虚构数据生成**，禁止真实课程信息
- `hooks/pre-commit`：隐私拦截钩子（见下），已通过 `core.hooksPath` 启用

运行时数据目录（ROOT，全部在应用文件夹内，不往外写）：

| 文件 | 内容 | 隐私处理 |
|---|---|---|
| `.sessdata.bin` | B站 SESSDATA 凭据 | **Windows DPAPI（CurrentUser）加密 + base64**，仅当前 Windows 账户可解；旧明文 `.sessdata.txt` 读取时自动迁移并删除。也支持环境变量 `BILI_SESSDATA` |
| `tracked_videos.json` | 追踪列表 | **数据最小化**：落盘只存数字字段 `bvid/totalEpisodes/totalDuration/lastProgress/added_at/lastSynced/jumps`；标题、UP 主、封面、集数标题等描述性信息不落盘，每次启动实时从 B站拉取，内存 `_meta_cache` 缓存 |
| `course_data.json` | 集数缓存 | 同上，仅作离线兜底 |
| `covers/` | 封面图缓存 | 可删，会重新拉取 |

路径约定（server.py 顶部）：
- **ROOT**：frozen 时 = exe 所在目录；源码运行时 = `../B站课程进度追踪`（与打包版共用同一份数据）
- **FRONTEND_DIR**：frozen 时 = `sys._MEIPASS`（_internal）；源码时 = `src/`
- PyInstaller `--add-data`/`--icon` 相对路径是相对 `--specpath` 解析的，build.py 一律用绝对路径

## 隐私与安全红线（不可破坏）

1. **凭据**：SESSDATA 只在进程内存中用于请求 B站 API，不写日志、不回显；落盘必须走 DPAPI（`_dpapi_protect/_dpapi_unprotect`，ctypes 调 crypt32）。
2. **静态服务拒绝**：`.sessdata*`、点开头隐藏文件一律 403，HTTP 读不到凭据。
3. **本地接口防护**：每个请求过 `_guard()`——校验 `Host` 必须为 127.0.0.1/localhost（防 DNS rebinding），`Origin`/`Sec-Fetch-Site` 非法则 403（防 CSRF）。
4. **接口错误统一状态码**：`_err_result(kind, message)`，kind ∈ `network | sessdata | notfound | bili`，由 `_classify_err`/`_kind_from_msg` 自动归类。前端状态模块依赖此字段，新增接口错误必须带 kind。
5. **pre-commit 钩子**（`hooks/pre-commit`）：暂存区命中 `sessdata|tracked_videos|course_data|covers/|\.exe$|\.bin$|\.pyc$` 直接拒绝提交。
6. 桌面快捷方式 `Desktop\B站课程追踪.lnk` → 应用 exe（换图标功能依赖此文件名）。

## 前端架构要点（src/index.html）

### 统一状态模块 AppStatus（单一事实源）
- 所有接口只"汇报发生了什么"：`reportSyncStart/Ok/Retry/Fail(kind, ctx, msg)`，不自行决定 UI 文案。
- 状态集中持有：`online / sessdata(unknown|ok|missing|invalid) / syncPhase(idle|syncing|retrying|ok|error) / syncKind / syncContext(init|switch|auto|manual|online-wake) / hasLocal / metaReady`。
- UI 出口（状态行 refreshStatus、离线横幅 offlineBar、呼吸灯 syncDot、集数提示、添加弹窗 modal-status）全部 `onStatus(fn)` 订阅、自动派生显示。**新增提示出口时订阅状态，不要自己拼文案。**
- 派生规则要点：`notfound`（历史里扫不到，近期没看）不是故障——手动刷新才温和提示"播放30秒再刷新"，自动/切视频静默回退本地；离线横幅仅 init/online-wake 首次失败显示；呼吸灯重试排队中保持蓝色、彻底失败才红、后台静默同步不闪蓝。
- 钩子：`window.AppStatus / emitStatus / reportSync*`。

### 总百分比老虎机数字（多次踩坑，改动必读）
- 结构：`#bigPct` 内每位一个 `.dslot`（高 1em、overflow:hidden；`.ddot` 小数点宽 .3em）> `.droll`（flex 列，translateY 滚动）> 若干 `.dline`（高 1em）。
- `spinPct(str)`：变化位滚 `((to-from+10)%10)+10` 格。**步数必须是"差值 + 整十圈"**，否则落位错误（+12 曾导致 100.0 滚成 322.2）。
- **方向：数字从上往下落**：滚轮条 line k = `(to-k) 模 10`（目标数字在最上），初始 `translateY(-steps em)` 显示旧值，过渡到 `translateY(0)` 落位；缓动 `cubic-bezier(.22,.61,.36,1)`；位间延迟 `i*65ms` 实现左→右错峰；单轮 460ms。
- 首次渲染/位数不匹配（开机 0.0→44.9、9.0→100.0）：**按目标结构新建滚轮、数字位先置 0 再滚**，严禁 setPctInstant 直跳（否则开机永远看不到动画）。
- 触发：`pendingSpin` 标志（bootstrap、switchVideo、fetchProgress 成功置位）；`REDUCED_MOTION` 或 `body.scrubbing`（拖滑块）走 `setPctInstant` 直跳。
- **同步成功渲染坑**：必须先按 `j.progress` 设好 `pos`，`pendingSpin=true`，再调 `selectEp(page, true)`（keepPos 不清 pos）只渲染一次。**严禁 `selectEp(page, false)`**——它内部以 pos=0 先 render 一帧，数字瞬间掉到本集起点并清空滚轮 transform，打断动画。
- 进度环：`ring.style.strokeDashoffset` + CSS transition .7s（必须用 style，setAttribute 不触发过渡）。

### 100% 庆祝
- `maybeCelebrate()`：每次 render 都调用，**"完成态"与"首次庆祝"必须分离**——
  - 完成判定（最后一集 `pos >= duration-0.5`）为真时**始终**加 `.celebrate`（金色环）并 `startConfettiRain()`，切回已看完的视频也要恢复金色和碎屑；
  - 仅 12 片爆发纸屑受 `localStorage 'celebrated:'+bvid` 限制（每课程只爆发一次）；未完成时移除 `.celebrate`。
  - 反例（已修）：旧逻辑 localStorage 命中即整体 return，导致切视频时 playSwapIn 移除金色环后切回已完成视频不再恢复金色/彩带。
- `startConfettiRain(firstDelay)`：`.confetti.rain` 更小半透明、3.4~5.4s 慢落、每 0.65~1.6s 出 1 片偶尔 2 片，`document.hidden` 时跳过省电；首次庆祝传 1800ms（等爆发落完），切回恢复传 500ms；内部 timer 防重入。`playSwapIn()`（切视频）时 `stopConfettiRain()`。
- 钩子：`window.startConfettiRain/stopConfettiRain/spinPct/setPctInstant`。

### 其他动效（只动 transform/opacity）
- 同步呼吸灯 syncDot（tag-sync 内，文案 #tagSyncTxt）；切换视频 `.swap-in` 180ms 淡入；跳跃列表 `.stagger` 20ms 错开；同步成功 `playSheen()` 一次性流光——**仅总进度条** `.bar-track.sheen`（::after 白色半透渐变高光从左扫到右）。圆环流光已试验后**取消**：试过单段白弧（两端硬切僵硬）、三段固定透明度弧（三档台阶）、12 段 WAAPI 彗星尾迹（连续渐隐但头部在环起止处仍显突兀、SVG 弧线高光天然不如线性渐变自然），用户明确不满意，不要再加。
- Tab 栏悬停上浮：`.tabs` 需 `padding:8px 2px 12px` 留白，否则被 `overflow-x:auto` 容器裁切。
- 红线：`@media (prefers-reduced-motion: reduce)` 全停用；`body.doc-hidden`（visibilitychange）暂停循环动画。
- 标题栏按钮 `tabindex="-1"` + 无 `:focus` outline，启动/聚焦时 `document.body.focus()`，防白框；用 `aria-label` 不用 `title`。

### 跳跃/回看记录（server.py `record_jump_if_needed`）
- 三类判定：跨集向后 gap≥3、跨集向前 gap≥5、**集内同集进度倒退≥180 秒**（kind="time"）。
- **基准字段是 `lastSynced`（只存 B站同步值），不是 `lastProgress`**（后者会被手动拖条/切集覆盖）。手动保存进度**不触发**跳跃检测。
- 仅当 B站端 `view_at` 变新（真有新观看）才检测；B站历史静止时不报。
- B站 `progress=-1` 表示"本集看完"，不是倒退，不得判为回看。
- 完全相同的跳跃去重；加载时自动清理存量重复记录与残留在磁盘上的描述性字段（标题等）。
- 跳跃列表只显示当前视频；名字显示涉及的**集标题**（断网缺集数信息才回退课程名）。

### 同步与轮询
- B站请求走 `_https_get` 连接复用（threading.local 每线程每域名一条 HTTPSConnection，出错 drop 重建重试一次）；错误文案 `_friendly_err`（DNS/超时/证书）；code=-101 → needSessdata。
- **历史翻页协议**：游标在 `data.cursor.view_at`（秒级时间戳），下一页传 `view_at=<上页 view_at>`；旧 `max`/`data.page` 已失效（传了返回首页，曾导致只扫 60 条）。`fetch_progress` 必传 stop_bvid，命中即停（通常 1 页）。
- 自动同步间隔可配（localStorage `autoSyncMin`，5/10/15/30 分钟）；`fetchProgress` 有 `fpInFlight` 互斥防并发。
- 全量轮询 `pollAllVideos` 每 15 分钟刷非当前视频徽章（间隔 1.2s，needSessdata 时 break）；visibilitychange 回窗口距 lastSyncAt>10min 静默补同步。
- 启动静默同步失败退避重试 5/15/30s×5（needSessdata 除外），监听 window online 即时重试。开机横幅根因多为开机瞬间网络未就绪，属正常，会自动恢复。

### 窗口与交互（app.py）
- tkinter 独立线程启动画面（stop_evt 轮询关闭）；主窗口 `hidden=True` + `events.loaded` 后 show()；关 X 先 `api.hide_window` 再 `/api/quit`；`webview.start()` 后 `os._exit(0)` 秒关。
- **八向缩放**：JS 边缘热区 `.rz` setPointerCapture 只报方向 → `api.resize_edge(edge)` → Python 端 GetCursorPos+GetWindowRect（物理像素同坐标系）算"边缘=鼠标" → SetWindowPos 一次完成位置+尺寸。**严禁在 JS 端用 window.screenX/innerWidth 算目标尺寸**（几何滞后一帧，拖 w/n 边误差正反馈发散，窗口飞出屏幕）；pywebview 的 win.resize+FixPoint 同样不可用。hwnd 取法：`self._win.native.Handle.ToInt32()`（.NET IntPtr 不能直接 int()）。最小尺寸由 pywebview MinimumSize 在 WM_WINDOWPOSCHANGING 兜底。
- 空状态引导页：`body.empty` 隐藏 header/panel/footer。
- 弹窗打开锁定主界面滚动；toast 底部居中、z-index 3001、宽度 `min(62vw,520px)`；Tab 栏悬停滚轮可左右滚。
- 快捷键：← 上一集；→ "本集看完，跳下一集"（控制区只有"上一集"和"本集看完，跳下一集"两个按钮，原重复的"下一集"已删）。

## 环境教训

- Windows 11，Python 3.14（`py -3`），PyInstaller 6.22.2，pywebview/Pillow 已装。
- 沙箱禁止 Python 往安装目录写 `__pycache__/*.pyc`（PyInstaller 报 "hit restricted"）：`PYTHONDONTWRITEBYTECODE=1` + `py -3 -B` + `PYINSTALLER_CONFIG_DIR` 指项目内。
- PowerShell 执行策略可能禁止 .ps1；复杂文件操作用 Python 脚本而非内联 PowerShell（引号嵌套易出错）。
- 修改 index.html 后验证：提取 `<script>` 内容 `node --check` 语法；**动效类改动必须调用工作区 skill `ui-motion-verify`**（`.trae/skills/ui-motion-verify/SKILL.md`：pywebview 真实窗口 + PrintWindow 截图 + evaluate_js 采样 transform/opacity，含可复用脚本骨架和采样踩坑），不要只靠静态分析或"代码看起来对"——本项目动效坑（数字落位错、动画被中间帧打断）全是实测才发现的。
- 工作区 skill 目录 `.trae/skills/`：若提交 GitHub 共享则保留；不共享则将 `.trae/` 加入 .gitignore。

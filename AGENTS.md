# AGENTS.md —— AI 开发协作约定与项目记忆

> **给 AI 助手的硬性规则（每次会话必读、必遵守）：**
>
> 1. **开始动手前**：通读本文件，再读对应源码。不要凭猜测修改。
> 2. **完成改动后**：如果改动涉及架构、约定、踩坑经验、**协作流程经验**或本文件记录的任何条目，**必须同步更新本文件**（新增/修正/删除过时内容）。这是项目记忆，随仓库转移，目的是让下一位 AI 不重复踩坑、不重复犯同样的协作错误。
> 3. **只写稳定知识**：目录结构、关键约定、协议细节、踩坑教训、**协作流程经验**、红线规则。不写临时进度、日志、一次性对话内容。协作流程经验指：经用户确认的工作方式约定（如"现象清晰时不做无意义复现"），这类约定和技术踩坑同等重要，必须沉淀。
> 4. **隐私红线**：严禁把 SESSDATA、真实观看记录、真实课程标题/UP 主、cookie、个人路径等写进本文件或任何会提交的文件。举例一律用虚构数据（如"高等数学基础班 · BV1DemoMath01"）。
> 5. **与用户沟通**：使用中文；先给方案/分析，用户确认后再动手；不要使用"（推荐）""最快上手（3 步）"这类营销腔。
> 6. **bat 脚本必须纯 ASCII**（GBK cmd 下中文乱码）；中文文件名操作放 build.py。
> 7. 打包命令：`py -3 scripts\build.py`（onedir + --noconsole；应用运行中自动关闭——先 taskkill 请求正常退出、卡住才强杀；打包完成后 `os.startfile` 自动启动新版本）。沙箱环境打包需 `PYTHONDONTWRITEBYTECODE=1`，`PYINSTALLER_CONFIG_DIR` 指向项目内 `build/pyi-cache`。

---

## 项目概览

B站课程观看进度追踪桌面应用，纯本地运行，通过 B站历史接口实时读取观看进度，支持多课程追踪、跳跃/回看检测、100% 庆祝动效、App 扫码登录（官方 WEB 二维码通道，手动粘贴 SESSDATA 兜底）。两端功能保持一致：无边框窗口 + 页面自绘深色标题栏 + 拖动 + 双击最大化 + 八向缩放。

- 后端：`src/server.py`（http.server，仅绑定 127.0.0.1，端口 8765，被占自动向后回退）
- 前端：`src/index.html`（单文件，原生 JS + CSS，无框架）
- **唯一桌面入口 `src/app.py`**：平台分支内聚在同一文件、注释分隔——Windows 走 pywebview(WebView2)（启动画面、Win32 边缘缩放），Linux 走 GTK3 + 系统 WebKit2GTK 4.1（详见下文「Linux 原生窗口」）
- Windows 打包：`scripts/build.py` + `scripts/build.bat`（PyInstaller **onedir**，`--noconsole`，index.html 内嵌进 `_internal`）。**禁止 onefile**（启动解压慢、退出清理慢，用户明确拒绝过）
- Windows 开发启动：`scripts\run.bat`（`pyw -3 src\app.py`）；依赖安装：`scripts\install.bat`
- **Linux 启动**：`bash scripts/run.sh`（原生无边框窗口）。系统库 `sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1`；Python 依赖（keyring pillow qrcode）缺失时自动装到项目内 `.pydeps/` 并设 `PYTHONPATH`（不污染系统 Python，规避 PEP 668）。**数据目录是 XDG `~/.local/share/bili-course-tracker/`**（不再是项目根下中文目录；/opt 与源码目录都不该写运行时数据）。`.pydeps/` 已加入 .gitignore。
- **Linux 打包**：`python3 scripts/build.py` 产出**源码 deb**（不跑 PyInstaller，133K vs 42M）——源码装 `/opt/bili-course-tracker/`，`/usr/bin/bili-course-tracker` 为启动脚本，运行时依赖走 control 的 `Depends`（python3-gi、gir1.2-gtk-3.0、gir1.2-webkit2-4.1、python3-keyring、python3-pil、python3-qrcode），apt 自动配齐。**版本号自动递增**：实际版本 = `1.0.0+N`（构建号存 build/.buildnum，每次打包 +1），apt 永远识别为升级、直接安装即覆盖——禁止再用固定版本号（会被 apt 判"已是最新"跳过，不得不 --reinstall）。打包同时生成 `build/install.sh` 一键脚本；交互终端打包后按回车即可本机安装。

## 目录结构

- `src/`：app.py、server.py、index.html、make_icon.py
- `scripts/`：build.py（一键打包）+ run/build/install（.bat/.sh），全部 ASCII 文件名；脚本内先 `cd` 回项目根再执行
- `assets/app_icon.ico`：打包图标
- `build/`：打包中间产物（可删，最终 deb 也在此）
- `docs/screenshot.png`：README 截图，**必须用虚构数据生成**，禁止真实课程信息
- `hooks/pre-commit`：隐私拦截钩子（见下），已通过 `core.hooksPath` 启用

运行时数据目录（ROOT，全部在应用文件夹内，不往外写）：

| 文件 | 内容 | 隐私处理 |
|---|---|---|
| `.sessdata.bin` | B站 SESSDATA 凭据 | **跨平台 keyring**（Windows DPAPI / macOS Keychain / Linux Secret Service）；keyring 不可用时回退到 `.sessdata.bin` 明文 + `chmod 600`。旧 DPAPI 的 `.sessdata.bin` 与旧明文 `.sessdata.txt` 在首次读取时自动迁移到 keyring。也支持环境变量 `BILI_SESSDATA` |
| `tracked_videos.json` | 追踪列表 | **数据最小化**：落盘只存数字字段 `bvid/totalEpisodes/totalDuration/lastProgress/added_at/lastSynced/jumps/is_season/season_id`；标题、UP 主、封面、集数标题等描述性信息不落盘，每次启动实时从 B站拉取，内存 `_meta_cache` 缓存 |
| `course_data.json` | 集数缓存 | 同上，仅作离线兜底 |
| `focus.json` | 番茄钟专注统计 | **数据最小化**：只存 `{"date": "YYYY-MM-DD", "seconds": N}`，按天累计、跨天自动清零；已加入 .gitignore 与 pre-commit 拦截 |
| `.focus_session.json` | 番茄钟运行态 | `{state(running|paused), base, started_at, updated_at}`：开始/暂停/继续即时落盘、运行中每 15s 心跳更新 updated_at；点开头 HTTP 404。正常结束/关闭时原子结算后删除；残留（kill/崩溃/断电）在下次启动 GET /api/focus 时自动恢复入账（running 封口不晚于最后心跳+60s，paused 按 base 全额），前端 toast 告知 |
| `covers/` | 封面图缓存 | 可删，会重新拉取 |

路径约定（server.py 顶部）：
- **ROOT**：优先级 = 环境变量 `BILI_TRACKER_ROOT`（Linux app.py 在 import server 前设置为 XDG 目录）> Windows frozen 时 exe 所在目录 > Windows 源码运行时 `../B站课程进度追踪`（与打包版共用同一份数据）> Linux frozen 且 exe 目录不可写时 XDG 回退
- **FRONTEND_DIR**：frozen 时 = `sys._MEIPASS`（_internal）；源码时 = `src/`
- `/api/icon/file`：先读 ROOT 下 `app_icon_preview.png`（用户换过的图标），无则回退 `<项目根>/assets/app_icon.ico`（PIL 转 PNG）——数据目录迁移后 XDG ROOT 里没有 preview 文件，不回退就会 404 变黑
- PyInstaller `--add-data`/`--icon` 相对路径是相对 `--specpath` 解析的，build.py 一律用绝对路径

## 隐私与安全红线（不可破坏）

1. **凭据**：SESSDATA 只在进程内存中用于请求 B站 API，不写日志、不回显；落盘走**跨平台 keyring**（`keyring` 库，自动适配 Windows DPAPI / macOS Keychain / Linux Secret Service），keyring 不可用时回退到 `.sessdata.bin` 明文 + `chmod 600`。`_kr_available()` 只检测库和后端是否存在，**实际读写 `_store_get`/`_store_set` 必须 try-except**——Linux 无 D-Bus/Secret Service 时 `keyring.get_password` 会抛 `InitError`，必须回退明文文件。**Linux 必须复用持久 SecretService 客户端**（`_get_kr_persistent()` 全局单例）+ 凭据值缓存（`_sessdata_cache`）：keyring SecretService 后端每次调用都新建并立即销毁 D-Bus 连接，gnome-keyring 在异步属性读取途中遇客户端断开会触发 GIO 断言（`gdbusconnection.c invoke_get_property_in_idle_cb: error != NULL`）SIGABRT，表现为每次启动都弹"内部错误"。旧版 DPAPI 的 `.sessdata.bin` 与旧明文 `.sessdata.txt` 在 `get_sessdata()` 首次调用时自动迁移到 keyring。`write_sessdata()` 写入后清理旧文件（keyring 可用时删 `.sessdata.bin`+`.sessdata.txt`，不可用时只删 `.sessdata.txt`）。
2. **静态服务拒绝**：`.sessdata*`、点开头隐藏文件、含 `..` 的路径一律返回 **404**（不是 403——404 连"文件是否存在"都不暴露，更稳妥），HTTP 读不到凭据。
3. **本地接口防护**：每个请求过 `_guard()`——校验 `Host` 必须为 127.0.0.1/localhost（防 DNS rebinding），`Origin`/`Sec-Fetch-Site` 非法则 403（防 CSRF）。
4. **接口错误统一状态码**：`_err_result(kind, message)`，kind ∈ `network | sessdata | notfound | bili`，由 `_classify_err`/`_kind_from_msg` 自动归类。前端状态模块依赖此字段，新增接口错误必须带 kind。
5. **pre-commit 钩子**（`hooks/pre-commit`）：暂存区命中 `sessdata|tracked_videos|course_data|focus\.json|covers/|\.exe$|\.bin$|\.pyc$` 直接拒绝提交。
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
- `spinPct(str, el, minSteps, coordinated)`：变化位滚**精确差值** `((to-from+10)%10)` 格（2→3 滚 1 格、4→8 滚 4 格，用户指定"精确滚动"，2026-09 从"差值+整十圈"改来）；`minSteps` 仅切换视频时传 3——当精确差值 < 3 时 steps+=10 多滚一圈，保证来回切换进度接近的视频时也有足够动画时长；同步更新/首次加载不传（minSteps=0，保持精确差值）。`coordinated` 默认 true——百分比用"一位变、全体转"（44.0→45.0 时十分位也滚，视觉协调）；**番茄钟传 false，只滚变化的位**（25:00→24:59 只有秒位和分钟位滚，小时位不动）。时长按格数缩放 `110+55*格数` 封顶 460ms（单格 165ms 利落翻页，实测美观成立）。落位正确性靠**行序公式**而非步数：滚轮条 line k = `(to-k) 模 10`（line[0]=目标数字），初始 `translateY(-steps em)` 显示旧值、过渡到 0 必落目标——**html 行数必须与 steps 严格一致**（旧坑：步数与行数不匹配曾致 100.0 滚成 322.2）。**新建滚轮=整圈滚（2026-09 用户要求"滚满"，勿回退成按差值滚几格）**：大百分比（bigPctMode=!el，renderPct 不传 el）滚 `to+10` 格=从 0 起整圈+差值落位；其他容器（番茄钟进入，textContent 已置真实值）每位滚 10 格=从当前显示值起无跳变整圈落位。**steps=0 时的分支**：① 有其他位在转（hasAnySpin，仅 coordinated=true 预检）或 minSteps>0 → 强制 10 步；② 真静止 → 跳过。hasAnySpin 在执行循环前预检，coordinated=false 时不预检。
- **方向：数字从上往下落**：初始 `translateY(-steps em)` 显示旧值，过渡到 `translateY(0)` 落位；缓动 `cubic-bezier(.22,.61,.36,1)`；位间延迟 `i*stag` 实现左→右错峰。**错峰模式（stag>0）全体统一时长**（2026-09 用户要求"顺序启停、动画连贯"：先按各 位算步数，取最大格数统一 `dur=min(460,110+55*maxSteps)`，保证停止顺序=启动顺序=左→右；同时模式 stag=0 维持各滚各的 `110+55*steps`）。**延迟按"数字位序号"（rollIdx）而非字符序号**（2026-09 用户实测踩坑：分隔符占拍会让波浪在冒号/小数点处产生 130ms 停顿、断续不流畅；rollIdx 只对滚动的数字位 +1，间隔均匀 65ms）。实现上 spinPct 是两遍循环：第一遍 `plan` 算各 位步数不动 DOM，第二遍建滚轮。
- **错峰/同时分场景（2026-09 用户定稿）**：**实时数据更新用"同时起跳"（间隔 0）**——番茄钟每秒 tick（`spinPct` 第 5 参显式传 0）、同步成功进度有变（`fetchProgress` 成功回调里 `context !== 'switch'` 时设一次性 `nextSpinStagger = 0`）；**展示型开场用错峰**（常量 `SLOT_STAG=65ms`）——切换视频（minSteps=3）、首次加载、番茄钟进入与退出（2026-09 用户指定"计时器进入和出都要错峰"：进入 = start() 直接调 `spinPct(fmt(todaySec), focusTime)` 错峰整圈滚——曾被实时同帧改成交互后修正，勿再让 start 走 render 的同时路径；且 spinPct 后必须**实测读各滚轮 `transitionDelay+duration` 取最大值 + 100ms 写入 `spinHoldUntil`，期间 `render()` 跳过数字滚动**（2026-09 用户实测踩坑：时钟偏差/秒位进位会让 start 里的首次 render 或第一秒 tick rebuild 滚轮，把开场动画拦腰打断；stop() 时复位 spinHoldUntil=0）；退出 = stop() 填 20 行循环滚时给每个 `.droll` 设 `animationDelay = idx*65ms` 左→右依次起滚，延迟期滚轮静止在基础态无跳变）。`spinPct` 第 5 参 `staggerMs` 显式值 > `nextSpinStagger`（一次性，消费即复位）> `SLOT_STAG`。错峰机制（统一时长后）：左→右依次启动、依次停稳。调试滑块/`setSpinStagger` 对比工具已按用户要求移除（2026-09 定稿后），勿再加回。估算每秒 tick 走 `setPctInstant` 直跳不经过滚轮，不受此规则影响。
- 首次渲染/位数不匹配（开机 0.0→44.9、9.0→100.0）：**按目标结构新建滚轮、大百分比从 0 起显滚 `to+10` 整圈落位**，严禁 setPctInstant 直跳（否则开机永远看不到动画）。
- **断网首启无集数也得滚**：`render()` 原来 `if(!e) return`、`selectEp` 原来 `if(!eps.length) return`——无 episodes 时直接返回，导致首次打开程序（无 SESSDATA、episodes 暂缺）时 `renderPct` 不被调用、bigPct 保持纯文本 "0" 不滚动。修法：两处都去掉早退，无 `e`/无 eps 时用 `active.lastProgress.progress` 兜底设 `pos`、算 `played`/`pct`，仍调 `renderPct(pct)`；集数标题回退"集数信息加载中…"。
- 触发：`pendingSpin` 标志（bootstrap、switchVideo、fetchProgress 成功置位）；`REDUCED_MOTION` 或 `body.scrubbing`（拖滑块）走 `setPctInstant` 直跳。
- **同步成功渲染坑**：必须先按 `j.progress` 设好 `pos`，`pendingSpin=true`，再调 `selectEp(page, true)`（keepPos 不清 pos）只渲染一次。**严禁 `selectEp(page, false)`**——它内部以 pos=0 先 render 一帧，数字瞬间掉到本集起点并清空滚轮 transform，打断动画。
- **切视频动画时序坑**：`switchVideo` 里 `renderAll`（本地缓存）和 `fetchProgress` 成功回调（最新进度）都会触发 `renderPct`。**修法**：`switchVideo` 里 `renderAll` 之前设 `pendingSpin=true, spinMinSteps=3`，用本地缓存值播一次动画（保证切换一定有动画，不依赖网络）；fetchProgress 成功回调里比较 B站返回的 page/progress 与本地缓存（`cur`/`pos`），**只有进度真正变化时才设 `pendingSpin=true` 并调 `selectEp` 播第二次，否则跳过 selectEp**（避免 setPctInstant 打断正在播放的动画）。spinMinSteps 在 switch 上下文为 3，其他为 0。**`playSwapIn()` 必须在 `renderAll()` 之前调用**——playSwapIn 会移除金色环和彩带，如果在 renderAll 之后执行，会把 maybeCelebrate 刚恢复的金环又删掉（切回已完成视频时金环/彩带消失）。
- **_restoreLocalProgress 打断动画坑**：fetchProgress 失败（无 SESSDATA/网络错误）时会调 `_restoreLocalProgress()` → `selectEp` → `render` → `renderPct`，但该路径**不设 pendingSpin**，renderPct 走 `setPctInstant` 重建 DOM，会打断 switchVideo/bootstrap 中 renderAll 正在播放的老虎机动画（表现为"切视频/首次打开有几率不动"）。**修法**：fetchProgress 失败分支中 `if(context !== 'switch' && context !== 'init') _restoreLocalProgress()`——switch/init 上下文 renderAll 已用本地缓存渲染，无需再恢复。
- 进度环：`ring.style.strokeDashoffset` + CSS transition .7s（必须用 style，setAttribute 不触发过渡）。

### 100% 庆祝
- `maybeCelebrate()`：每次 render 都调用，**"完成态"与"首次庆祝"必须分离**——
  - 完成判定（最后一集 `pos >= duration-0.5`）为真时**始终**加 `.celebrate`（金色环）并 `startConfettiRain()`，切回已看完的视频也要恢复金色和碎屑；
  - 仅 12 片爆发纸屑受 `localStorage 'celebrated:'+bvid` 限制（每课程只爆发一次）；未完成时移除 `.celebrate`。
  - 反例（已修）：旧逻辑 localStorage 命中即整体 return，导致切视频时 playSwapIn 移除金色环后切回已完成视频不再恢复金色/彩带。
- `startConfettiRain(firstDelay)`：`.confetti.rain` 更小半透明、3.4~5.4s 慢落、每 0.65~1.6s 出 1 片偶尔 2 片，`document.hidden` 时跳过省电；首次庆祝传 1800ms（等爆发落完），切回恢复传 500ms；内部 timer 防重入。`playSwapIn()`（切视频）时 `stopConfettiRain()`。
- 钩子：`window.startConfettiRain/stopConfettiRain/spinPct/setPctInstant`。

### 其他动效（只动 transform/opacity）
- 同步呼吸灯 syncDot（tag-sync 内，文案 #tagSyncTxt）；切换视频 `.swap-in` 180ms 淡入；跳跃列表 `.stagger` 20ms 错开；同步成功 `playSheen()` 一次性流光——**只有直线条 `.bar-track.sheen`**（::after 白色渐变高光从左扫到右，0.9s ease-out）；`fetchProgress` 的 `switch` 上下文不播流光（切换已有 .swap-in 淡入，紧跟流光杂乱）。**圆环流光已彻底移除（2026-09 最终决定，不要再加）**：先后尝试实心白弧（"一根白柱子"）、锥形渐变彗星、90° 长尾多级渐变，均达不到预期——conic 亮区无论怎么调，运动时要么像方块、要么尾迹不可见。`.ring-sheen` / `@keyframes ringSheen` / `#ringSheen` 元素 / playSheen 圆环分支均已删除。
- Tab 栏悬停上浮：`.tabs` 需 `padding:8px 2px 12px` 留白，否则被 `overflow-x:auto` 容器裁切。
- 红线：`@media (prefers-reduced-motion: reduce)` 全停用；`body.doc-hidden`（visibilitychange）暂停循环动画。
- 标题栏按钮 `tabindex="-1"` + 无 `:focus` outline，启动/聚焦时 `document.body.focus({preventScroll:true})`，防白框且避免聚焦把页面拉回顶部；用 `aria-label` 不用 `title`。
- **标题栏必须 `position:fixed`，不能用 `sticky`**：WebView2/Blink 下 `body{overflow-y:auto}` 的实际滚动由 `html`（`document.scrollingElement`）承载，标题栏的 `sticky` 相对 body 计算，body 随 html 整体滚动，导致标题栏跟着滚走。修法：标题栏改 `position:fixed; top:0; left:0; right:0`，同时 `body{padding-top:38px}` 让出标题栏高度。固定定位永远相对视口，不随滚动移动。
- **最小化恢复保持滚动位置（WebView2 坑）**：窗口最小化时宿主会把 WebView2 视口高度压到接近 0，浏览器据此把 `scrollTop` 钳制/清零，恢复后原位置丢失。修法：监听 `resize`，视口高度 `< 50px` 时记下 `scrollTop`，恢复到正常高度后用 `requestAnimationFrame` 还原（`preserveScrollOnMinimize` IIFE）。阈值 50px 远小于最小窗口高度 560，不会误判正常缩放。

### AI 估算模式（集内进度时间差推算）
- **背景**：B站历史接口的 `progress` 有上报延迟（心跳15秒/次，聚合可能更久），无法拿到实时播放进度。用 `view_at`（上报时间戳）+ 时间差推算当前集内进度。
- **触发条件**：`progress + (now - view_at) < 当前集时长` → 上报时的进度 + 过去了多久，还没超过这集总时长，认为用户大概率还在看这集。不依赖跨集检测，单集即可判断。**额外限制：必须两次刷新间 `view_at` 有更新**（`lp.view_at > prevViewAt`）才进入估算——若 `view_at` 不变说明用户可能暂停了，此时不估算（避免进度虚涨）。`prevViewAt` 变量记录上一次的 view_at。
- **切换/添加视频后误触发估算坑（已修，勿回退）**：`prevViewAt` 是视频级状态，**切换视频时必须重置为 0**（`switchVideo` 里 `stopEstimate()` 后紧跟 `prevViewAt = 0`），否则新视频的 `view_at` 会和上一个视频（或初始 0）比较，只要新视频近期看过就被判为"连续观看"而误开估算。`checkEstimate` 中 `prevViewAt === 0` 表示"切换后第一次刷新/首次加载"，只记录基准 `prevViewAt = lp.view_at` 不启动估算（`forceEstimate` 调试模式除外），等下一次刷新 `view_at` 真正变大才估算。`bootstrap` 首次加载也走此分支（初始 `prevViewAt = 0`），避免打开应用即误开。
- **调试功能集中在设置弹窗"调试"分区**（2026-09 用户要求，勿再放主界面）：`#btnDebugEst`（原在"从B站读取进度"按钮下方，已移入），点击强制切换 `forceEstimate` 标志并 `startEstimate()/stopEstimate()`，绕过 `view_at` 更新检查，用于验证动效。数字错峰对比滑块已随定稿移除（见老虎机章节）。`stopEstimate()` 时会重置 `forceEstimate` 并恢复按钮文字。调试开启时用 `debugBasePos`（当前 pos）/`debugBaseTime`（当前时间戳）作模拟基准，进度从当前位置每秒 +1 往前走（不篡改真实 `lastProgress`）；直接用真实 `view_at` 会因上报时间过旧直接顶到集末尾，看不出走动效果。
- **运行逻辑**：进入估算模式后每秒 `pos = min(baseProgress + (now - baseViewAt), 该集时长)`，同时更新集数滑块（`posSlider.value` + `--p`）并 `render()`——`render()` 本身不更新滑块（滑块只在 `selectEp` 时同步），必须在 timer 里手动同步，否则估算时只有总百分比/环在动、滑块不动。总百分比/进度环/总进度条/集数滑块四处数据同源、每秒一起走。
- **退出条件**：切换视频、手动拖动进度条、手动切集（上一集/下一集/选集）时 `stopEstimate()`。
- **视觉标识**（`body.estimating` class 控制，**保持原有渐变风格只换色调**）：
  - 窗口四周青色扩散波纹（`.ai-ripple` 三个 span，**必须用 `box-shadow: inset` 内阴影**：从边缘 1px 亮青线 `inset 0 0 0 1px` 扩散到 `inset 0 0 26px 14px` 淡出，错峰 0.9s，2.8s 循环）。**外扩 box-shadow 不可见**——span `inset:0` 占满视口，向外的 spread 全投到视口/屏幕外被裁掉；更早的 `border+scale` 也因 body overflow 裁切失败。只有 inset 方向（向窗口内扩散）肉眼可见。
  - **接管动画（进入估算时一次性 0.9s）**：青色覆盖层从 0 点增长到当前进度位置，逐渐替换原色条；原蓝紫条全程不动、只在覆盖层下方静默同步，中途呈现"前段青、尾端原色"的替换效果。三处实现不同：①进度环——SVG 加第二个圆 `#ringBarEst`（class `.bar-est`，stroke 固定 `url(#gEst)`；**不要复用 `.bar`**，否则会被 `.ring.celebrate/.focus-ring .bar` 改色规则波及），进入时 dashoffset 从周长 C 过渡到当前值；②总进度横条——`.bar-track`（已有 position:relative）内加绝对定位 `#overFillEst`，width 0→集数占比；③集数滑块——input range 无法插子元素，用多层 background（青层 `--pe` 压在原色层 `--p` 上），**`@property --pe{syntax:'<percentage>'}` 注册后自定义属性才能 transition**，JS 设 0%→当前%。函数 `playEstimateTakeover()`；`takeoverUntil` 时间戳保证接管动画期间 renderPct/render/timer 的数据同步不打断动画；动画结束 setTimeout 校准终点并清过渡，之后覆盖层每秒直跳跟随（变化 <0.1% 无需过渡）。
  - **回退动画（取消估算时一次性 0.9s，接管的反向）**：`stopEstimate()` 不立即移除 body class，而是置 `estClosing=true` + body 加 `est-closing` class、清数据 timer，三处覆盖层反向过渡回 0（环 dashoffset→C、横条 width→0、滑块 --pe→0%，缓动 `cubic-bezier(.42,0,1,1)` ease-in）；`body.est-closing` 下波纹容器 opacity 0、底部文字隐藏（CSS 选择器用 `body.estimating:not(.est-closing)` 排除），但覆盖层保持可见直到缩回完成。0.9s 后 `_finishEstClose()`（`closingTimer`）才移除 estimating/est-closing class 并复位，底色条无缝露出。回退期间 renderPct/render/timer 的覆盖层同步条件全部加 `!estClosing`；回退中又满足估算条件（如自动同步返回）则 `startEstimate()` 用 `wasClosing` 跳过 `if(estimating) return` 守卫、取消 closingTimer 重播接管（`playEstimateTakeover` 里必须清 `ringEst.style.opacity=''`）。REDUCED_MOTION 时回退时长 0 直接复位。
  - **回退终点残留坑（已修，勿回退）**：两个根因——①缓动不能用末端减速的 `cubic-bezier(.55,.06,.4,.95)`，青弧最后 100ms 在顶部慢速爬行像残留，改 ease-in 末端加速收走；②`.bar-est` 是 `stroke-linecap:round`，dashoffset 收到 C（弧长 0）时两端圆头重合为一个 14px 青色圆点，要挂到 class 移除（960ms）才消失。修法：回退时 ringEst 的 transition 串附加 `opacity 140ms ease-in 760ms`（前 760ms 保持实色、最后 140ms 快速淡出盖住圆点）并设 inline opacity:0；`_finishEstClose` 复位时清掉 inline opacity 交还 class 控制。
  - 估算色调 = 青蓝渐变 `#38e0ff → #4f8fff`（原色是蓝紫 `#6ea8ff → #8b5cf6`，方向/结构相同只换色调，禁止用纯色覆盖；渐变定义在 SVG `<defs>` 的 `#gEst`，与 `#g` 同结构）。估算时底色条不再换色，青色只存在于覆盖层；thumb 只换边框色，保持白底细边原样。注意不要给"今日专注"按钮变色——它是 primary 渐变按钮不是进度条。
  - 窗口底部居中常驻文字"AI 估测时间中"（`.ai-estimating-label`，圆角胶囊，蓝色）。
- **关键函数**：`checkEstimate()` / `startEstimate()` / `playEstimateTakeover()` / `stopEstimate()` / `_finishEstClose()`，变量 `estimating` / `estTimer` / `prevViewAt` / `forceEstimate` / `debugBasePos` / `debugBaseTime` / `takeoverUntil` / `estClosing` / `closingTimer`。

### B站合集（ugc_season）合并追踪
- **背景**：B站"合集"是 UP 主将多个**独立 BV** 视频整理成的一组（区别于同一 BV 的多P）。原程序把每个 BV 当独立追踪条目，无法统一算总进度/切集。
- **交互流程**：添加视频时，`fetch_video_info` 检测 B站 view 接口返回的 `ugc_season` 字段；若存在，`/api/tracked/add` 返回 `{ok:true, need_confirm:true, season:{season_id,title,ep_count}}`，前端在该历史条目行内展开两个按钮"仅追踪此视频"/"合并合集(N集)"由用户选择（**不再弹二次确认框**，避免打断浏览历史列表）。选合并→ `merge_season:true`；选仅追踪→ `merge_season:false`。
- **合并实现**：`fetch_season_archives(season_id, sessdata, ref_bvid)` 调 view 接口取 `ugc_season.sections[].episodes` 展平为合集的 episodes（**不调额外合集接口**，season/info 接口 404 且 wbi 签名不必要）。每集带自己的 `bvid`，条目标识 bvid 用首集 bvid。**合集封面用首集视频的 `pic` 字段**（`ugc_season.cover` 常为纯黑合集封面图，体验差）。
- **episodes 结构统一**：多P视频和合集的 episodes 每集都有 `bvid` 字段——多P视频所有集 bvid 相同；合集每集 bvid 不同。**合集每集的 `page` 是合集内集序号（1,2,3...），不是视频内部分P page**（合集每集都是独立视频，内部 page 恒为 1，直接用会导致下拉框全显示"第1集"）。前端 `curBvid()` 取当前集的 bvid 用于跳转/打开B站。
- **进度同步**：`fetch_progress(sessdata, bvid, episode_bvids)` 对合集传入所有集的 bvid 列表，在历史里搜索任一命中（不设 stop_bvid），取 view_at 最新的，再把命中的 bvid 映射回合集内集序号作为 `page` 返回。
- **合并冲突处理**：合并时若合集内某些视频已被单独追踪，自动移除这些单视频条目，并迁移最新一条的 `lastProgress`（page 映射为合集集序号）。
- **落盘字段**：`is_season`（bool）和 `season_id` 落盘；`_enrich_video` 对合集条目调 `fetch_season_archives` 而非 `fetch_video_info`。
- **wbi 签名**（已实现但当前未使用）：`wbi_sign()` + `_get_wbi_keys()` 从 nav 接口取 img/sub_key，固定重排表混 mixin_key，md5 签 w_rid。后续若需调 wbi 接口（如 seasons_archives_list）可直接用。

### 跳跃/回看记录（server.py `record_jump_if_needed`）
- 三类判定：跨集向后 gap≥3、跨集向前 gap≥5、**集内同集进度倒退≥180 秒**（kind="time"）。
- **基准字段是 `lastSynced`（只存 B站同步值），不是 `lastProgress`**（后者会被手动拖条/切集覆盖）。手动保存进度**不触发**跳跃检测。
- 仅当 B站端 `view_at` 变新（真有新观看）才检测；B站历史静止时不报。
- B站 `progress=-1` 表示"本集看完"，不是倒退，不得判为回看。
- 完全相同的跳跃去重；加载时自动清理存量重复记录与残留在磁盘上的描述性字段（标题等）。
- 跳跃列表只显示当前视频；名字显示涉及的**集标题**（断网缺集数信息才回退课程名）。

### 扫码登录（server.py `qrlogin_*` + index.html `QrLogin`/`makeQRMatrix`）
- 走 **WEB 通道**（`passport.bilibili.com/x/passport-login/web/qrcode/{generate,poll}`，source=main-fe-header），**不是 TV 通道**——WEB 拿到的是网页端 cookie 会话，在 B站侧只新增一条"网页登录记录"（可在手机端登录设备管理随时下线），不占手机/TV 端设备名额；TV 通道才会作为一台云视听设备挂进设备列表。
- 后端：`POST /api/qrlogin/start` 用**独立 opener + `http.cookiejar`**（generate 会 Set-Cookie buvid3 等，poll 必须带同一套）+ 统一浏览器 UA（与后续历史接口同一画像）；`qrcode_key` 只存进程内存（`_qr_state`，180 秒有效，进程退出即消失），**不落盘**。`_qr_state` 同时存 `jar`（CookieJar 引用）和 `opener`——**`urllib.request.build_opener()` 返回的 `OpenerDirector` 没有 `.cookiejar` 属性**，cookiejar 挂在 `HTTPCookieProcessor` handler 上，必须显式存 jar 引用，不能调 `opener.cookiejar`（会 AttributeError 导致 poll 崩溃断连）。响应回传 `{url, matrix}`——matrix 是 `qrcode` 库生成的 0/1 二维数组（含 4 模块静区），前端直接画 canvas 不再前端编码。`GET /api/qrlogin/poll` 透传状态：86101 wait / 86090 scanned / 86038 expired（过期清状态）/ 0 成功——成功时 SESSDATA 有两处来源：① `data.url` query 参数（旧版通道）② cookie jar（新版通道，B 站通过 Set-Cookie 下发），**必须两处都查**。复用 `write_sessdata()` 走 DPAPI 落盘并立即清状态，响应只回 `{status:"success"}`，凭据不进日志、不进响应体。do_GET/do_POST 必须加顶层 try-catch（server 崩溃会断连，前端拿到 expired 遮罩而非真实错误）。
- 前端 `QrLogin` IIFE：点"扫码登录"→ start → `drawQR(canvas, j.matrix)` 出码 → **2 秒一轮** poll；单次网络抖动静默等下一轮，后端返回 no-session/expired 则停轮询并弹遮罩（遮罩可点、内含圆形刷新图标按钮，均触发 `QrLogin.begin()` 重新 start）；成功后 toast、`AppStatus.sessdata='ok'`、关弹窗、有追踪视频则 `fetchProgress('manual')` 否则引导添加课程。关/开设置弹窗必须 `QrLogin.reset()` 停轮询。手动粘贴 SESSDATA 入口**保留作兜底**（扫码被风控时还有路走）。
- **二维码过期遮罩设计**：遮罩 `#qrMask` 绝对覆盖 `#qrCanvas`，背景 `rgba(10,16,36,.55)` + `backdrop-filter:blur(3px)` → 底下 canvas 二维码轻度模糊可见（虚化效果）。内容为**图标和文字并排**：刷新圆圈箭头 SVG（`id="qrRefresh"`，白色描边，20×20px）+ "二维码已失效"白色文字，flex-direction 默认 row（并排），gap:10px。整体黑白灰风格，无多余装饰。遮罩整体可点击刷新，SVG 单独绑定 click + `stopPropagation` 防冒泡。**关键 id `qrMask`/`qrMaskTxt`/`qrRefresh` 不可删**（JS 绑定了显隐和点击）。
- **设置弹窗 SESSDATA 区域布局**：从上到下依次为 `sessdataStatus`（状态文字，始终可见）→ `sessdataLogout`（退出登录按钮，仅 configured 时显示）→ `<details>` 折叠区包裹手动粘贴 input + toggle + save（默认折叠，点击"手动粘贴 SESSDATA（备用）"展开）。**所有关键 id 必须保留**（JS 绑定了显隐和点击）。
- **登出**：设置弹窗"退出登录"按钮（仅 SESSDATA 已配置时显示），`POST /api/sessdata/logout` → `delete_sessdata()` 删除 `.sessdata.bin` 及旧明文 `.sessdata.txt` → 前端 `AppStatus.sessdata='missing'` + toast + 关弹窗。与保存接口风格一致，走 `_wlock`。
- **二维码编码由后端 Python `qrcode` 库生成**（`_make_qr_matrix`：ECC-M、border=0 后手动加 4 模块静区、返回 `[[0/1,...],...]`）。**手写 JS 编码器 `makeQRMatrix` 已弃用**——手写编码器有版本信息 BCH、格式信息掩模等系统性 bug（pyzbar 无法解码），与标准库对比 372 处差异；node 反向解码自检"通过"是因为解码和编码犯了同样的错误，自洽但不合标准。`makeQRMatrix` 函数保留在 index.html 中（未删，但登录流程不再调用），改动二维码时**不要恢复手写编码器**，直接用后端 `qrcode` 库。build.py 已加 `--hidden-import qrcode/qrcode.main/qrcode.generator/qrcode.constants`。
- **canvas 缩放模糊坑（已修，勿回退）**：`drawQR` 设 canvas 原生尺寸（V8 = 285×285），CSS 固定 224×224 缩放显示。默认 `image-rendering:auto`（双线性插值）会让黑白模块边缘出现灰色过渡像素，**手机摄像头无法识别二维码**——必须显式设 `image-rendering:pixelated`（最近邻缩放，边缘保持锐利）。canvas 缩放显示时这条样式不可省。
- **验证方法**：后端 `_make_qr_matrix(url)` → PIL 画 PNG → `pyzbar.decode` 确认能解码还原原文（已通过）；真实窗口实测 start/poll/canvas 黑模占比（V8 实测 0.379）与失效遮罩。真机扫码的 scanned/success 端到端需用户手机确认。

### 番茄钟（纯专注计时，index.html `FocusTimer` + server.py `/api/focus`）
- **独立于 AppStatus**：纯本地功能，与网络/同步状态无关，做成独立 IIFE 模块（`window.FocusTimer` 未暴露，内部 phase: idle|running|paused）。
- 交互：`btnFocus` 即开关（点一下开始、再点结束）；暂停/继续是番茄环内的 `btnFocusPause`。**任何正数时长都记录**（2026-09 已取消"不足 5 秒视为误触不记录"门槛，勿恢复）。**退出时立刻移除 focus-mode class**（按钮变回蓝色"今日专注"，不等 950ms 动画结束）——focus-closing 接管动画，移除 focus-mode 无 layout 跳变（两规则同值覆盖）。**防连续点击**：`focusLock` 时间戳，stop() 时设 `Date.now()+300`，start() 开头 `if(Date.now()<focusLock) return` 阻止 300ms 内重新开始。变量 `focusLock`。
- 并排切换动效：`body.focus-mode` 下总环 `#mainRing` **保持原尺寸不动**（用户明确要求不要缩小、文字也不许动）；`.focus-ring` `transform` 从 `translateX(560px)` 屏外飞入 + opacity 淡入（margin/opacity/transform 过渡均 0.9s，与红弧进出动画同长）。`body` 已加 `overflow-x:hidden`，防 transform 溢出产生横向滚动条。**约定修正：一次性布局切换动画允许 margin 过渡**（每次点击只触发一帧流水，非循环），循环动画仍然只许 transform/opacity。
- **红弧进出动画（与 AI 估算接管/回退同款，2026-09 新增）**：进入 `playBarGrow()`——focusBar 先 none+offset=C 强制重排，再 `stroke-dashoffset .9s cubic-bezier(.22,.9,.35,1)` 到今日累计/目标对应值，`takeUntil` 时间戳内 render() 不写 offset（交给 CSS 过渡），920ms setTimeout 清过渡并校准；退出 `playBarShrink()`——`cubic-bezier(.42,0,1,1)` ease-in 缩回 C，opacity `140ms ease-in 760ms` 串接淡出盖住 round 线帽弧长 0 时的圆点残留（同 AI 回退坑）。**移出时数字反复循环向上滚**（用户要求"向下移一样反复循环"+"连续滚动不能空白"）：`animation:focusNumUp .46s linear infinite`，0→-10em 循环。**关键坑：`setRollInstant`/`pctSlotNode` 创建的 `.droll` 静态时只有 1 行 `.dline`（当前数字），不是 10 行——stop() 必须先填满 0-9 再复制**：`document.querySelectorAll('#focusTime .droll').forEach(r=>{ const cur=parseInt(r.firstElementChild?.textContent||'0',10); if(isNaN(cur))return; r.innerHTML=''; for(let i=0;i<10;i++){const ln=document.createElement('span');ln.className='dline';ln.textContent=(cur+i)%10; r.appendChild(ln);} r.innerHTML+=r.innerHTML; })`——从当前数字起循环排列 10 行（如 cur=3→3,4,5,6,7,8,9,0,1,2），复制成 20 行，-10em 显示第 11 行=第 1 行副本，跳回 0 无缝衔接。数字循环滚动 + 环/淡出/margin/红弧缩回**全部同时 0.9s**（`playBarShrink()` 直接调用，CSS transition 无 delay，`closeTimer` 950ms）。**不要加 delay**（用户最终要求删去延迟）。`closeTimer` 950ms 后 finishClose：先 `fr.style.transition='none'` 禁用 transition 再移除 focus-mode/focus-closing class，`requestAnimationFrame` 下一帧恢复（保险措施，margin 已在 focus-closing 期间过渡到位）。**关键：focus-closing CSS 必须把 margin 也设回默认值 `6px -240px 18px 0` 并加入 transition**——否则 margin 在 finishClose 移除 class 时才瞬间变化，主环（无 transition）瞬间跳 150px，而番茄环已飞走，用户看到"一个先动"（番茄环 0~0.9s 飞出，主环 950ms 后跳）。实测加上 margin transition 后主环从 t=0 同步右移 282→432（0.9s 平滑过渡），与番茄环飞出完全同步。
- **番茄环排版完整性坑（已修，勿回退）**：旧方案 `width:0→240px` + `overflow:hidden` 动画 width 腾位——动画期间 SVG 被 overflow 按宽裁切（width≈0 时内容完全消失、width 中段时 SVG 被竖切只剩半截），用户反馈"移入时错乱排版""移入其间被遮挡消失一段时间"。修法：**width 固定 240px 不动画**（SVG 始终完整渲染）、`overflow:visible`（不裁切）、隐藏时用 `margin-right:-240px` 折叠布局占位（总环仍居中）、显示时 `margin:6px 0 18px 60px` 让位。实测采样 svgW 全程恒定 240px、opacity/transform/margin 单调平滑过渡。**不要再恢复 width 动画 + overflow:hidden**。
- 番茄环与总环**等大 240px**（SVG 同尺寸同几何，无缩放补偿），弧线红色 `#f87171`、track `#3a2430`、计时文字 `#fecaca` 40px。
- **两环并排行对齐规则（2026-09 实测校准，勿拍脑袋平移）**：默认单环 `.ring .center-txt` 几何居中；**开启专注模式时主环内容禁止任何移动**（曾给主环加 translateY 上移被用户明确退回"不要动它"）。对齐完全由番茄环自己完成：番茄环（三行：40px 数字 + `.pct-label` + 暂停按钮）`body.focus-mode .ring.focus-ring .center-txt{justify-content:flex-start; padding-top:86px}`（飞入时不可见可直接定位；86px = 实测使数字行/标签行与主环居中的两行完全同 y），`body.focus-mode .ring .focus-time{height:44px;line-height:44px}`（主环数字自然 44px，行盒统一消化 44/40px 字号差），标签 margin 两边自然继承 `.ring .pct-label` 的 6px。实测两环数字行与标签行 rect 完全一致。主环无对应按钮行，留空。
- **每日目标（2026-09 新增）**：番茄钟从"25 分钟循环纯视觉节奏"改为**每日目标进度**——默认 8 小时（`DEFAULT_GOAL=8*3600`），`.focus-row` 按钮下方 `.focus-goal` 行内嵌 `#focusGoal` number 输入（0.5~24 小时、0.5 步进、非法值回落 8，隐藏 number 原生箭头），localStorage `focusGoalSec` 持久化纯本地偏好；红环 `frac=clamp((todaySec+elapsed())/goalSec,0,1)`，填满即达成不再循环，运行中改目标 render() 立即重算；旧 `PERIOD=25*60` 已删，勿恢复。
- **总环环内只保留两行**：`#bigPct` 百分比 + `.pct-label`"总体播放进度"，默认几何居中、并排时按上条规则对齐。原第三行 `#bigPlayed`（"已播 / 总时长"）已删——与下方 `#stPlayed`/`#stRemain` 统计卡片信息重复（2026-09 用户要求删除，勿恢复；`.played-time` CSS 与 render 中的 bigPlayed 赋值同步删除）。
- 开关按钮颜色规则（用户指定）：**未开启时用 primary 渐变同款**（深色字），hover 加亮 `brightness(1.08)`；`body.focus-mode` 下（显示"结束专注"）**切换为暂停按钮同款红色半透明风**（rgba(248,113,113,.14) 底 + .45 边 + `#fecaca` 字），与暂停/继续按钮 hover 都用**加深**（background 升到 .38）。整行 flex:1 + padding 10px 0；`#focusToday` 在按钮下方居中。
- **开关按钮"闪黑"坑（已修，勿回退）**：渐变是 background-image、红底是 background-color——状态切换时渐变图瞬间消失、底色从透明过渡 150ms，透出近黑页面底色即"闪黑一下"。修法：**按钮底色恒定** rgba(248,113,113,.14)（两态共用），渐变挂在 `::after`（`z-index:-1` + 按钮 `isolation:isolate`，使其落在背景之上、文字之下）上做 opacity 交叉淡出/淡入；`filter` 加亮会同时作用于渐变层。
- **hover 覆盖坑**：全局 `button:hover{background:#152047; border-color:var(--accent)}` 声明在文件后部，同特异性下会覆盖 focus-row 按钮——`.focus-row button:hover` 必须**显式重申 background 和 `border-color:transparent`**（否则悬浮变深且出 accent 描边）。
- **时间显示复用进度同款老虎机滚轮**：`spinPct(str, el, minSteps, coordinated)` 已泛化——el 缺省 = bigPct（自动补 % 后缀），传容器（如 `focusTime`）只滚数字不加后缀；分隔符 `isSep` 同时支持 `.` 和 `:`（静止不滚）。**滚轮结构 CSS（`.dslot/.ddot/.droll/.dline`）作用域必须随容器一起扩展**（现为 `.pct` 与 `.focus-time` 双选择器），复用到新容器漏加选择器会导致滚轮竖排散开不裁切。计时每秒 tick 只滚变化的位（秒位）——`spinPct(s, el, 0, false, 0)` 传 `coordinated=false` 不触发 hasAnySpin 全体联动、第 5 参 0 同时起跳（实时计时无波浪延迟）；**进入/退出例外用错峰**（见老虎机章节：start() 直接调 `spinPct` 错峰开场、stop() 收场按位加 `animationDelay`）；`REDUCED_MOTION` 走 `setRollInstant` 直跳；**stop() 复位必须用 `setRollInstant`**（`textContent=` 会把滚轮 DOM 打回纯文本）。**start() 每次都要滚**：start 时先 `$('focusTime').textContent = fmt(todaySec)` 重置为今日累计纯文本，让 spinPct 走 structureOk=false 重建分支、所有位滚 10 步（回滚一圈）；否则 stop 后 setRollInstant 留下的 .dslot 结构会让 spinPct 判定 structureOk=true、steps=0 直接 return，第二次开启就没动画了。
- **环内显示今日累计**：`render()` 显示 `fmt(todaySec + elapsed())`（今日累计含本次），不再是"本次时长"；按钮文字"番茄钟"已改为"今日专注"；stop 后回显 `fmt(todaySec)`（服务端返回的已含本次）。
- 计时基于时间戳（`Date.now()/1000 - startedAt` + 暂停累计 `base`），**后台 timer 被节流也不丢秒**。**系统休眠后 interval 可能被挂起不恢复**——visibilitychange 回窗口时如果 `phase==='running'`，必须 `clearInterval(timer); timer = setInterval(render, 1000)` 重建 interval，否则休眠唤醒后番茄钟不再 tick。红环总量为每日目标（默认 8h，见上条），今日累计填满即达成。
- 结束时 POST `/api/focus {"add": 秒}`（**旧版兼容接口，勿在新代码中使用**）；启动时 GET `/api/focus` 渲染"今日专注"。
- **运行态持久化（2026-09 新增，改动必读）**：计时时间是不可丢失数据，**权威状态在服务端**（服务端 `time.time()` 时钟），接口 `POST /api/focus/{start,pause,resume,heartbeat,stop}`：start 落盘会话、pause 把运行段折进 base、resume 重开运行段、运行中每 15s heartbeat 更新 updated_at、stop 原子地"算本轮→入账（任何正数秒都记录）→删会话"（摘会话在 `_wlock` 内，重复/并发 stop 不会重复入账）。前端本地 base/startedAt 仅用于显示，每次操作以服务端返回的 `now` 对齐。**关闭按钮（#winClose）必须先 `await FocusTimer.flushOnExit()` 再走 hide/quit**——否则 Linux 的 close 消息和 Windows 的 /api/quit 会立即 os._exit，本轮时间丢失。`pagehide` 用 `navigator.sendBeacon('/api/focus/stop')` 兜底浏览器模式。**崩溃恢复**：GET /api/focus 先跑 `recover_focus_session()`——running 残留封口 = min(now, updated_at+60)（心跳 15s，崩溃最多损失约 15s；也防止几天后才打开却凭空计入数天），paused 残留按 base 全额；`recovered>0` 即入账并经响应字段让前端 toast。跨午夜口径（用户已确认）：全部计入结束当天，不做跨天拆分。

### 同步与轮询
- B站请求走 `_https_get` 连接复用（threading.local 每线程每域名一条 HTTPSConnection，出错 drop 重建重试一次）；错误文案 `_friendly_err`（DNS/超时/证书）；code=-101 → needSessdata。
- **历史翻页协议**：游标在 `data.cursor.view_at`（秒级时间戳），下一页传 `view_at=<上页 view_at>`；旧 `max`/`data.page` 已失效（传了返回首页，曾导致只扫 60 条）。`fetch_progress` 必传 stop_bvid，命中即停（通常 1 页）。
- 自动同步：当前视频每 60 秒（`AUTO_SYNC_MS`；设置面板仅总开关，localStorage `autoSync`，关闭时全量轮询一并停）。
- 回窗口/获得焦点补同步（`wakeSyncCheck`）：visibilitychange 变可见或 window focus 时，距上次同步（`lastSyncAt`，fetchProgress 进入时刷新，任何上下文都算）超 60s 就静默 `fetchProgress('auto')`。
- 全量轮询 `pollAllVideos` 每 2 分钟（`POLL_ALL_MS`）静默刷非当前视频徽章（条目间隔 1.2s，needSessdata/断网即停本轮，notfound 保留旧徽章；`resetPollAll` 自续且幂等，bootstrap 时启动）。历史注：此功能 2026-09 前只存在于旧版 AGENTS.md 记载，代码中从未落地，现已实现。
- **同步代次 `syncGen`（并发防重，勿回退）**：`switchVideo` 开始时和每次 `fetchProgress` 进入时 `++syncGen`；响应解析后先比 `gen !== syncGen`，落后即整响应作废（成功/失败/异常三个分支都守；finally 里 `resetAutoSync` 也只许最新请求执行）。`switchVideo` 还必须立即清 `autoSyncTimer` + `stopSyncRetry()`。旧坑：无此机制时旧视频的自动同步/重试与切换请求并发、乱序返回，每次成功都 `playSheen()`，表现为"来回切换多次流光"；旧记录里"fpInFlight 互斥"并不存在，以此条为准。
- 启动静默同步失败退避重试 5/15/30s×5（needSessdata 除外），监听 window online 即时重试。开机横幅根因多为开机瞬间网络未就绪，属正常，会自动恢复。

### 窗口与交互（app.py）
- **统一入口**：app.py 内 `run_windows()`（pywebview）与 `run_linux()`（GTK+WebKit）两个分支，共用 `start_http_server()`（端口 8765 起向后回退 10 个、回写 srv.PORT）与常量；改窗口行为必须两端同步。
- Windows：tkinter 独立线程启动画面（stop_evt 轮询关闭）；主窗口 `hidden=True` + `events.loaded` 后 show()；关 X 先 `api.hide_window` 再 `/api/quit`；`webview.start()` 后 `os._exit(0)` 秒关。
- **Windows 八向缩放**：JS 边缘热区 `.rz` setPointerCapture 只报方向 → `api.resize_edge(edge)` → Python 端 GetCursorPos+GetWindowRect（物理像素同坐标系）算"边缘=鼠标" → SetWindowPos 一次完成位置+尺寸。**严禁在 JS 端用 window.screenX/innerWidth 算目标尺寸**（几何滞后一帧，拖 w/n 边误差正反馈发散，窗口飞出屏幕）；pywebview 的 win.resize+FixPoint 同样不可用。hwnd 取法：`self._win.native.Handle.ToInt32()`（.NET IntPtr 不能直接 int()）。最小尺寸由 pywebview MinimumSize 在 WM_WINDOWPOSCHANGING 兜底。
- 空状态引导页：`body.empty` 隐藏 header/panel/footer。
- 弹窗打开锁定主界面滚动；toast 底部居中、z-index 3001、宽度 `min(62vw,520px)`；Tab 栏悬停滚轮可左右滚。
- 快捷键：← 上一集；→ "本集看完，跳下一集"（控制区只有"上一集"和"本集看完，跳下一集"两个按钮，原重复的"下一集"已删）。

### Linux 原生窗口（app.py `run_linux`，GTK3 + WebKit2GTK 4.1）
- **无边框**：`Gtk.Window.set_decorated(False)`；窗口 RGBA + `set_app_paintable`，标题栏完全由页面 `.titlebar` 承担（页面 bootstrap 检测到注入的 `window.pywebview` 即不加 `browser-mode`，标题栏保留）。
- **pywebview shim 必须 DOCUMENT_START 注入**（`WebKit.UserScript` + `UserContentManager`）：页面启动早期就判断 `window.pywebview`；shim 的 `minimize`/`hide_window` 通过 `webkit.messageHandlers.native.postMessage` 发消息（`register_script_message_handler("native")`），`resize_edge` 留空。
- **拖动/缩放必须走原生事件，不能走 JS 消息**：Wayland 下 `begin_move_drag`/`begin_resize_drag` 要合成器接管且需要按下事件的 serial/time，JS 异步桥拿不到。在 WebView 的 `button-press-event` 里用事件坐标判断：①边缘 7px 边带 → 四角/四边 `begin_resize_drag`；②标题栏 38px 高、且不在右侧 92px 按钮区 → 单击 `begin_move_drag`、双击切换最大化。按钮区点击 return False 照常下发，页面按钮才能收到。
- **PyGObject 的 drag API 是老签名**：`begin_resize_drag(edge, button, root_x, root_y, time)`（5 参，**没有 device**）、`begin_move_drag(button, root_x, root_y, time)`（4 参）。多传 device 会静默失败（旧坑：拖不动窗口）。
- **圆角只能由页面自绘**：Wayland 下 WebKit 内容在独立矩形 subsurface，GTK 层 CSS `border-radius` 裁不到（旧坑：四角全直）。做法：WebView `set_background_color(透明 RGBA)` + GTK 窗口透明 + 注入脚本给 `<html>` 加 `linux-native` 类，CSS 里 `html.linux-native body{border-radius:12px}`；最大化时由 `window-state-event` 经 `run_javascript` 切 `native-maximized` 类取消圆角。加载真实页面前先 `load_html` 一帧深色圆角占位，防透明穿帮。
- 窗口尺寸/最大化状态存 ROOT 下 `ui_state.json`（非追踪数据，不进 .gitignore 拦截名单但也无需提交——在 XDG 数据目录里）。

## 环境教训

- Windows 11，Python 3.14（`py -3`），PyInstaller 6.22.2，pywebview/Pillow 已装。
- **Linux（Ubuntu 26.04 / Wayland）桌面方案定为 GTK3 + 系统 WebKit2GTK 源码 deb**：不要在 Linux 上用 PyInstaller（包大、且要处理显卡/keyring 一堆打包问题），也不要用 Edge `--app`（标题栏跟 GTK 主题、无法可靠去掉，PWA window-controls-overlay 在 --app 下不生效）。改窗口逻辑时 app.py 两个平台分支一起改。
- **源码文件名一律英文 ASCII**（2026-09 用户明确要求）：目录与文件名全部英文（src/scripts/build/run/install…），禁止中英混杂。中文只允许出现在：注释文案、应用产品名/运行时数据目录（Windows 下 `B站课程进度追踪/` 是成品应用名）。bat 脚本本来就要纯 ASCII，此规则对所有源码文件生效。
- **改动后必须 grep 自查关键声明已落盘**——"已改"的口头/摘要声明不可信（出现过摘要说已改、实际漏改的情况）；接手会话或恢复上下文时先以代码实际状态为准再动手。
- **推送规则（2026-09 用户确认，勿违反）**：日常改动只在**本地保存（commit），不主动 `git push`**；一批功能告一段落、**经用户确认后**才推送（大版本号提升时同理）。推送前必须把累积改动整理进 CHANGELOG.md，与代码一起提交——禁止只推代码不带日志，也禁止未经用户确认自行推送。
- 沙箱禁止 Python 往安装目录写 `__pycache__/*.pyc`（PyInstaller 报 "hit restricted"）：`PYTHONDONTWRITEBYTECODE=1` + `py -3 -B` + `PYINSTALLER_CONFIG_DIR` 指项目内。
- PowerShell 执行策略可能禁止 .ps1；复杂文件操作用 Python 脚本而非内联 PowerShell（引号嵌套易出错）。
- 修改 index.html 后验证：提取 `<script>` 内容 `node --check` 语法；**动效类改动必须实测**，不要只靠静态分析或"代码看起来对"——本项目动效坑（数字落位错、动画被中间帧打断、渐变退化成方块）全是实测才发现的。平台分工：
  - Windows：工作区 skill `ui-motion-verify`（`.trae/skills/ui-motion-verify/SKILL.md`：pywebview 真实窗口 + PrintWindow 截图 + evaluate_js 采样）
  - Linux：工作区 skill `linux-ui-motion-verify`（`.trae/skills/linux-ui-motion-verify/SKILL.md`：GTK 离屏窗口 + 系统 WebKit 加载真实页面 + 定时 pixbuf 截图，含 scripts/check_js.py 语法校验与 scripts/verify_motion.py 多帧采样）
- 工作区 skill 目录 `.trae/skills/`：若提交 GitHub 共享则保留；不共享则将 `.trae/` 加入 .gitignore。
- **复现验证的边界**：用户对现象描述清晰、无歧义时，直接读代码定位并修复，不要先花一轮操作去"复现确认"用户说的现象（无信息增量）。实测验证只用于两类场景：① 修复后确认修复生效；② 现象描述模糊/有多种可能根因时，缩小排查范围。

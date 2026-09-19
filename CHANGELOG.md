# 更新日志

## 2026-09-19 — 项目文件整理

- 根目录散落脚本统一收进 `scripts/`，并全部改为英文 ASCII 文件名：
  - 启动.bat → run.bat、打包.bat → build.bat、安装依赖.bat → install.bat、启动.sh → run.sh
- 各脚本内先 `cd` 回项目根再执行，双击/命令行使用方式不变
- 清理运行时垃圾：build 中间残留、旧中文数据目录、.venv、__pycache__（释放约 250M）
- 同步更新 README 目录树、AGENTS.md、app.py 与 ui-motion-verify skill 中的路径
- 验证：scripts/build.py 打包正常、scripts/run.sh 启动正常、JS 语法校验通过

## 2026-09-19 — 移除圆环流光

- **删除进度环扫光**：圆环流光经多次调整（实心弧→锥形彗星→长尾渐变）仍达不到预期效果，用户决定移除
- 同步成功的流光反馈**仅保留直线进度条** `.bar-track.sheen`（0.9s 白色渐变扫过）
- 删除内容：`.ring-sheen` / `@keyframes ringSheen` 全部 CSS、`#ringSheen` DOM 元素、`playSheen()` 中的圆环分支

### 其他
- 新增工作区 skill `linux-ui-motion-verify`：系统 WebKit 离屏窗口加载真实页面 + 定时截图采样的 Linux 动效验证流程，含可复用脚本 check_js.py / verify_motion.py

## 2026-09-19 — 流光优化：切换不闪、环上彗星恢复饱满渐变

- **切换视频后不再立刻播放流光**：切换时已有 .swap-in 淡入动画，紧跟流光显得杂乱；`fetchProgress` 的 `switch` 上下文跳过 `playSheen()`，手动刷新/自动同步/初始化同步仍播放
- **修复环上扫光退化成方块**：旧锥形渐变的亮区（.30→.85）集中在最后 14° 弧（约 26px），形成白色方块，低亮度尾迹在深色轨道上几乎不可见。改为 **90° 长尾多级渐变**（270° 起从 .04 逐级过渡到亮头 .85，最高亮区仅最后 2°），运动中呈现饱满彗星扫过的效果
- 验证：系统 WebKit 加载真实页面触发 playSheen，300ms 帧采样确认尾迹加长、亮头不再硬方块

## 2026-09-19 — 修复来回切换视频时多次流光

- **问题**：快速来回切换视频时，同步流光（进度条/圆环）被播放多次
- **根因**：同步请求无并发/新旧保护——切换过程中旧视频的自动同步定时器、失败重试定时器可能触发，与切换请求并发；多个请求乱序返回，每个成功回调都无条件 `playSheen()`
- **修复**：
  - 新增同步代次计数器 `syncGen`：每次切视频开始与每次 `fetchProgress` 进入时 +1；响应返回时代次已落后则整个响应作废（不更新进度、不报状态、不播流光、不排重试），失败/异常分支同样守卫
  - `switchVideo` 立即清除 `autoSyncTimer` 和 `syncRetryTimer`，旧定时器不再在切换途中发起请求
  - 过期请求的 `finally` 不再重排自动同步，避免顶掉新请求的定时器

## 2026-09-19 — Linux 原生窗口 & 入口统一

### 新增
- **Linux 原生无边框窗口**：GTK3 + 系统 WebKit2GTK 4.1，标题栏由页面自绘（深色），与 Windows 版外观交互一致，不再出现系统白色标题栏
- **拖动/缩放原生处理**：Wayland 下交互移动/缩放必须由合成器接管（需要按下事件 serial），边缘 7px 热区命中判断在原生 button-press-event 中完成；支持拖动、双击标题栏最大化、八向缩放
- **窗口圆角**：WebView 透明 + 页面 body 自绘 12px 圆角（Wayland subsurface 无法被 GTK 层裁切），最大化时自动取消
- 窗口尺寸/最大化状态持久化（数据目录 ui_state.json）

### 变更
- **桌面入口统一为 src/app.py**：Windows（pywebview）与 Linux（GTK+WebKit）逻辑合并进同一文件，平台分支用注释清晰分隔；删除 app_linux.py、app_linux_native.py
- **Linux 打包改为源码 deb**：不再用 PyInstaller（包体积 42M → 133K），源码装 /opt，运行时依赖由 control 的 Depends 声明（python3-gi、gir1.2-gtk-3.0、gir1.2-webkit2-4.1、python3-keyring、python3-pil、python3-qrcode），apt 安装时自动配齐
- `启动.sh` 改为直接启动原生窗口（app.py），并检查 GTK/WebKit 系统库
- server.py 支持 `BILI_TRACKER_ROOT` 环境变量指定数据目录
- `/api/icon/file` 无自定义图标时回退 assets 默认图标（数据目录迁移后标题栏 logo 不再变黑）

## 2026-09-19 — 修复 gnome-keyring 每次启动崩溃

- **问题**：每次运行 启动.sh 都弹"Ubuntu 出现内部错误"（gnome-keyring-daemon SIGABRT）
- **根因**：keyring SecretService 每次调用新建并立即销毁 D-Bus 连接，gnome-keyring 在异步属性读取途中遇客户端断开，触发 GIO 断言（invoke_get_property_in_idle_cb: error != NULL）
- **修复**：进程内复用持久 SecretService 客户端（连接常驻）；新增凭据值进程内缓存，get_sessdata 绝大多数请求不再联系 keyring；write/delete 同步维护缓存

## 2026-09-18 — 协作约定：推送必须附带更新日志

- 约定：每次推送到 GitHub 前，必须更新 CHANGELOG.md，与代码改动一起提交

## 2026-09-18 — B站合集（ugc_season）合并追踪

### 新增功能
- **合集合并追踪**：添加属于 B站合集的视频时，可选择将整个合集合并为一个课程条目（多集统一进度、集数切换）
- **添加弹窗行内选择**：点击"追踪"后，若视频属合集，按钮区就地展开"仅追踪此视频"/"合并合集(N集)"两按钮，不再弹二次确认框
- **多 bvid 进度同步**：合集条目同步进度时搜索所有集的 bvid，命中后映射回合集集序号

### 后端
- `fetch_video_info` 检测 `ugc_season` 字段；`fetch_season_archives` 从 view 接口 `sections` 展平合集全部剧集
- `/api/tracked/add` 支持 `merge_season` 参数；合并时自动移除已单独追踪的合集内视频并迁移最新进度
- 落盘新增 `is_season`/`season_id` 字段；`_enrich_video` 对合集条目调 `fetch_season_archives`

### 修复
- **合集封面**：改用首集视频封面（`ugc_season.cover` 常为纯黑图）
- **UP主名**：从 view 接口读取 `owner.name`（原写死空串导致显示"-"）
- **集序号**：合集每集 `page` 用合集内集序号（原恒为 1，下拉框全显示"第1集"）
- **keyring 回退**：Linux 无 D-Bus/Secret Service 时 `keyring` 调用抛 `InitError`，回退到明文 `.sessdata.bin`

### 其他
- Linux `启动.sh`：依赖安装到项目内 `.pydeps/`（规避 PEP 668 与沙箱权限）

## 2026-09-18 — 番茄钟动效完善 & 圆环扫光

### 番茄钟
- **退出动效**：结束专注时数字反复循环向上滚动（与进入时向下落入对称），环向左飞出、淡出、红弧缩回、主环同步右移同时进行
- **每日目标**：新增每日目标设置（默认 8 小时，0.5~24 小时可调），红色进度环按"今日累计 / 每日目标"填充，填满即达成
- **按钮即时反馈**：结束专注时按钮立即变回蓝色"今日专注"，不再等动画结束
- **防连续点击**：结束后 300ms 内禁止重新开始，避免误触

### 进度环
- **扫光改渐变彗星**：圆环白光从实心白柱改为渐变彗星尾迹（conic-gradient + radial-mask），与直线进度条扫光质感统一
- **流光速度统一**：圆环与直线条扫光线速度一致

### 布局
- **主环文字固定**：开启专注模式时主进度环的文字不再移动，番茄环自己对齐到主环位置
- **环内重复时间删除**：移除了进度环内与下方卡片重复的时间显示

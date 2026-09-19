---
name: linux-ui-motion-verify
description: Verify Linux UI animations by loading the real app page in a GTK offscreen window with system WebKit, triggering the animation, and sampling timed screenshots plus JS state. Use after changing animation/CSS/JS in src/index.html on Linux. Do not use for Windows (use ui-motion-verify) or for static text-only edits.
---

# Linux UI 动效验证

在 Linux 上用**系统 WebKit（和生产窗口同一个引擎）**加载真实页面、真实触发动效、定时截图采样。
静态分析对本项目的动效问题（落位错误、动画被中间帧打断、渐变退化成方块）没有信息量，必须实测。

## 前置条件

系统库（缺失则提示用户安装）：

```bash
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
```

在 Trae 沙箱里运行这些脚本需要禁用沙箱（要访问 X/Wayland 会话）；用户自己在终端运行不受影响。

## 标准流程

### 1. 语法校验（每次改完先跑）

```bash
python3 .trae/skills/linux-ui-motion-verify/scripts/check_js.py
```

用 WebKit 的真实解析器执行 `new Function(脚本内容)`，比 esprima 可靠（支持新语法）。
输出 `OK` 才继续。

### 2. 动效采样

```bash
python3 .trae/skills/linux-ui-motion-verify/scripts/verify_motion.py \
  --trigger "playSheen()" \
  --wait 3500 \
  --samples 260,680
```

脚本行为：
- 以 `BILI_TRACKER_ROOT`（默认 `~/.local/share/bili-course-tracker`）为数据目录启动真实 HTTP 服务
- `Gtk.OffscreenWindow`（1120×840）内用系统 WebKit 加载 `http://127.0.0.1:<port>/`
- 页面加载完成后等待 `--wait` 毫秒（等数据请求与渲染稳定），执行 `--trigger` 的 JS
- 在 `--samples` 指定的每个毫秒偏移截一帧 PNG（`/tmp/motion_<ms>.png`），最后自动退出
- 用 Read 工具逐张查看截图，判断动效是否符合预期

常用触发表达式：

| 验证目标 | --trigger |
|---|---|
| 同步流光 | `playSheen()` |
| 切视频入场 | 见下文「复杂链路」 |
| 100% 庆祝 | `maybeCelebrate()`（需先构造完成态 lastProgress） |
| 老虎机百分比 | `pendingSpin=true; renderPct(45.0)` |
| AI 估算接管 | `startEstimate()`（先设调试基准） |

### 3. 需要采样 JS 内部状态时

在 `--trigger` 里把状态写进 `document.title`，再用截图脚本的变体读取；
或临时在脚本的采样回调里加 `web.run_javascript("JSON.stringify({...})", callback)`。
采样 transform/opacity 时注意读取要在样式应用后的下一帧。

## 复杂链路（切视频等）

切换涉及网络 await，无法用单次 `--trigger` 复现。做法：
复制 `scripts/verify_motion.py` 为临时脚本，在 `load-finished` 后先调用页面内部的
`switchVideo('BV...')`（通过 `run_javascript`），再按预期时序多帧采样。
注意切换时同步代次 `syncGen` 会作废旧请求响应，采样间隔要覆盖整个切换过程。

## 踩坑记录（脚本骨架已规避，修改时勿回退）

- **截图用 `Gtk.OffscreenWindow.get_pixbuf()`，不要用 `WebView.get_snapshot()`**：
  后者 finish 返回 cairo Surface，常报 "Couldn't find foreign struct converter for 'cairo.Surface'"。
- 定时器是 `GLib.timeout_add`，**没有 `Gtk.timeout_add`**。
- `GLib.timeout_add` 的回调**不接收参数**；要传上下文用闭包或 lambda。
- `load-finished` 后立刻读 `web.get_title()` 可能拿到旧值，需再延迟 300ms。
- 页面会发起真实 B站请求并打印服务端日志到 stderr，属正常；数据目录用 XDG 路径，
  不要指到 /opt 或源码目录（无写权限）。
- 一次加载可能触发多次 `load-changed`（仅导航会 FINISHED），触发逻辑要用 fired 标志防重入。
- 脚本内已设 `GSETTINGS_BACKEND=memory`：测试/沙箱环境可能无权访问 `/run/user/<uid>/dconf/user`，
  不设置会刷一屏 dconf-CRITICAL。
- **单集视频没有 `.bar-track`**（`.single-ep .bar-over{display:none}`），
  在单集视频上触发 `playSheen()` 看不到任何流光，验证流光要选多集视频（如合集）。
- 离屏窗口共享 WebKit 的 localStorage/profile，页面恢复的"上次查看视频"可能不是你预期的那个；
- 脚本内已通过 `BILI_TRACKER_ROOT` 固定数据目录，但 active 视频由页面 localStorage 决定。

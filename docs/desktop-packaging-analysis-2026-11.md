# 桌面端（.exe）可行性分析

> 项目：AI 剧本杀（LangGraph + Streamlit）
> 日期：2026-11-13
> 结论：**可以做，而且不需要重写业务层；但不能简单执行 pyinstaller app.py。**

## 1. 结论先行

推荐路线：

**PyInstaller（onedir）+ pywebview（Edge WebView2）+ 内置 Streamlit Server + Inno Setup 安装包**

- 业务层 graph.py / nodes.py / rules.py / validators.py / prompts.py 基本零改动；
- UI 层 app.py / ui_*.py 基本零改动；
- 最终交付：一个 AI剧本杀-Setup.exe 安装程序，安装后桌面快捷方式启动；
- 如果必须“单文件 exe”，可以 --onefile，但启动慢、杀毒误报率高，不推荐作为正式版。

不适合的路线：

- 直接 pyinstaller app.py：Streamlit 不是普通 GUI 程序，不会自动起 server，得到的 exe 无法正常显示界面；
- 把 Streamlit 重写成 PySide6/Flet：UI 动画、st.iframe、Web Audio、聊天组件全部要重做，工作量至少 2–4 周，风险远大于收益；
- 用 stlite（Pyodide）打包：langchain-deepseek / langgraph / 本地网络请求在浏览器 WASM 环境不可靠，不适合本项目。

## 2. 当前项目与桌面化的匹配度

### 2.1 适合桌面化的部分

- 所有核心逻辑都是本地 Python：LangGraph 状态机、规则校验、名字池、日志；
- 不依赖外部数据库；MemorySaver 就是单进程内存，桌面单用户正好；
- UI 资源全部内联：ui_assets.py、ui_audio.py 的 BGM/音效都是代码生成，无外部图片/音频服务；
- 不依赖服务器端登录、云存储、多用户并发；
- st.iframe / st.html / Web Audio 在 Edge WebView2（Chromium）里可以正常工作；
- 172 个测试全部 fake LLM，不依赖网络，适合打包前回归。

### 2.2 需要处理的桌面化障碍

| 障碍 | 现状 | 影响 | 处理 |
|---|---|---|---|
| Streamlit 是 Server 架构 | app.py 用 streamlit run 启动 | exe 不能直接显示窗口，必须起本地 server 再套 WebView | launcher 启动 streamlit.web.bootstrap，pywebview 打开 127.0.0.1:port |
| .env / API Key | config.py 在 import 时 load_dotenv() | 打包后 CWD 不是项目目录，找不到 .env；把 key 打进 exe 会泄露 | 首次启动设置页 / Windows Credential Manager；不要打包 .env |
| 日志路径 | logging_config.py 按 __file__ 写 logs/ | onefile 时写到临时解压目录，日志丢失；Program Files 只读 | 改为 %LOCALAPPDATA%\AIMurderMystery\logs |
| SQLite checkpoint | graph.py 按 __file__ 写 checkpoints.sqlite | 同上 | 改为 %LOCALAPPDATA% 用户数据目录 |
| .streamlit/config.toml | Streamlit 按 CWD 读取主题 | exe 从快捷方式启动时 CWD 可能是 system32，主题错乱 | launcher 设置 CWD 或用 --theme.* 参数显式传入 |
| WebView2 运行时 | Windows 11 自带；部分 Windows 10 没有 | pywebview 退回旧 IE 内核，CSS/JS 全崩 | 安装包检测 WebView2，缺失时安装 Evergreen Bootstrapper |
| 中文字体 | CSS 使用 KaiTi / STKaiti / STSong | 非中文 Windows 可能缺字体，排版变差 | 使用系统 serif 兜底，或打包开源中文字体 |
| 网络与 API 计费 | 每局调用 DeepSeek API | 桌面版不是离线版，断网/欠费就不可玩 | 启动时做连通性检查；明确提示需要网络和 API Key |
| 单文件打包 | PyInstaller onefile 解压到临时目录 | 启动 8–20 秒、杀毒误报、临时目录权限问题 | 正式版用 onedir + 安装包；onefile 仅便携版 |

## 3. 四种方案对比

| 方案 | 改动量 | 体积/启动 | 桌面体验 | 风险 | 结论 |
|---|---:|---|---|---|---|
| A. PyInstaller + pywebview + Streamlit | 小 | 约 250–450MB（单 exe）/ 300–500MB（双 exe）；3–8 秒启动 | 独立窗口、无浏览器地址栏 | 中 | **推荐** |
| B. PyInstaller + 默认浏览器 | 最小 | 同 A | 会打开 Chrome/Edge，像本地网页 | 低 | 可作为快速原型 |
| C. Electron/Tauri + Python sidecar | 大 | Electron 150MB+；Tauri 小但需 Rust | 最像原生应用，可做菜单/托盘/更新 | 高 | 作品集/商业版再考虑 |
| D. PySide6/Flet 重写 UI | 很大 | 中等 | 原生控件，但会失去现有视觉 | 很高 | 不建议 |
| E. stlite/Pyodide | 中 | 浏览器 WASM | 无 Python 安装 | 很高 | 不适合 LangChain/LangGraph |

> A 方案的详细落地方式见 [desktop-option-a-proposal-2026-11.md](desktop-option-a-proposal-2026-11.md)。
>
> C 方案的详细落地方式见 [desktop-option-c-proposal-2026-11.md](desktop-option-c-proposal-2026-11.md)。

## 4. 推荐架构

~~~text
AI剧本杀.exe（launcher，PyInstaller onedir）
│
├─ 子进程：内置 Python + Streamlit Server
│    └─ 127.0.0.1:随机端口（仅本机监听）
│
├─ 主窗口：pywebview + Edge WebView2
│    └─ http://127.0.0.1:<port>
│
├─ LangGraph / LangChain
│    └─ HTTPS → DeepSeek API
│
└─ 用户数据目录：%LOCALAPPDATA%\AIMurderMystery\
     ├─ config.json / credential（或 Windows Credential Manager）
     ├─ logs\game.log
     └─ checkpoints.sqlite（可选）
~~~

### 启动流程

1. launcher 读取用户配置和 API Key；
2. 找一个空闲端口，设置 server.address=127.0.0.1、server.headless=true；
3. 子进程启动 streamlit.web.bootstrap.run(app.py, ...)；
4. 轮询 http://127.0.0.1:<port>/_stcore/health；
5. pywebview.create_window(...) 打开窗口；
6. 关闭窗口时终止 Streamlit 子进程并清理。

### 为什么用子进程而不是线程

streamlit.web.bootstrap.run() 内部会安装 signal handler，并且会阻塞 asyncio event loop；在后台线程里启动容易遇到 signal/主线程问题。用“同一个 exe 的 --streamlit-server 模式”自我启动子进程最稳：

~~~text
AI剧本杀.exe            → 正常双击：启动 pywebview
AI剧本杀.exe --server   → 内部模式：只跑 Streamlit server
~~~

## 5. 需要改哪些代码

### 5.1 新增 launcher.py

职责：

- multiprocessing.freeze_support()（PyInstaller 必需）；
- 读取 %LOCALAPPDATA% 配置；
- 找空闲端口；
- 启动/停止 Streamlit 子进程；
- pywebview.create_window() + webview.start()；
- 处理 --server 内部模式。

### 5.2 配置路径模块

新增 paths.py：

~~~python
def user_data_dir():
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "AIMurderMystery")
    os.makedirs(path, exist_ok=True)
    return path
~~~

改造：

- logging_config.py：日志目录改为 paths.user_data_dir()/logs；
- graph.py：checkpoints.sqlite 改为用户数据目录；
- config.py：load_dotenv() 改为显式加载 %LOCALAPPDATA%\AIMurderMystery\.env 或由 launcher 注入环境变量；
- 不把 .env 放进安装目录，避免 API Key 随 exe 泄露。

### 5.3 首次启动设置 API Key

三种做法，推荐第一种：

1. launcher 原生设置窗口：Tkinter 或 pywebview 的本地设置页；保存到 Windows Credential Manager（keyring）；
2. 安装后在 %LOCALAPPDATA% 放 .env，首次让用户手工编辑（体验差）；
3. Streamlit 应用内输入并重启 server（实现别扭，不推荐）。

### 5.4 Streamlit 主题

.streamlit/config.toml 需要作为 data 文件打包，或在启动参数里显式传：

~~~text
server.headless=true
server.address=127.0.0.1
server.fileWatcherType=none
browser.gatherUsageStats=false
theme.base=dark
theme.primaryColor=#C0392B
theme.backgroundColor=#141110
theme.secondaryBackgroundColor=#1D1916
theme.textColor=#E8DCC8
~~~

## 6. 打包步骤（建议照抄）

### 6.1 环境

- Windows 10/11 64 位；
- Python 3.10 64 位（项目已验证 3.10.20）；
- PyInstaller 6.x；
- pywebview 5.x（EdgeChromium 后端）。

### 6.2 依赖

新增 requirements-desktop.txt：

~~~text
pyinstaller>=6,<7
pywebview>=5,<6
keyring>=25
~~~

### 6.3 PyInstaller 命令（onedir）

~~~powershell
pyinstaller --noconfirm --clean --onedir --windowed ^
  --name "AI剧本杀" ^
  --icon "assets/app.ico" ^
  --add-data "app.py;." ^
  --add-data ".streamlit/config.toml;.streamlit" ^
  --collect-all streamlit ^
  --collect-all langgraph ^
  --collect-all langchain_core ^
  --collect-all langchain_deepseek ^
  --collect-all pydantic ^
  --hidden-import streamlit.web.bootstrap ^
  --hidden-import streamlit.web.cli ^
  --hidden-import streamlit.runtime.scriptrunner ^
  --hidden-import webview.platforms.edgechromium ^
  launcher.py
~~~

注意：

- --windowed 会隐藏控制台；launcher 自己写日志，不要依赖 print；
- --collect-all streamlit 会把 Streamlit 的 static 资源一起收进去；
- --add-data 的 Windows 分隔符是分号；
- 先 --onedir 验证，再考虑 --onefile。

### 6.4 安装包

用 Inno Setup 生成：

~~~text
AI剧本杀-Setup.exe
  └─ 安装到 %LOCALAPPDATA%\Programs\AIMurderMystery
  └─ 创建桌面快捷方式
  └─ 检测/安装 WebView2 Runtime
~~~

比“单文件 exe”更像正常 Windows 软件，也便于升级和卸载。

## 7. 需要重点测试的桌面场景

1. 干净 Windows 10/11（没有 Python、没有项目源码）能否启动；
2. 没有 WebView2 的机器：安装器能否自动补齐；
3. 首次启动没有 API Key：设置页是否清晰、不会直接崩；
4. 断网 / API Key 错误 / DeepSeek 限流：是否有可读错误；
5. 中文 Windows 字体、1366×768 与 2K 分辨率；
6. 启动后第一次点击音效是否被浏览器自动播放策略拦截；
7. 关闭窗口时 Streamlit 子进程是否退出（任务管理器无残留）；
8. 端口被占用时能否换端口；
9. 长局（30 分钟、100+ 条消息）滚动、内存、checkpoint；
10. SmartScreen / 杀毒软件误报；
11. 安装路径含中文/空格；
12. 双击快捷方式时的工作目录是否正确（主题、日志、.env）。

## 8. 工作量与风险

### MVP（能跑、能演示）

- launcher + onedir 打包 + 手动配置 API Key：1–2 天；
- 不处理 WebView2 自动安装、不做代码签名。

### 可交付版

- 首次设置、用户数据目录、日志/checkpoint、图标、Inno Setup：3–5 天；
- 干净 Windows 虚拟机回归 + 安装包测试：再加 1–2 天。

### 主要风险

1. Streamlit 打包不是官方主路径：streamlit.web.bootstrap 是内部 API，必须锁定 streamlit==1.61.1；
2. WebView2 依赖：Windows 10 老机器可能缺失，必须做安装检测；
3. 杀毒误报：PyInstaller onefile 常见，正式版用 onedir + 签名；
4. API Key 安全：桌面客户端无法真正藏住 key，必须让用户自己填；
5. 不是离线应用：没有 DeepSeek 网络就没有剧本生成，这是产品边界，不是打包问题；
6. 单文件 exe 启动慢：onefile 每次解压，30 秒内启动可能被用户认为卡死。

## 9. 最终建议

- 课程答辩/演示：走方案 A，先做 onedir + pywebview 的 MVP，1–2 天可出可双击的桌面窗口；安装包后续补。
- 正式交付给同学/老师：加 Inno Setup + WebView2 检测 + 首次 API Key 设置，3–5 天。
- 不要重写 Streamlit UI：现有 172 个测试、动画、音频、角色卡都值得保留。
- 不要打包 .env：把 DeepSeek Key 放进 exe 等于公开泄露。
- 如果用户要求“完全离线”，当前架构做不到；需要接入本地模型或预生成剧本包，属于另一个产品阶段。

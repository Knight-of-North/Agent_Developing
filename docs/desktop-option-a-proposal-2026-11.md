# 桌面端 A 方案书：PyInstaller + pywebview + Streamlit Server

> 项目：AI 剧本杀（LangGraph + Streamlit）
> 版本：v1.0
> 日期：2026-11-13
> 状态：方案设计，尚未实施
> 关联文档：[桌面端可行性分析](desktop-packaging-analysis-2026-11.md) · [C 方案书](desktop-option-c-proposal-2026-11.md)

## 0. 文档目的

本文定义 A 方案的完整落地方式：**保留现有 Streamlit UI 和全部业务代码，用 PyInstaller 打包，用 pywebview 套一个 Windows 桌面窗口，内部启动本地 Streamlit Server**。

本文回答：

1. A 方案改什么、不改什么；
2. 启动器如何启动和停止 Streamlit；
3. 路径、API Key、日志、checkpoint 怎么处理；
4. PyInstaller 和 Inno Setup 怎么打包；
5. 工期、测试、风险和验收标准。

---

## 1. 执行摘要

### 1.1 结论

A 方案**技术可行，且是当前项目投入产出比最高的桌面化路线**。

| 维度 | 判断 |
|---|---|
| 技术可行性 | 高 |
| 业务层复用率 | 100% |
| UI 层复用率 | 100% |
| 预计工作量 | MVP 1–2 天；可交付版 3–5 天；完整 QA 再加 1–2 天 |
| 最终包体积 | onedir 约 250–450MB；安装包约 150–300MB |
| 启动时间 | onedir 约 3–8 秒；onefile 约 8–20 秒 |
| 推荐度 | **课程答辩、快速交付首选** |

### 1.2 推荐交付形式

~~~text
AI剧本杀-Setup.exe（Inno Setup 安装程序）
  └─ 安装到 %LOCALAPPDATA%/Programs/AIMurderMystery
     ├─ AI剧本杀.exe
     └─ 应用文件 + 内置 Python 运行时
~~~

用户双击桌面快捷方式启动，看到的是独立桌面窗口，不是浏览器地址栏。

### 1.3 不做什么

- 不改 graph.py、nodes.py、rules.py、validators.py、prompts.py；
- 不重写 app.py、ui_*.py；
- 不引入 React、Electron、Tauri；
- 不把 DeepSeek API Key 打进 exe；
- 不承诺离线运行；
- 不把 Streamlit 暴露到局域网，只监听 127.0.0.1。

---

## 2. 现状与 A 方案适配性

### 2.1 当前技术栈

- Python 3.10 64 位；
- Streamlit 1.61.1；
- LangGraph 1.2.11、langchain-core 1.5.4、langchain-deepseek 1.1.0；
- httpx 0.28.1、python-dotenv 1.2.2；
- 172 个测试，全部 fake LLM；
- 无数据库、无外部静态资源服务、无登录系统。

### 2.2 为什么适合 A 方案

- 所有 UI 都是 Streamlit 组件，换壳后原样运行；
- ui_assets.py、ui_audio.py 都是内联 HTML、CSS、JS，没有外部资源路径；
- app.py 的 st.iframe、st.html、st.chat_input、st.download_button 在 Edge WebView2 中都有对应能力；
- 只有一个本地用户，MemorySaver 足够；
- 不需要多窗口、菜单、托盘等原生能力，pywebview 的窗口足够。

### 2.3 A 方案的主要边界

| 边界 | 说明 |
|---|---|
| 不是原生 UI | 本质仍是 Streamlit 页面，只是去掉浏览器地址栏 |
| 依赖 Streamlit Server | 必须启动本地 Tornado、Starlette 服务 |
| 依赖 WebView2 | Windows 10 老机器可能需安装 |
| 单文件启动慢 | onefile 每次解压；正式版建议 onedir |
| 需要网络和 API Key | 不是离线应用 |
| Streamlit 内部 API | streamlit.web.bootstrap 必须锁版本 |

---

## 3. A 方案总体架构

~~~text
┌────────────────────────────────────────────────────────────┐
│ AI剧本杀.exe（launcher）                                   │
│  ├─ 单实例锁                                               │
│  ├─ 首次设置 API Key（keyring）                             │
│  ├─ 找空闲端口                                             │
│  ├─ 启动 streamlit-server.exe 子进程                       │
│  ├─ 轮询 /_stcore/health                                   │
│  ├─ pywebview 打开 http://127.0.0.1:<port>                 │
│  └─ 关闭窗口时终止子进程                                    │
└──────────────────────────┬─────────────────────────────────┘
                           │ 本地 HTTP、WebSocket
┌──────────────────────────▼─────────────────────────────────┐
│ Streamlit Server（内置 Python）                            │
│  ├─ app.py（现有 UI）                                      │
│  ├─ graph.py、nodes.py、rules.py、validators.py            │
│  ├─ LangGraph + MemorySaver 或可选 SqliteSaver             │
│  └─ DeepSeek HTTPS                                        │
└────────────────────────────────────────────────────────────┘
~~~

### 3.1 为什么服务端要单独子进程

streamlit.web.bootstrap.run() 内部会安装 signal handler、阻塞 asyncio event loop，并要求在主线程执行；pywebview 也要求 GUI 主循环在主线程。

因此不要让两者挤在一个线程里，推荐：

~~~text
AI剧本杀.exe                    → 正常双击：启动 pywebview 主窗口
streamlit-server.exe（读取 AI_MM_PORT）→ 内部模式：只启动 Streamlit Server
~~~

launcher 用 subprocess.Popen 启动一份 helper 进程；关闭窗口时终止该子进程。

### 3.2 推荐双 exe 结构

~~~text
AI剧本杀.exe          # windowed launcher，只有窗口和托盘逻辑
streamlit-server.exe  # console helper，启动 Streamlit；由 launcher 以 CREATE_NO_WINDOW 启动
~~~

为什么不用一个 exe 自启动：

- PyInstaller 的 --windowed 会把进程切成 GUI 子系统，sys.stdout / sys.stderr 可能为 None；
- Streamlit 和 logging_config 会往 stdout 写日志，--windowed 下可能报 NoneType 写入错误；
- 双 exe 可以：launcher 无控制台，helper 有合法 stdout 但被 CREATE_NO_WINDOW 隐藏，launcher 可把 helper stdout 重定向到日志文件。

如果坚持单 exe，必须把 logging_config 改成只写文件，并关闭 Streamlit 的 stdout 输出；风险更高，不推荐。

---

## 4. 启动器设计（launcher.py）

### 4.1 启动流程

1. multiprocessing.freeze_support()，PyInstaller 必需；
2. 获取单实例锁，避免重复启动；
3. 读取 %APPDATA%/AIMurderMystery/config.json；
4. 读取 Windows Credential Manager 中的 DeepSeek Key；
5. 如果没有 Key，弹出 Tkinter 首次设置窗口；
6. 找一个空闲端口；
7. 启动 streamlit-server.exe 子进程，设置环境变量，并把它 stdout/stderr 重定向到 %LOCALAPPDATA%/AIMurderMystery/logs/sidecar.log；
8. 轮询 http://127.0.0.1:<port>/_stcore/health；
9. pywebview.create_window 打开窗口；
10. webview.start()；
11. 关闭窗口时 terminate 并等待，必要时 taskkill /T /F。

### 4.2 启动器伪代码

~~~python
import os
import socket
import subprocess
import sys
import time
import webview

def find_free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def wait_health(port: int, timeout: float = 30.0) -> bool:
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:" + str(port) + "/_stcore/health",
                timeout=1,
            ):
                return True
        except Exception:
            time.sleep(0.25)
    return False

def server_exe_path() -> str:
    # 与 launcher 同目录下的 sidecar 子目录；Inno Setup 会一起安装
    return os.path.join(os.path.dirname(sys.executable), "sidecar", "streamlit-server.exe")

def main():
    port = find_free_port()
    env = os.environ.copy()
    env["AI_MM_PORT"] = str(port)
    env["STREAMLIT_SERVER_HEADLESS"] = "true"
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    child = subprocess.Popen(
        [server_exe_path()],
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        if not wait_health(port):
            raise RuntimeError("Streamlit 启动超时")
        webview.create_window(
            "AI 剧本杀",
            "http://127.0.0.1:" + str(port),
            width=1440,
            height=900,
        )
        webview.start()
    finally:
        child.terminate()
        child.wait(timeout=5)
~~~

### 4.3 server_entry.py

~~~python
import os
import sys

from streamlit.web import bootstrap

# PyInstaller 分析入口：显式 import app.py 的依赖模块，确保它们被收进包。
# 不要 import app 本身——app.py 在 import 时会执行 st.set_page_config 等页面逻辑。
import nodes, graph, prompts, rules, validators, game_state, game_session  # noqa: E402,F401
import config, names, locations, detective_assistant, logging_config  # noqa: E402,F401
import ui_audio, ui_assets, ui_launch, ui_characters, ui_start, ui_components, ui_theme  # noqa: E402,F401

def resource_dir() -> str:
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

def main() -> None:
    port = int(os.environ["AI_MM_PORT"])
    app_path = os.path.join(resource_dir(), "app.py")
    bootstrap.run(app_path, False, [], {
        "server.address": "127.0.0.1",
        "server.port": port,
        "server.headless": True,
        "server.fileWatcherType": "none",
        "browser.gatherUsageStats": False,
        "theme.base": "dark",
        "theme.primaryColor": "#C0392B",
        "theme.backgroundColor": "#141110",
        "theme.secondaryBackgroundColor": "#1D1916",
        "theme.textColor": "#E8DCC8",
    })

if __name__ == "__main__":
    main()
~~~

### 4.4 资源路径

PyInstaller onedir 下：

- sys._MEIPASS：打包资源目录；
- os.path.dirname(sys.executable)：exe 所在目录；
- app.py 作为 data 文件加入，resource_dir() 优先取 _MEIPASS，否则取项目目录。

不要用 __file__ 定位安装目录；在冻结环境下它可能指向临时解压目录。

---

## 5. Streamlit Server 启动细节

### 5.1 bootstrap.run 参数

Streamlit 1.61.1 的签名：

~~~python
bootstrap.run(main_script_path, is_hello, args, flag_options)
~~~

必须显式传：

~~~python
{
    "server.address": "127.0.0.1",
    "server.port": port,
    "server.headless": True,
    "server.fileWatcherType": "none",
    "browser.gatherUsageStats": False,
}
~~~

不要用默认端口 8501：多实例或端口占用会冲突。由 launcher 动态找空闲端口。

### 5.2 健康检查

Streamlit 健康接口：

~~~text
http://127.0.0.1:<port>/_stcore/health
~~~

必须等它返回 200 后再开窗口，否则用户会看到 WebView 的错误页或白屏。

### 5.3 主题

.streamlit/config.toml 需要作为 data 文件一起打包；如果担心 CWD 不在应用目录，可在 flag_options 里显式写主题键：

~~~text
theme.base=dark
theme.primaryColor=#C0392B
theme.backgroundColor=#141110
theme.secondaryBackgroundColor=#1D1916
theme.textColor=#E8DCC8
~~~

### 5.4 版本锁定

streamlit.web.bootstrap 是 Streamlit 的内部 API，不是稳定公开接口。

必须锁定：

~~~text
streamlit==1.61.1
~~~

升级 Streamlit 前要在桌面壳中重新跑启动测试。

---

## 6. API Key 与配置

### 6.1 原则

- 绝不把 .env 或 API Key 打进安装包；
- 用户自己的 Key 只保存在 Windows Credential Manager；
- 首次启动引导用户输入；
- 支持更换 Key。

### 6.2 首次启动

推荐 launcher 用 Tkinter 做一个小设置窗口：

~~~text
┌─────────────────────────────┐
│ AI 剧本杀 · 首次设置         │
│ DeepSeek API Key: [________] │
│ [保存并开始]                 │
└─────────────────────────────┘
~~~

保存：

~~~python
import keyring
keyring.set_password("AIMurderMystery", "deepseek", key)
~~~

启动时：

~~~python
key = keyring.get_password("AIMurderMystery", "deepseek")
if not key:
    show_setup_dialog()
os.environ["DEEPSEEK_API_KEY"] = key
~~~

### 6.3 为什么不在 Streamlit 页面里设置

config.py 在 import 时初始化配置，streamlit 脚本已经启动后再改环境变量不一定能影响已创建的 LLM 客户端；在 launcher 启动子进程前设置最可靠。

### 6.4 配置目录

~~~text
%APPDATA%/AIMurderMystery/config.json
  ├─ window_width / window_height
  ├─ audio_volume / muted
  └─ last_game_summary

Windows Credential Manager
  └─ service: AIMurderMystery
      username: deepseek
      password: <API Key>
~~~

不要把 Key 写入 config.json 或日志。

---

## 7. 路径改造清单

### 7.1 新增 paths.py

~~~python
import os

def app_data_dir() -> str:
    base = os.getenv("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "AIMurderMystery")
    os.makedirs(path, exist_ok=True)
    return path

def local_data_dir() -> str:
    base = os.getenv("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "AIMurderMystery")
    os.makedirs(path, exist_ok=True)
    return path
~~~

### 7.2 需要修改的文件

| 文件 | 当前问题 | 改法 |
|---|---|---|
| logging_config.py | 用 __file__ 下 logs/ | 改用 local_data_dir()/logs |
| graph.py | SQLite 写在代码目录 | 改用 local_data_dir()/checkpoints.sqlite |
| config.py | load_dotenv() 依赖 CWD | launcher 设置环境变量；可选加载 %APPDATA%/.env |
| app.py | 无路径假设 | 不需要改 |
| ui_assets.py、ui_audio.py | 全部内联 | 不需要改 |
| main.py | 终端版入口 | 保留，不参与桌面打包 |

### 7.3 为什么不能写安装目录

Inno Setup 默认安装到 %LOCALAPPDATA%/Programs 时通常可写，但如果用户安装到 C:/Program Files，安装目录只读。日志、checkpoint、缓存必须写入用户数据目录。

---

## 8. pywebview 窗口设计

### 8.1 窗口参数

~~~python
webview.create_window(
    "AI 剧本杀 · 推理之夜",
    "http://127.0.0.1:" + str(port),
    width=1440,
    height=900,
    min_size=(1100, 720),
    resizable=True,
    confirm_close=False,
)
webview.start(debug=False)
~~~

### 8.2 WebView2 后端

pywebview 在 Windows 上默认使用 EdgeChromium，需要：

- Windows 10/11；
- Microsoft Edge WebView2 Runtime。

安装器检测注册表：

~~~text
HKEY_LOCAL_MACHINE/SOFTWARE/WOW6432Node/Microsoft/EdgeUpdate/Clients/F3017226-FE2A-4295-8BDF-00C3A9A7E4C5
~~~

如果缺失，运行 MicrosoftEdgeWebView2Setup.exe /silent /install。

### 8.3 兼容性确认

| 功能 | WebView2 兼容性 | 备注 |
|---|---|---|
| st.iframe | 支持 | Chromium 内核 |
| st.html + JS | 支持 | 需允许 unsafe_allow_javascript |
| Web Audio | 支持 | 首次点击后解锁 |
| st.download_button | 支持 | 需验证下载目录权限 |
| st.chat_input | 支持 | 键盘正常 |
| 中文 Markdown | 支持 | 字体回退 |

---

## 9. PyInstaller 打包

### 9.1 新增 requirements-desktop.txt

~~~text
pyinstaller>=6,<7
pywebview>=5,<6
keyring>=25
~~~

### 9.2 构建 launcher（windowed）

~~~powershell
pyinstaller --noconfirm --clean --onedir --windowed ^
  --name "AI剧本杀" ^
  --icon "assets/app.ico" ^
  --collect-all webview ^
  --hidden-import tkinter ^
  --hidden-import webview.platforms.edgechromium ^
  launcher.py
~~~

### 9.3 构建 streamlit-server（console）

~~~powershell
pyinstaller --noconfirm --clean --onedir --console ^
  --name "streamlit-server" ^
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
  server_entry.py
~~~

说明：

- launcher 不 import Streamlit，所以不需要 --collect-all streamlit；
- streamlit-server 负责真正的 Streamlit 运行时，必须收全 static 资源；
- 两个 onedir 目录在 Inno Setup 中合并安装：launcher 在主目录，server 放 sidecar 子目录；
- --windowed 只给 launcher；server 用 --console，但由 CREATE_NO_WINDOW 隐藏；
- 不建议先上 --onefile，启动慢且容易被杀毒误报。

### 9.4 onefile（可选便携版）

~~~powershell
pyinstaller --noconfirm --clean --onefile --windowed ^
  --name "AI剧本杀-Portable" ^
  ...同样参数...
~~~

仅用于 U 盘便携场景；正式版用 onedir。

### 9.5 体积估算

| 部分 | 估算 |
|---|---:|
| launcher Python 运行时 | 20–35MB |
| streamlit-server Python 运行时 | 30–50MB |
| Streamlit + static | 30–40MB |
| LangChain、LangGraph、Pydantic、httpx | 40–80MB |
| 其他依赖和资源 | 50–100MB |
| onedir 总大小 | 约 300–500MB |
| 安装包压缩后 | 约 180–350MB |

实际大小以构建产物为准。

---

## 10. Inno Setup 安装包

### 10.1 安装结构

~~~text
AI剧本杀-Setup-1.0.0.exe
└─ 安装到 %LOCALAPPDATA%/Programs/AIMurderMystery
   ├─ AI剧本杀.exe
   ├─ _internal/
   └─ sidecar/
      ├─ streamlit-server.exe
      └─ _internal/
~~~

### 10.2 安装器要做的事

1. 检查 Windows 10/11 64 位；
2. 检查 WebView2 Runtime，缺失时安装；
3. 复制 onedir 目录；
4. 创建桌面快捷方式；
5. 创建开始菜单项；
6. 注册卸载项；
7. 可选：首次启动创建 %APPDATA%/AIMurderMystery。

### 10.3 Inno Setup 关键配置

~~~text
[Setup]
AppName=AI剧本杀
AppVersion=1.0.0
DefaultDirName={localappdata}/Programs/AIMurderMystery
DefaultGroupName=AI剧本杀
OutputBaseFilename=AI剧本杀-Setup-1.0.0
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Icons]
Name: "{autodesktop}/AI剧本杀"; Filename: "{app}/AI剧本杀.exe"
Name: "{group}/AI剧本杀"; Filename: "{app}/AI剧本杀.exe"

[Run]
Filename: "{app}/AI剧本杀.exe"; Description: "启动 AI 剧本杀"; Flags: nowait postinstall skipifsilent
~~~

WebView2 安装可以在 [Code] 中检测注册表，再 Exec 安装器。

### 10.4 代码签名

- 正式分发建议签名，减少 SmartScreen 警告；
- 课程答辩可先不签，但 Windows 可能提示未知发布者；
- 签名证书需购买，不是打包必需。

---

## 11. 测试计划

### 11.1 构建前

- pytest -q -W error 172 passed；
- ruff check . 通过；
- python -m compileall -q . 通过；
- 手动 streamlit run app.py 全流程可玩。

### 11.2 构建后

1. 干净 Windows 10/11（无 Python）启动；
2. 无 WebView2 的机器安装器处理；
3. 首次启动无 Key：设置窗口；
4. 错误 Key、断网、超时：错误可读；
5. 开始页、选角、讨论、投票、揭晓；
6. 音频首次点击、静音、音量记忆；
7. st.download_button 导出复盘 JSON；
8. 中文路径、中文用户名；
9. 1366×768、1920×1080、2K；
10. 关闭窗口后 taskmgr 无 AI剧本杀.exe 子进程；
11. 端口占用自动换端口；
12. 连续 3 局无 checkpoint 泄漏；
13. 安装、卸载、覆盖升级。

### 11.3 验收指标

| 指标 | 目标 |
|---|---|
| 启动到可交互 | ≤ 8 秒，onedir，SSD |
| 安装包 | ≤ 300MB |
| 空闲内存 | ≤ 500MB |
| 现有功能 | 100% 等价 |
| 测试 | 172+ passed |
| 核心流程崩溃 | 0 |

---

## 12. 实施计划

| 阶段 | 内容 | 人日 |
|---|---|---:|
| P0 | 路径改造、launcher 骨架、健康检查 | 1 |
| P1 | pywebview 窗口和 Streamlit 子进程 | 1 |
| P2 | API Key 设置、日志、checkpoint | 0.5–1 |
| P3 | PyInstaller onedir 构建 | 1 |
| P4 | Inno Setup 和 WebView2 检测 | 0.5–1 |
| P5 | 干净 Windows 回归 | 1–2 |
| 合计 |  | **5–7 人日** |

MVP 可压缩到 1–2 天：launcher、onedir、手填 .env，不做安装器和首次设置。

---

## 13. 风险清单

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Streamlit 内部 API 变更 | 中 | 高 | 锁定 streamlit==1.61.1；升级前重测 |
| WebView2 缺失 | 中 | 高 | 安装器检测并安装 |
| PyInstaller 漏收 Streamlit static | 中 | 高 | --collect-all streamlit + 启动冒烟 |
| st.download_button 在 WebView2 行为异常 | 中 | 中 | 验证；必要时改用原生保存对话框 |
| 音频自动播放被拦截 | 中 | 中 | 首次点击后解锁 |
| API Key 泄露 | 中 | 高 | keyring；不打包 .env；不写日志 |
| 单文件启动慢、杀毒误报 | 高 | 中 | 正式版 onedir；签名 |
| 关闭后子进程残留 | 中 | 中 | Job Object、taskkill、退出钩子 |
| 安装路径只读 | 中 | 中 | 用户数据写 %LOCALAPPDATA% |

---

## 14. A 方案 vs C 方案

| 维度 | A：PyInstaller + pywebview | C：Electron + React + Python |
|---|---|---|
| 工期 | 5–7 人日 | 28–39 人日 |
| UI 复用 | 100% | 30%–50% |
| 业务层复用 | 100% | 80%–90% |
| 桌面体验 | 中，WebView 壳 | 高，原生窗口、菜单、托盘 |
| 包体积 | 250–450MB | 250–400MB |
| 维护成本 | 低 | 高 |
| 适合场景 | 课程答辩、快速交付 | 正式产品、作品集、长期迭代 |

结论：

- 如果目标是答辩时能双击打开、功能完整：选 A；
- 如果目标是真正像桌面软件的成品：选 C；
- 两者可以共用同一套 Python 业务层，A 的代码不会浪费，后续可迁移到 C。

---

## 15. 最终建议

A 方案是最适合当前项目的桌面化路线：

1. 1–2 天做出可双击的 MVP；
2. 3–5 天补首次设置、日志、checkpoint、安装包；
3. 5–7 天完成干净 Windows 回归，达到答辩交付标准；
4. 不重写 UI、不改业务逻辑、不引入前端框架，风险最低。

**建议先做 A 方案保证答辩交付，再视时间决定是否升级 C 方案。**

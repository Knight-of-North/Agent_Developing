# 桌面端 C 方案书：Electron / Tauri + Python Sidecar

> 项目：AI 剧本杀（LangGraph + Streamlit）
> 版本：v1.0
> 日期：2026-11-13
> 状态：方案设计，尚未实施
> 关联文档：[桌面端可行性分析](desktop-packaging-analysis-2026-11.md)

## 0. 文档目的

本文定义 C 方案的完整落地方式：**用独立桌面壳（Electron 或 Tauri）替换 Streamlit 页面，用 Python Sidecar 承载 LangGraph 业务逻辑**，最终交付 Windows .exe 安装包。

本文回答四个问题：

1. C 方案到底改什么、不改什么；
2. Electron 与 Tauri 怎么选；
3. 前后端接口、状态、流式输出、打包怎么做；
4. 工期、风险、验收标准是什么。

---

## 1. 执行摘要

### 1.1 结论

C 方案**技术可行**，但它是“重做 UI 层”，不是“给 Streamlit 套壳”。

| 维度 | 判断 |
|---|---|
| 技术可行性 | 高 |
| 业务层复用率 | 约 80%–90% |
| UI 层复用率 | 约 30%–50%（CSS/视觉可复用，组件需重写） |
| 预计工作量 | 约 28–39 人日；全职 5–8 周，课余 7–10 周 |
| 最终包体积 | Electron 版约 250–400MB；Tauri 版约 200–300MB |
| 推荐度 | 如果目标是“正式桌面产品/作品集”，推荐；如果只是课程答辩，A 方案更划算 |

### 1.2 推荐技术组合

**主方案：Electron 当前稳定版 + React 19 + TypeScript + Vite + Zustand + Python FastAPI/uvicorn Sidecar**

选择 Electron 而不是 Tauri 作为主实现的原因：

- 项目当前没有 Rust 工程经验，Electron 的学习曲线明显更低；
- 现有 ui_*.py 中大量 CSS/HTML/JS 可以直接迁移为 React 组件或静态资源；
- Electron 自带 Chromium，渲染结果与当前 Streamlit 浏览器环境最接近；
- electron-builder 生成 NSIS 安装包成熟，Windows 交付路径清晰；
- Tauri 的优势是体积小、内存低，但它需要 Rust 工具链、WebView2 运行时和 externalBin sidecar 配置，首次落地风险更高。

Tauri 作为备选，接口层设计完全兼容，见第 10 节。

### 1.3 不做什么

- 不重写 graph.py / nodes.py / rules.py / validators.py / prompts.py 的核心业务；
- 不改变游戏规则、不改 LLM prompt 语义；
- 不把 DeepSeek API Key 打进 exe；
- 不承诺离线运行；
- 不把 Streamlit 作为生产运行时依赖（C 方案应彻底移除生产环境的 Streamlit）。

---

## 2. 现状盘点

### 2.1 当前代码规模

| 层 | 文件 | 约行数 | C 方案处理方式 |
|---|---|---:|---|
| UI 编排 | app.py | 1,100 | 拆成 React 页面与路由 |
| 主题/样式 | ui_theme.py | 535 | CSS 迁移/重写为 design tokens |
| 启动动画 | ui_launch.py | 786 | React 组件 + CSS/JS 动画 |
| 音频 | ui_audio.py | 802 | Web Audio 模块移植 |
| 角色视觉 | ui_characters.py | 319 | React 组件（头像/角色卡） |
| 开始页 | ui_start.py | 99 | React 表单组件 |
| 通用组件 | ui_components.py | 281 | React 组件 |
| 图与节点 | graph.py / nodes.py | 1,509 | Python Sidecar 直接复用 |
| Prompt/规则 | prompts.py / rules.py / validators.py | 1,095 | 直接复用 |
| 状态/会话 | game_state.py / game_session.py | 286 | 改造成 GameService |
| 测试 | tests/ | 172 passed | 保留并补 API/E2E 测试 |

结论：**业务层约 3,000 行可复用，UI 层约 4,000 行需要重写或迁移**。这是 C 方案工作量的主要来源。

### 2.2 当前运行依赖

- Python 3.10；
- Streamlit 1.61.1（生产 UI）；
- LangGraph 1.2.11、langchain-core 1.5.4、langchain-deepseek 1.1.0；
- httpx、python-dotenv；
- 无数据库、无 Redis、无外部静态资源服务。

C 方案后端可以保留 Python 3.10 + LangGraph 依赖，但**移除 Streamlit**；新增 FastAPI、uvicorn、keyring；前端新增 Node/pnpm 工具链。

### 2.3 当前产品特征

- 单人 vs AI 群；
- 15 个 LangGraph 节点；
- interrupt 人在回路（选角、发言、投票、平票陈词）；
- 私密线索 / 公开线索 / 调查；
- DM 揭晓与复盘；
- 自定义主题、背景、时间、地点、名字；
- 音频、开场动画、阶段幕布、角色卡、嫌疑度面板、侦探笔记、AI 侦探助手；
- 结算 JSON 导出。

C 方案必须保持以上功能等价。

---

## 3. C 方案总体架构

~~~text
┌────────────────────────────────────────────────────────────┐
│ Electron Main Process（Windows .exe）                      │
│  ├─ 单实例锁 / 窗口管理 / 菜单 / 托盘（可选）              │
│  ├─ 启动 Python Sidecar（随机端口 + 随机 token）           │
│  ├─ 健康检查 / 崩溃重启 / 退出清理                          │
│  └─ 启动/停止 sidecar；Key 由 sidecar 的 keyring 管理       │
└──────────────────────────┬─────────────────────────────────┘
                           │ localhost HTTP + SSE
┌──────────────────────────▼─────────────────────────────────┐
│ React Renderer（Chromium）                                  │
│  ├─ 启动页 / 案件设定 / 选角                                │
│  ├─ 游戏页：对话、角色卡、线索板、调查、投票、复盘           │
│  ├─ Web Audio（BGM/音效）                                   │
│  └─ 设置页：API Key、音量、主题                             │
└──────────────────────────┬─────────────────────────────────┘
                           │ 127.0.0.1:随机端口
┌──────────────────────────▼─────────────────────────────────┐
│ Python Sidecar（FastAPI + uvicorn）                         │
│  ├─ GameService：start / resume / snapshot / review         │
│  ├─ LangGraph：graph.stream / interrupt / checkpointer       │
│  ├─ 现有 nodes / rules / validators / prompts               │
│  └─ 日志、配置、SQLite checkpoint（用户数据目录）            │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTPS
                    DeepSeek API
~~~

### 3.1 进程边界

- **Electron Main**：不做业务逻辑，只负责窗口、sidecar 生命周期、菜单、安装更新；API Key 不经过 Main。
- **Renderer**：纯 UI，不直接持有 API Key，不直接访问文件系统。
- **Python Sidecar**：唯一业务后端，负责 LLM、图状态、规则、日志、持久化。
- **DeepSeek**：外部 HTTPS 服务。

### 3.2 为什么不让 Renderer 直接调用 DeepSeek

- API Key 会暴露在 DevTools / 前端包中；
- prompt 和规则会暴露；
- 无法防止用户篡改剧本状态；
- 后端统一做日志、重试、限流、错误兜底更安全。

---

## 4. Python Sidecar 设计

### 4.1 新增文件

~~~text
server/
├─ main.py              # FastAPI app + uvicorn 启动 + StaticFiles 托管 React dist
├─ routes/
│  ├─ health.py
│  ├─ game.py
│  └─ config.py
├─ schemas.py           # Pydantic 请求/响应模型
├─ game_service.py      # GameSession 的桌面版封装
├─ streaming.py         # LangGraph event -> SSE 映射
├─ paths.py             # %LOCALAPPDATA% / %APPDATA% 路径
└─ static/              # 构建后的 React dist，可选由 FastAPI 同源托管
~~~

### 4.2 GameService 职责

~~~python
class GameService:
    def start(self, request: StartGameRequest) -> StartGameResponse: ...
    def resume(self, game_id: str, action: ActionRequest) -> ResumeResponse: ...
    def snapshot(self, game_id: str) -> GameSnapshot: ...
    def stream(self, game_id: str) -> Iterator[StreamEvent]: ...
    def review(self, game_id: str) -> ReviewResponse: ...
    def cleanup(self, game_id: str) -> None: ...
~~~

内部复用：

- game_session.GameSession 的 start_stream / resume_stream；
- graph.py 的 build_graph()；
- 启动时从 keyring 读取 Key 并注入 os.environ["DEEPSEEK_API_KEY"]，保存新 Key 后立即更新环境变量，避免 get_llm 读到旧值；
- nodes.py 的 ScriptGenerationError、generate_script_stream；
- rules.py 的 judge_ending、apply_followup_guard；
- validators.py 的清洗与校验；
- prompts.py 的 prompt builder。

### 4.3 需要从 app.py 迁出的逻辑

app.py 里不只是 UI，还包含一些“会话状态”逻辑，必须迁到后端或前端：

| 当前逻辑 | 迁移目标 |
|---|---|
| st.session_state.result | 后端 GameService 内存状态 |
| st.session_state.thread_id | 后端 game_id |
| st.session_state.used_names | 后端 GameState.used_names |
| st.session_state.game_history | 前端 localStorage / 后端 SQLite |
| detective_notes_* | 前端 localStorage |
| suspicion_* | 前端 localStorage |
| personal_script_seen | 前端 localStorage |
| review_json_* | 后端生成，前端下载 |
| assistant_advice_* | 后端接口 + 内存缓存 |
| 音频设置 | 前端 localStorage |
| _reset_game_state | 后端 cleanup + 前端清 state |

### 4.4 Sidecar 启动协议

Electron 启动 Python 时传：

~~~text
python_sidecar.exe --port 54321 --token <random-token> --data-dir <appdata>
~~~

Sidecar 只监听 127.0.0.1，所有请求带 Authorization: Bearer <token>。

Sidecar 启动完成后向 stdout 输出一行 JSON：

~~~json
{"event":"ready","port":54321,"pid":12345}
~~~

Electron Main 等待这一行，再创建窗口，避免“窗口先开、后端未就绪”白屏。

---

## 5. HTTP / SSE 接口设计

### 5.1 接口总表

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /api/health | 健康检查 |
| POST | /api/games | 创建新局：主题/风格/背景/时间/地点/自定义名字/节奏 |
| GET | /api/games/{id} | 获取当前快照与 interrupt |
| POST | /api/games/{id}/actions | 提交选角/发言/公开线索/指控/调查/投票 |
| GET | /api/games/{id}/events | SSE：token / message / phase / interrupt / error / done |
| GET | /api/games/{id}/review | 复盘 JSON |
| DELETE | /api/games/{id} | 清理本局 |
| GET | /api/settings/status | 只返回模型名、是否已配置 Key，不返回 Key |
| POST | /api/settings/key | 保存 API Key（sidecar 写入 Windows Credential Manager） |

### 5.2 关键请求模型

~~~typescript
type StartGameRequest = {
  theme: string;
  backgroundStyle: string;
  backgroundStory?: string;
  storyTime?: string;
  storyLocation?: string;
  customNames?: string[];
  roundsPerPlayer: 2 | 3 | 4;
};

type ActionRequest =
  | { type: "choose_role"; role: string }
  | { type: "speak"; text: string }
  | { type: "silence" }
  | { type: "reveal_clue"; clue: string }
  | { type: "accuse"; target: string }
  | { type: "investigate"; location?: string }
  | { type: "vote"; target: string | null }
  | { type: "tiebreak"; text: string };
~~~

### 5.3 SSE 事件

~~~text
event: token
data: {"text":"主持人正在说..."}

event: message
data: {"speaker":"李四","content":"我怀疑张三。"}

event: phase
data: {"phase":"vote","round":6}

event: interrupt
data: {"type":"human_turn","speaker":"张三","targets":["李四","王五"],...}

event: error
data: {"code":"LLM_TIMEOUT","message":"DeepSeek 请求超时，请重试"}
~~~

规则：

- ai_player_turn 的 think 绝不进入 token 事件，只发送最终 message；
- DM 台词可发送 token 事件；
- 每个节点结束发送 message；
- 遇到 interrupt 发送 interrupt 后结束当前 SSE；
- 前端用 EventSource 或 fetch + ReadableStream；Electron 下推荐 fetch 流，便于带 Authorization header。

---

## 6. React 前端设计

### 6.1 页面结构

~~~text
src/
├─ app/
│  ├─ App.tsx
│  ├─ routes.tsx
│  └─ providers.tsx
├─ pages/
│  ├─ LaunchPage.tsx
│  ├─ SetupPage.tsx
│  ├─ RoleSelectPage.tsx
│  ├─ GamePage.tsx
│  ├─ RevealPage.tsx
│  └─ SettingsPage.tsx
├─ components/
│  ├─ chat/
│  ├─ character/
│  ├─ clues/
│  ├─ audio/
│  └─ effects/
├─ stores/
│  ├─ gameStore.ts
│  ├─ uiStore.ts
│  └─ settingsStore.ts
├─ api/
│  ├─ client.ts
│  └─ events.ts
└─ styles/
   ├─ tokens.css
   ├─ theme.css
   └─ motion.css
~~~

### 6.2 视觉迁移原则

- 保留暗色档案风、红/金配色、衬线标题、卷宗/印章/烛光意象；
- ui_theme.py 的 CSS 拆成 design tokens 与全局样式；
- ui_launch.py / ui_components.py 的 HTML/CSS 动画转成 React 组件；
- 头像算法（MD5 取色 + 首字）直接移植到 TypeScript；
- 打字机、印章、幕布、粒子效果用 CSS animation / Web Animations API；
- 不引入外部图片服务，保持零静态资源依赖。

### 6.3 聊天渲染安全

当前 app.py 的规则必须保留：

- 主持人/DM 消息允许 Markdown 渲染；
- 玩家/AI 消息按纯文本渲染；
- 所有用户输入做转义；
- React 使用 react-markdown 时只能启用白名单组件，禁止 rehype-raw；
- 不允许 dangerouslySetInnerHTML 接收 LLM 文本。

### 6.4 音频迁移

ui_audio.py 的 Web Audio 引擎可整体移植为 src/audio/engine.ts：

- 用 AudioContext 合成 BGM / 音效；
- 首次用户手势后 resume()；
- 场景切换幂等；
- 音量/静音存 localStorage；
- 不在 React render 中创建 AudioContext，放在单例模块。

### 6.5 状态管理

- gameStore：当前 GameSnapshot、消息、interrupt、phase；
- uiStore：展开面板、滚动位置、当前 tab；
- settingsStore：音量、主题、API Key 是否已配置；
- 所有持久化 UI 状态：localStorage，key 带 gameId 前缀；
- 游戏结束后清理当前局的 UI key。

---

## 7. Electron Main 设计

### 7.1 职责

- 单实例锁：app.requestSingleInstanceLock()；
- 创建 BrowserWindow，contextIsolation: true、nodeIntegration: false、sandbox: true；
- 生产环境加载 http://127.0.0.1:<port>/app（由 FastAPI 同源托管 React dist，避免 CORS）；开发环境加载 Vite dev server；
- 生成随机 token，启动 sidecar；
- 不读取、不持有 DeepSeek Key；Key 由 Python sidecar 通过 keyring 读取；
- 健康检查失败时显示可读错误，不白屏；
- 退出时 kill sidecar（Windows 用 taskkill /T /F 或 Job Object）；
- 记录 sidecar 日志到 %LOCALAPPDATA%/AIMurderMystery/logs/sidecar.log。

### 7.2 安全设置

- webSecurity: true；
- allowRunningInsecureContent: false；
- 禁止 will-navigate 跳出本地页面；
- setWindowOpenHandler 只允许外部浏览器打开白名单链接；
- CSP：default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'；生产环境同源加载，不需要额外 connect-src；
- 不加载远程 JS/CSS。

### 7.3 首次启动设置（Frontend + Sidecar）

- 如果 sidecar 报告 Key 未配置，React 设置页显示输入框；
- 前端调用 POST /api/settings/key，由 Python sidecar 执行 keyring.set_password("AIMurderMystery", "deepseek", key)，并更新 os.environ["DEEPSEEK_API_KEY"]；
- 设置完成后刷新 /api/settings/status，进入主界面；
- 绝不在 config.json 或 localStorage 中明文保存 Key。

---

## 8. 数据目录与配置

~~~text
%APPDATA%/AIMurderMystery/
├─ config.json              # 主题、音量、窗口大小、最近对局
└─ credentials             # 由 keyring 管理，不手写

%LOCALAPPDATA%/AIMurderMystery/
├─ logs/
│  ├─ game.log
│  └─ sidecar.log
├─ checkpoints.sqlite       # 可选
└─ cache/
~~~

改造点：

- logging_config.py 不再使用 __file__ 下的 logs/；
- graph.py 的 checkpoints.sqlite 不再写安装目录；
- config.py 的 load_dotenv() 改为读取用户配置或由 launcher 注入环境变量；
- 不在安装目录写任何用户数据（Program Files 可能只读）。

---

## 9. 打包与发布

### 9.1 Python Sidecar

新增 requirements-sidecar.txt：

~~~text
fastapi>=0.115
uvicorn[standard]>=0.30
keyring>=25
pyinstaller>=6
~~~

~~~powershell
pyinstaller --noconfirm --clean --onedir --console ^
  --name "aimurder-sidecar" ^
  --hidden-import uvicorn ^
  --hidden-import fastapi ^
  --collect-all langgraph ^
  --collect-all langchain_core ^
  --collect-all langchain_deepseek ^
  --collect-all pydantic ^
  server/main.py
~~~

产物放到 electron/resources/sidecar/。sidecar 使用 --console 构建，Electron spawn 时设置 windowsHide: true；不要给 sidecar 加 --windowed，否则 stdout 可能不可用，无法输出 ready JSON。

### 9.2 Electron

~~~text
electron-builder
├─ target: nsis
├─ artifactName: AI剧本杀-Setup-[version].exe
├─ extraResources: resources/sidecar/**
├─ asar: true
└─ icon: build/icon.ico
~~~

安装包结构：

~~~text
AI剧本杀-Setup-1.0.0.exe
└─ 安装到 %LOCALAPPDATA%/Programs/AIMurderMystery
   ├─ AI剧本杀.exe
   ├─ resources/sidecar/...
   └─ resources/app.asar
~~~

### 9.3 体积估算

| 部分 | 估算 |
|---|---:|
| Electron 运行时 | 100–150MB |
| Python 运行时 + 依赖 | 150–250MB |
| 前端资源 | 5–15MB |
| 安装包压缩后 | 约 200–350MB |
| 安装后 | 约 350–500MB |

### 9.4 签名与更新

- 正式分发建议购买代码签名证书，减少 SmartScreen 警告；
- 内测可先不签名，但要在 README 说明；
- 自动更新可后续用 electron-updater + GitHub Releases；首版不做。

---

## 10. Tauri 备选方案

如果更重视安装包体积与内存，可选 Tauri 2：

| 维度 | Electron | Tauri 2 |
|---|---|---|
| 渲染内核 | 自带 Chromium | 系统 WebView2 |
| 安装包 | 大 | 小 |
| 内存 | 较高 | 较低 |
| 语言 | TypeScript/JS | Rust + TypeScript |
| 学习成本 | 低 | 中高 |
| Windows 兼容 | 最稳 | 依赖 WebView2 |
| Python sidecar | extraResources + spawn | externalBin + shell 插件 |

Tauri 的 Python sidecar 需要在 tauri.conf.json 配置 externalBin，目标文件名带 target triple，例如：

~~~text
binaries/aimurder-sidecar-x86_64-pc-windows-msvc.exe
~~~

如果团队没有 Rust 经验，建议不要为了“体积小”强行上 Tauri。

---

## 11. 测试与验收

### 11.1 自动化测试

- 现有 172 个 Python 测试全部保留；
- 新增 FastAPI 接口测试：TestClient + fake LLM；
- 新增 SSE 合约测试：事件顺序、interrupt 终止、错误事件；
- 新增前端 Vitest：store、消息渲染、角色卡；
- 新增 Playwright E2E：启动页 → 选角 → 发言 → 投票 → 揭晓（fake sidecar）；
- 打包后 smoke test：干净 Windows 虚拟机启动、健康检查、退出清理。

### 11.2 手工验收

1. 无 Python 环境的 Windows 10/11 可启动；
2. 无 API Key 时进入设置页，不崩溃；
3. Key 错误/断网/超时有可读错误；
4. 中文路径、中文用户名正常；
5. 1366×768、1920×1080、2K 布局正常；
6. 首次点击音频可播放，静音/音量记忆；
7. 关闭窗口后任务管理器无 sidecar 残留；
8. 端口冲突时自动换端口；
9. 连续玩 3 局无内存泄漏；
10. 安装/卸载/升级正常。

### 11.3 验收指标

| 指标 | 目标 |
|---|---|
| 启动到可交互 | ≤ 8 秒（SSD） |
| 安装包 | ≤ 400MB |
| 空闲内存 | ≤ 500MB |
| 现有功能 | 100% 等价 |
| 现有测试 | 172+ 全部通过 |
| 崩溃率 | 0（核心流程） |

---

## 12. 实施计划

### 12.1 阶段拆分

| 阶段 | 内容 | 人日 |
|---|---|---:|
| P0 | 冻结 API 契约、数据目录、错误码 | 2 |
| P1 | Python Sidecar + GameService + SSE | 4–6 |
| P2 | Electron 壳 + sidecar 启停 + 健康检查 | 3–4 |
| P3 | React 页面：启动/设定/选角/游戏/结算 | 10–14 |
| P4 | 视觉/音频/动画迁移与打磨 | 5–7 |
| P5 | 打包、安装器、干净 Windows 回归 | 4–6 |
| 合计 |  | **28–39 人日** |

### 12.2 里程碑

- M1：Sidecar 能在本机跑通一局（无 UI）；
- M2：Electron 能启动 sidecar 并显示 React 页面；
- M3：完整流程可玩（UI 功能等价）；
- M4：安装包可在干净 Windows 安装运行；
- M5：答辩演示版冻结。

### 12.3 建议的降风险策略

如果时间不足，按以下顺序砍：

1. 先不做自动更新、代码签名；
2. 先不做托盘/多窗口；
3. 先只做 1366×768 与 1920×1080 两档响应式；
4. 先复用现有 CSS 的视觉，不追求 4K 像素级；
5. 不要为了赶时间退回 Streamlit 套壳——那就是 A 方案，不是 C 方案。

---

## 13. 风险清单

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| UI 重写范围失控 | 高 | 高 | 先锁定页面清单与验收标准；按页面逐个交付 |
| SSE/stream 事件顺序错乱 | 中 | 高 | 定义事件协议 + 合约测试；前端只按事件驱动 |
| Sidecar 进程残留 | 中 | 中 | Job Object / taskkill / 退出钩子 |
| API Key 泄露到前端 | 中 | 高 | 只在 sidecar/keyring；Renderer 不持久化、不写 localStorage |
| WebView2 缺失 | 中 | 高 | Electron 自带 Chromium，风险低于 Tauri |
| 安装包过大 | 高 | 中 | 使用 onedir + 压缩；可接受 300–400MB |
| 音频自动播放被拦截 | 中 | 中 | 首次点击手势后 resume；设置页提示 |
| 现有测试被破坏 | 中 | 高 | 业务层不改；先补 API 测试再动 UI |
| 时间不足 | 高 | 高 | 按里程碑砍非核心功能；不要中途换方案 |

---

## 14. 方案对比与决策建议

| 维度 | A：PyInstaller + pywebview | C：Electron + React + Python |
|---|---|---|
| 工期 | 1–5 天 | 28–39 人日 |
| UI 复用 | 100% | 30%–50% |
| 桌面体验 | 中（WebView 壳） | 高（原生窗口/菜单/托盘） |
| 包体积 | 250–450MB | 250–400MB |
| 维护成本 | 低 | 高 |
| 适合场景 | 课程答辩、快速交付 | 正式产品、作品集、长期迭代 |

**决策建议：**

- 如果目标是“答辩时能双击打开、功能完整”：选 A，不要选 C；
- 如果目标是“做成一个真正像桌面软件的成品”，并且愿意投入 1–2 个月：选 C；
- 如果选择 C，建议先做 **C-MVP**：Electron + React 只实现开始页、选角、聊天、投票、揭晓五个页面，音频/动画第二期再加；
- 不建议在 C 方案中保留 Streamlit 作为生产 UI；那只是 A 方案的变体，会增加一层无意义的依赖。

---

## 15. 结论

C 方案可行，且能显著提升桌面体验，但它的本质是**重做前端 + 拆出 Python 服务层**，不是简单打包。

- 业务层复用率高，现有 LangGraph/规则/prompt 不需要重写；
- 主要成本在 React UI 重写、SSE 流式协议、Electron 进程管理和安装包；
- 推荐 Electron 作为主实现，Tauri 作为体积优先的备选；
- 若时间有限，A 方案更符合课程项目投入产出比；若追求正式桌面产品，C 方案值得做。

**最终建议：先把 C 方案作为“毕业设计/作品集升级路线”，用 A 方案保证答辩交付；两者共用同一套 Python 业务层，后续迁移不会浪费。**

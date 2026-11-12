# 🎭 剧本杀·推理之夜

> 暑假智能体开发学习项目 · LangGraph 多智能体协作实战

---

## 这是什么

一个**能自己开剧本杀的 AI 主持人**——你输入主题和风格，它自动创作案件、设计嫌疑人、分配线索；然后你扮演其中一个嫌疑人，和一群 AI 同台辩论、推理、投票、抓出真凶。整个过程用 LangGraph 把十几个节点串成一张状态图，LLM 只管"说什么"，确定性逻辑只管"算不算、合不合规"。

和网上那些"AI 跑一局"的玩具不同——这是有完整信息差、角色剧本、违规校验、计票机制的硬核实现，能真的玩完一局。

---

## 📸 效果展示

| 启动封面（深夜档案室 · 点击启封） | 开始页（输入主题 / 风格） |
|:---:|:---:|
| ![启动封面](docs/screenshots/00-launch-screen.jpg) | ![开始页](docs/screenshots/01-start-page.png) |

| 选角色（首字头像角色卡） | 主持人开场（左栏角色卡 + 中央故事） |
|:---:|:---:|
| ![选角色](docs/screenshots/02-choose-role.png) | ![DM 开场](docs/screenshots/03-dm-opening.png) |

整套暗色档案风 UI：#141110 黑底 + 金红配色 + Minecraft 风格原创钢琴 BGM（可切悬疑档案）+ 启动封面（顶灯 / 夜雨 / 尘埃 / 火漆印章）+ 开场红章打字机动画 + 阶段幕布。沉浸感是"加分项"不是"主菜"——主菜还是下面的智能体架构。

开始页在 1500px 以上按视口自动放大（容器 `min(92vw, 2300px)`，标题 / 表单 / 说明卡字号使用 `clamp()`），2K/4K 全屏不再缩成小条；1366/1440 仍保持紧凑布局。

---

## 🧠 智能体架构速览

整个游戏跑在 LangGraph 的 `StateGraph` 上，15 个节点依次推进：

```
START
  → generate_script（LLM 生成剧本）
  → distribute_clues（线索按 holder 分发，信息差起点）
  → choose_role ← interrupt（你选角色）
  → dm_intro（DM 开场讲故事）
  → self_intro（AI 嫌疑人自我介绍，线程池并发）
  → ↺ 讨论循环：ai_player_turn / human_turn / dm_midpoint
        ↑ 条件边 route_speaker 路由谁发言（点名插队 / 追问 / 正常轮换）
  → ai_vote（AI 投票，并发 + 注入不在场证明花名册）
  → human_vote ← interrupt（你投票，可弃权）
  → tally（确定性计票，平票走结构化 tie_candidates）
  → tie_break（平票加时：AI 候选人辩护，仅一次 + vote_round 硬保险丝）
  → human_tiebreak ← interrupt（玩家若在平票名单，可做最后陈述）
  → ↺ ai_vote → human_vote → tally（平票重投）
  → final_statement（得票最高者陈词）
  → dm_reveal（DM 揭晓 + 复盘）
  → END
```

**核心机制**：

- **Think / Speak 双通道**——AI 玩家先内心推理（think），再公开发言（speak），只把 speak 展示给你；凶手在心里盘算、在嘴上掩饰
- **interrupt 人在回路**——所有需要你决策的环节（选角色 / 发言 / 投票）都走 `interrupt`，图暂停等你 `Command(resume)`
- **确定性 vs LLM 分层**——LLM 只管"说什么"，校验 / 计票 / 防泄露 / 名单过滤全在 Python 节点里跑（防失控 + 省 token）
- **剧本自洽校验**——LLM 生成后跑 5+ 条硬检测（结构化字段非空、死者 ≥2 条公开边、嫌疑人间 ≥⌈N/2⌉ 条边、关系方向语义），不通过就打回重试
- **凶手狡辩策略池**——LLM 不让凶手自爆，按"被指控次数"循环使用 5 套否认 / 反问 / 嫁祸策略
- **本局复盘卡**——结算页展示真凶 / 你的指认 / 票数 / 线索统计，并支持导出本局 JSON 复盘
- **胜负结算动画**——按玩家视角显示胜利金色碎片 / 失败暗红压场 / 平局灰雾，动画不阻塞复盘操作
- **AI 侦探助手**——只基于公开线索 + 玩家笔记给出可疑点/下一步盘问建议，不读取私密线索或真相
- **场景搜证**——隐藏线索按书房/花园/门厅分配，玩家可选择地点调查，旧剧本自动回退随机调查
- **嫌疑度面板 / 推理笔记**——玩家侧推理辅助，不进入 AI 上下文，不改变规则
- **角色视觉**——姓名首字头像 + 确定性渐变，选角卡 / 侧栏角色卡 / 嫌疑人总览 / 聊天头像保持一致
- **最近战绩**——本会话最近 10 局（胜负 / 角色 / 指认 / 结果）在开始页展示，不落库、刷新清空

---

## 🛠️ 技术栈

| 类别 | 选型 | 为什么 |
|---|---|---|
| **智能体编排** | LangGraph ≥1.0 | 图结构天然契合剧本杀的"阶段推进"，condition edge 处理"轮到谁" |
| **大模型** | DeepSeek `deepseek-v4-flash` | 中文生成稳、价格便宜（每局约 ¥0.1~0.3），适合学习项目预算 |
| **Web UI** | Streamlit ≥1.61 | 5 分钟搭交互界面，专注后端逻辑 |
| **测试** | pytest | 纯函数单元测试，**167 passed / 2.6s**，不烧 token |
| **工程化** | ruff + GitHub Actions | 静态检查 + 双版本 CI；PR 上自动跑，不花一分钱 API 费用 |
| **环境** | conda py10 + pip 清华镜像 | Python 3.10 隔离环境，安装快 |

---

## 🚀 快速开始（5 分钟跑通）

### 第 1 步：环境

```bash
# 推荐 conda（隔离 + Python 3.10）
conda create -n py10 python=3.10 -y
conda activate py10
```

### 第 2 步：装依赖

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# 想要严格复现我的环境（推荐）：
# pip install -r requirements.lock -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 注意：`requirements.txt` 是兼容区间，`requirements.lock` 是从实装环境读出的精确版本。
> 下限不是随便写的——`streamlit>=1.61` 是 `_THEME_CSS` 校准过的 DOM 版本，
> `langchain-deepseek>=1.1` 是 `reasoning_effort` 参数存在的版本。

### 第 3 步：填 API Key

```bash
cp .env.example .env       # Windows: copy .env.example .env
# 编辑 .env，把 DEEPSEEK_API_KEY=... 换成你的 key
```

到 [platform.deepseek.com](https://platform.deepseek.com) 注册并创建 key（需充值少量余额，每局约 ¥0.1~0.3）。

### 第 4 步：启动

> **VS Code 快速启动**：用 VS Code 打开 `Agent_Developing` 仓库根目录（`.vscode/settings.json` 所在目录，不要打开上一级课程目录），新建集成终端会自动激活 `Conda py10`，直接执行 `streamlit run app.py` 即可。修改该配置后需要关闭旧终端，重新开一个终端（或重启 VS Code）。

```bash
# 推荐：Web 版（浏览器体验最好）
streamlit run app.py
# → 浏览器自动打开 http://localhost:8501

# 备选：终端版
python main.py
```

---

## 🎮 怎么玩一局

1. **点击启动封面「开始游戏」** → 输入主题 → 选风格 → 可选自定义剧情背景 / 时间 / 地点 → 点生成
2. **选角色**：从嫌疑人里挑一个你扮演（其他人都是 AI）
3. **读个人剧本**：你的身份、秘密、与死者的关系、你手里的私密线索——**别人不知道你的秘密**
4. **讨论环节**：轮流发言，可以：
   - 💬 发言 / 推理 / 质问（或输入「沉默」保持缄默）
   - 🔍 调查隐藏线索（有限次数，随机发放）
   - 🗂️ 公开线索给所有人
   - ⚡ 指控某人（对方必须回应）
5. **投票**（可投「弃权」）→ **DM 揭晓**（公布真相 + 复盘谁被埋没）

一局大约 15~30 分钟。

---

## 🔊 声音设计（Web Audio 合成，无外部音频素材）

声音系统参考了 [Wolfcha](https://github.com/oil-oil/wolfcha) 等狼人杀项目的组织方式——**BGM 随阶段切换 + 每个流程节点独立提示音 + 音乐/音效分离控制**，
再按剧本杀的"卷宗 / 推理 / 投票 / 揭晓"语境重新配器。默认 BGM 为 **Minecraft 风格原创钢琴氛围**：
参考 C418 为《Minecraft》创作的极简钢琴 / 长混响 / 大量留白的气质，但旋律与和弦进行全部为本项目原创，
**不内嵌任何 Minecraft 原曲**（原曲受版权保护，不能随项目分发）；如果自己有授权的音频文件，也可以后续替换为本地曲目。

控制面板的「⛏ 音乐风格」按钮可在 **Minecraft 风格** 与 **悬疑档案** 之间一键切换；两种风格共用下面的阶段映射。

| 游戏环节 | 背景音乐（Minecraft 风格） | 事件音效 |
|---|---|---|
| 启动封面 / 开始页 | 方块 · 晨光（稀疏钢琴 + 暖色弦垫） | 点击「开始游戏」→ 火漆印章 + 钟声 |
| 点击「立案侦查」 | 继续晨光主题 | 翻卷宗 + 打字机 + 盖章 |
| 选角色 / 开场动画 | 晨光主题压低 | 打字机逐字声 + 红章落章声 |
| 讨论阶段 | 方块 · 远行（加入轻微行走感低音） | 讨论开始钟声；轮到玩家时双音提示 |
| 投票 / 平票加时 | 方块 · 夜色（低音钢琴，克制而紧张） | 铜锣开场、钟表滴答、平票警报 |
| 真相揭晓 / 结局 | 方块 · 回响 → 归乡 / 余烬 | 铜锣 + 弦乐惊悚音、胜利/失败琶音 |
| 任意按钮 / 输入回车 | — | 轻点击、发送提示音 |

**控制方式**：侧边栏「♫ 声音」面板可独立开关音乐/音效、调节总音量、切换 Minecraft / 悬疑风格，
设置写入 `localStorage` 跨刷新记忆。浏览器自动播放策略要求先有一次用户交互，因此首次点击页面任意位置
（或「开始游戏」）后音频引擎才启动；之后 Streamlit rerun 不会重建 AudioContext，BGM 不闪断。

实现位置：`ui_audio.py`（音色库 / 生成式 BGM 调度 / 控制面板），`app.py` 只负责在各阶段调用
`set_audio_scene()` 与 `play_audio_cue()`。旧版雷雨氛围音已移除，改为纯悬疑配乐或 Minecraft 风格配乐。

---

## 🗂️ 项目结构

```
Agent_Developing/
├── app.py               # Streamlit Web UI（推荐入口；页面编排）
├── ui_theme.py          # 全局暗色主题 CSS
├── ui_assets.py         # 开场动画 / 阶段幕布 HTML（纯视觉）
├── ui_audio.py          # 声音系统：生成式 BGM + 分环节音效 + 控制面板
├── ui_launch.py         # 首次启动的沉浸式封面 / 启动界面
├── ui_characters.py     # 角色视觉：首字头像 / 选角卡 / 角色卡 / 嫌疑人总览
├── ui_start.py          # 开始页配置与渲染函数
├── ui_components.py     # 游戏页 UI 渲染组件（进度 / 动画）
├── main.py              # 终端交互版入口
├── graph.py             # LangGraph 图编排（节点注册 + 条件边 + checkpointer）
├── nodes.py             # 节点函数：LLM 节点 + 确定性节点（编排层，已拆薄）
├── rules.py             # 游戏规则引擎（纯函数：线索检测 / 点名 / 守卫 / 计票）
├── prompts.py           # prompt 构建（含人物关系铁律 / 信息差铁律等）
├── validators.py        # 解析 / 规范化 / 兜底（JSON 解析、名单过滤、线索生成）
├── interrupt_handler.py # interrupt 中断处理（类型识别、校验、沉默/弃权保留字）
├── game_session.py      # 会话抽象层（图生命周期 + interrupt 提取，双端共用）
├── game_state.py        # 共享状态定义（TypedDict + Annotated reducer）
├── locations.py         # 搜证地点映射（隐藏线索 → 书房/花园/门厅）
├── detective_assistant.py # AI 侦探助手 prompt（仅公开信息 + 玩家笔记）
├── names.py             # 嫌疑人名字池 + 抽样
├── logging_config.py    # 日志配置（文件轮转 + 对局 ID 上下文注入）
├── config.py            # 集中配置（温度 / 轮数 / 限制，全量环境变量可覆盖）
├── tests/               # pytest 单元测试（167 passed，含 2026-11 回归集）
├── scripts/
│   └── test_api.py      # API 连通性测试（手动脚本，pytest 不收集）
├── docs/
│   ├── screenshots/     # README 截图
│   ├── launch-screen-design.md  # 启动封面设计说明（参考界面 / 动效 / 验收）
│   ├── optimization-proposal-2026-11.md  # 优化方案书（对标开源项目）
│   └── deep-review-2026-08/  # 深度审查报告（27 项问题全量修复档案）
├── diagnose.py          # 调试脚本（看 LLM 原始 JSON 返回）
├── diag.py              # 网络诊断（trust_env 开/关对比，排查代理）
├── pytest.ini           # 测试发现限定 tests/（防真实 API 调用被误收集）
├── pyproject.toml       # ruff 静态检查配置（E/W/F/I/B 规则集）
├── requirements.txt     # 兼容区间（下限与代码实际 API 对齐）
├── requirements.lock    # 精确版本（由实装环境读出，保证可复现）
├── .streamlit/
│   └── config.toml      # 暗色主题 / 主题红 / 工具栏最小化
├── .github/workflows/   # CI：ruff + pytest，2 个 Python 版本
├── LICENSE              # MIT
└── .env.example         # 环境变量模板（29 项全量注释）
```

**模块边界**：`nodes.py` 只做编排（调用 LLM + 调确定性函数），确定性游戏规则全在 `rules.py`、业务逻辑在 `validators.py` / `prompts.py` / `interrupt_handler.py` 里——这是为了**让测试不依赖 LLM**，167 个测试全是纯函数。

**配置生效链路（2026-11 修复）**：`.env` 由 `config.py` 在 **import 时**加载，先于任何 `os.getenv`。此前 `load_dotenv()` 写在 `nodes.py` 且排在 `from config import ...` 之后，导致 `.env` 里 24 项配置静默失效（只有调用期才读的 `DEEPSEEK_*` 生效）。改完 `.env` 需重启进程。

---

## 🧪 测试与质量

```bash
pytest -q              # → 167 passed in 2.6s（pytest.ini 已限定只扫 tests/）
ruff check .           # → All checks passed（未用导入 / 未定义名 / import 排序）
```

CI（`.github/workflows/ci.yml`）在每次 push/PR 上跑 Python 3.10 + 3.12 两个版本，
**全程不发真实 LLM 请求**——纯函数测试是零成本的，这正是不把业务逻辑塞进节点的回报。

测试覆盖（**不烧 token**）：
- JSON 解析 / 名单过滤 / 线索兜底
- 自洽校验（5+ 条硬检测 + 重试反馈）
- 计票 / 投票校验 / 结局判断（含弃权单列）
- 凶手狡辩策略池
- 2-gram 线索检测（转述命中 / 同字异义不误报）
- 点名→追问守卫（str/dict 协议等价性）
- 插队不计费 + 保险丝（M11）
- 平票结构化候选传递（L4）
- **2026-11 回归集**（`tests/test_hardening.py`，11 类）：`.env` 生效时序、forbidden 停用词清洗、
  指控计数认目标、非字符串 secret 不炸流程、prompt 无 Python repr、玩家不可自投、
  并发助手只重跑失败项、`distributed_clues` 增量合并
- **声音系统契约**（`tests/test_audio.py`）：BGM 场景 / 全关卡音效齐全、控制面板音乐/音效双开关、
  雷雨旧音彻底清除、阶段幕布不再自建 AudioContext、`app.py` 各阶段接入点不丢失
- **角色视觉**（`tests/test_character_ui.py`）：头像确定性 / 首字取色、LLM 字段 HTML 转义、
  角色卡头与嫌疑人总览渲染

**故意不测**：图流程端到端 mock 冒烟——这是测试盲区，留给开学后（frozen 测试需要 mock LLM，不划算）。

---

## 💣 踩过的坑（学习痕迹，避雷指南）

暑假里这些坑挨个踩过，记下来给后面的同学：

| 坑 | 现象 | 解决 |
|---|---|---|
| **Streamlit baseweb 组件白底** | 输入框 / selectbox / expander 在暗色主题下有白底块 | `*` 通配穿透 + `background-image: none !important` + `box-shadow: none !important` 兜底；模板见 `app.py` `_THEME_CSS` |
| **Streamlit 热重载失效** | 改 `nodes.py` / `prompts.py` / `app.py` 后页面没刷新 | **Ctrl+C 重启**——热重载对这些模块不生效（CSS / HTML 改动才会热重载） |
| **`streamlit` 命令报 Fatal error in launcher** | 环境目录从 Anaconda 改名到 Miniconda 后，旧的 `streamlit.exe` 里还嵌着旧 Python 路径；`python -m streamlit run app.py` 却正常 | 重装包重建入口：`python -m pip install --force-reinstall --no-deps streamlit==1.61.1`；`pytest` 同类问题同样重装 |
| **`st.iframe` 不支持 key** | 跨局重播动画/幕布会卡 | 跨局重播靠 HTML 里拼 `data-tid`（thread_id），React 靠"位置稳定+HTML 常量"复用；音频改由顶层引擎持有，不受 iframe 重挂载影响 |
| **手机热点 IP 风控** | `APIConnectionError` | **不是代码 bug**——换网络或等几分钟重试 |
| **LLM 偷懒只讲故事不填 JSON** | 角色卡显示「待补充」 | 5 条硬检测 + 重试反馈机制（`_check_script_consistency`） |
| **`deepseek-chat` 模型 404** | 2026-07 已退役 | `.env` 改用 `DEEPSEEK_MODEL=deepseek-v4-flash` |
| **`relations` 方向反** | LLM 写的 from/to 跟 rel 语义主语反了（"我恨 A" 写成 A→我） | prompt 铁律 + 校验兜底：from 是主语，to 是宾语 |
| **`.env` 改了完全没反应** | 调 `ROUNDS_PER_PLAYER` / `LLM_TEMP_*` 全部无效，只有 `DEEPSEEK_*` 生效 | **import 顺序问题**：`config.py` 的常量在 import 时求值，而 `load_dotenv()` 写在 `nodes.py` 且排在 `from config import` 之后 → 24 项配置读到的是默认值。修法：`load_dotenv()` 挪到 `config.py` 最顶部。教训：**模块级常量 + 环境变量 = 必须保证加载顺序** |
| **`forbidden` 混进「我」** | AI 每句台词都被判"泄露秘密"，3 次重试后整局都在说同一句兜底话 | forbidden 是泄密检测的词表，本身必须有区分度：入库前剔停用词/角色名，并去重截断（`validators._sanitize_forbidden`） |
| **"致命问题"靠字符串子串判定** | 改一句校验文案，`fatal` 分类静默失效；两处副本还会各自漂移 | 把"是否致命"变成数据（`ScriptProblem.fatal`），而不是 `"murderer" in p` 这种文本嗅探 |

---

## 🧭 给同做大一大二智能体项目的同学

如果你是第一次接触 LangGraph + 多智能体，建议按这个顺序跟：

1. **先跑通**：把项目跑起来，玩完一局，先有"体感"
2. **读 `graph.py` + `game_state.py`**：理解 StateGraph 是怎么把节点串起来的（这是 LangGraph 最核心的概念）
3. **读 `nodes.py` 的 4 个确定性节点**（`choose_role_node` / `tally_node` / `route_speaker` / `_check_script_consistency`）——它们**不调 LLM**，是"图的骨架"
4. **读 `prompts.py` 的 prompt 铁律**——理解"怎么用结构化约束让 LLM 不偷懒"（这是工程化的关键）
5. **读 `interrupt_handler.py`**——理解人在回路怎么用 `Command(resume)` 把图接起来
6. **自己改一个 prompt 铁律 + 加一个测试**——边做边学最快

**推荐配套教程**：`ai-agents-from-zero-hnu` 仓库 27 章 LangGraph + 面试题体系。

---

## ❓ FAQ

| 问题 | 解答 |
|---|---|
| **一局要多少钱？** | 约 ¥0.1~0.3（DeepSeek 按 token 计费），自定义剧情背景 / 长讨论会更贵 |
| **能纯本地跑吗？** | 不能，剧本生成必须调 LLM；其他确定性逻辑全在本地 |
| **Streamlit 改了代码不生效？** | `nodes.py` / `prompts.py` / `app.py` 改完要 **Ctrl+C 重启**（CSS / HTML 改动会热重载） |
| **为什么用 DeepSeek 而不是 GPT？** | 中文生成稳 + 便宜。¥3.28 余额能跑很多局，预算友好 |
| **能多玩家局域网联机吗？** | 当前是单玩家 vs AI 群。多人联机是开学后方向 |

---

## 📝 致谢

- **LangGraph** 团队——状态图编程模型的设计哲学
- **DeepSeek**——便宜的中文大模型 API
- **`ai-agents-from-zero-hnu`** 教程——27 章 LangGraph 入门资料
- **`loverGraph`** 项目——"确定性 vs LLM 分层"的设计思想借鉴

---

> 📅 **项目周期**：2026 年暑假（大一升大二）
> 👤 **作者**：舒飞（湖南师范大学 · 软件工程 2025 级 · 数字智能方向）
> 🎯 **目标**：智能体开发课程项目产出
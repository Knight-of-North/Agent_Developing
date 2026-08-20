# AI 剧本杀主持人（Murder Mystery Host）

一个基于 **LangGraph** 的多智能体协作系统：自动生成剧本、主持游戏流程、模拟 AI 玩家发言，并管理线索与投票。**你（真人）扮演一个角色**，与多个 AI 嫌疑人一起讨论、推理、投票，体验完整的剧本杀流程。

> 💡 **新手体验提示**：下面的「快速开始」带你 5 分钟跑起来，「怎么玩一局」教你完整流程。想深入底层原理（LangGraph 怎么编排的）再看文末的「核心概念」。

---

## ✨ 演示效果

| Web 版界面（推荐） | 角色卡 + 人物关系图 |
|---|---|
| 截图位：`screenshots/ui.png` | 截图位：`screenshots/rolecard.png` |

> 📷 运行后自行截图放入 `screenshots/` 目录（或直接替换上面两行），让 README 更直观。

- 🎭 **多智能体协作**：剧本生成、DM 主持、AI 玩家（多个嫌疑人）、确定性工具节点各司其职
- 🧠 **Think / Speak 双通道**：AI 玩家先内心推理（think），再公开发言（speak），只把 speak 展示给你
- 🗳️ **投票环节**：讨论结束后投票指认凶手，确定性节点统计票数，DM 对比投票与真相
- 🛡️ **防跑飞校验**：AI 玩家发言若泄露秘密（禁忌词），自动打回重说
- 👤 **人在回路**：你扮演一个角色，通过 interrupt 实时参与讨论和投票
- 🔍 **调查与指控**：你可以公开线索、正式指控某人、调查隐藏线索，主动推进剧情

---

## 🚀 快速开始（5 分钟跑通）

### 前置要求

| 依赖 | 说明 |
|---|---|
| **Python 3.10+** | 推荐用 conda 创建独立环境（见下） |
| **DeepSeek API Key** | 到 [platform.deepseek.com](https://platform.deepseek.com) 注册并创建 key（需充值少量余额） |

### 第 1 步：创建并激活环境

```bash
# conda 方式（推荐）
conda create -n py10 python=3.10 -y
conda activate py10

# 或者用系统 Python 的 venv
# python -m venv py10 && py10/Scripts/activate   # Windows
# python -m venv py10 && source py10/bin/activate  # macOS / Linux
```

### 第 2 步：安装依赖（建议先配清华镜像源，速度快很多）

```bash
pip install -r requirements.txt
```

### 第 3 步：配置密钥

复制 `.env.example` 为 `.env`，填入你的 DeepSeek key：

```bash
cp .env.example .env   # Windows 用: copy .env.example .env
```

然后用编辑器打开 `.env`，把 `DEEPSEEK_API_KEY=你的key` 换成真实 key。

> ⚠️ **模型注意**：`.env` 里 `DEEPSEEK_MODEL` 默认是 `deepseek-v4-flash`（`deepseek-chat` 已于 2026-07 退役，不要用旧模型名）。

### 第 4 步：启动

```bash
# 方式一（推荐）：Web 图形界面版 —— 浏览器操作，体验最佳
streamlit run app.py

# 方式二：终端交互版 —— 纯命令行，适合无图形环境
python main.py
```

启动后浏览器会自动打开（默认 `http://localhost:8501`）。

---

## 🎮 怎么玩一局

1. **输入主题**：填一个主题（如「民国豪门恩怨」「校园怪谈」）和风格，可选自定义剧情背景 / 嫌疑人名字 / 时间 / 地点
2. **生成剧本**：点击生成，LLM 自动创作案件、人物、线索（流式显示，等它写完）
3. **选择角色**：从嫌疑人里选一个你扮演的角色
4. **读个人剧本**：开局读自己的「小册子」——你的身份、秘密、与死者的关系、你掌握的私密线索（**别人不知道你的秘密**）
5. **讨论环节**：大家轮流发言，你可以：
   - 💬 **发言**：发表推理、质问别人
   - 🔍 **调查**：搜索隐藏线索（有限次数）
   - 🗂️ **公开线索**：把你手里的线索公开给所有人
   - ⚡ **指控**：正式指控某人是凶手（对方必须回应）
6. **投票**：讨论结束，所有人投票指认凶手
7. **DM 揭晓**：公布真相、对比投票，复盘哪些线索被埋没

> 一局大约 15~30 分钟（视讨论深度），会消耗少量 token（见 FAQ）。

---

## 🗂️ 项目结构

```
Agent_Developing/
├── app.py               # Streamlit Web UI（推荐入口：streamlit run app.py）
├── main.py              # 终端交互版入口（python main.py）
├── graph.py             # LangGraph 图编排（节点注册、条件边、checkpointer）
├── nodes.py             # 节点函数：多智能体 + 确定性节点（纯编排，已拆薄）
├── names.py             # 嫌疑人名字池 + 抽样（_parse_names / _pick_suspect_names）
├── prompts.py           # prompt 构建（_build_script_prompt）
├── validators.py        # 解析/规范化/兜底（_parse_json / _enforce_names / _extract_speak 等）
├── interrupt_handler.py # interrupt 中断处理（类型识别、角色/投票校验）
├── visualization.py     # 人物关系图可视化（pyvis 生成交互 HTML）
├── game_state.py        # 共享状态定义（TypedDict + reducer）
├── tests/               # 纯函数单元测试（pytest，不烧 token）
├── diagnose.py          # 调试脚本：查看模型对"输出JSON"的原始返回
├── diag.py              # 网络诊断：直连测试 DeepSeek（trust_env 开/关对比，排查代理问题）
├── test_api.py          # API 连通性测试：跑一次真实调用（参数与 nodes.py 一致）
├── docs/                # 代码审查报告 / 玩法评估报告 / 调研报告（归档）
├── requirements.txt     # 依赖清单
└── .env.example         # 密钥配置模板
```

---

## 🔍 图结构（13 个节点）

```
START
  → generate_script（生成剧本）
  → distribute_clues（线索分发，信息差）
  → choose_role（你选角色，interrupt）
  → dm_intro（DM 开场）
  → self_intro（AI 自我介绍）
  → [讨论循环：ai_player_turn / human_turn / dm_midpoint]
      ↑           （条件边 route_speaker 路由）
  → ai_vote（AI 投票）
  → human_vote（你投票，interrupt）
  → tally（计票）
  → final_statement（得票最高者最终陈词）
  → dm_reveal（DM 揭晓 + 复盘）
  → END
```

---

## ❓ 常见问题（FAQ）

| 问题 | 解决方法 |
|---|---|
| **启动报错 `DEEPSEEK_API_KEY` 缺失 / AuthError** | 检查 `.env` 是否存在、key 是否填对（`cp .env.example .env` 后编辑） |
| **报错 `APIConnectionError`** | 网络问题。手机热点常见（IP 风控）——换网络或等几分钟重试，不是代码 bug |
| **改了代码不生效** | Streamlit 热重载有坑：改了 `nodes.py` / `prompts.py` / `app.py` 等后必须 **Ctrl+C 重启**，热重载不会自动加载 |
| **`deepseek-chat` 模型报错 / 404** | 该模型已于 2026-07 退役，`.env` 里改用 `DEEPSEEK_MODEL=deepseek-v4-flash` |
| **一局要花多少钱 / token** | 每次对话调用 DeepSeek API 按量计费，一局约 1~3 元（¥3.28 级）。想省 token 就少自定义剧情背景、讨论别太长 |
| **生成剧本时角色卡显示「待补充」** | 多发生在自定义剧情背景只写了主旨没写细节时——LLM 已加"自由发挥补全"铁律，重试一次或给背景补点人物信息 |
| **`python main.py` 黑窗口无法输入中文** | 终端交互版建议用 Web 版（`streamlit run app.py`），体验更好 |

---

## 🧠 核心概念（LangGraph 学习要点，进阶阅读）

> 想深入理解项目怎么用 LangGraph 编排的，再读这一节。

| 概念 | 作用 | 对应代码 |
|---|---|---|
| **StateGraph** | 用图定义游戏流程 | `graph.py` |
| **共享状态 State** | 所有节点共享的"舞台" | `game_state.py` 的 `GameState` |
| **reducer** | `messages` 用 `operator.add` 追加而非覆盖 | `Annotated[list, operator.add]` |
| **条件边** | 根据状态路由（轮到谁发言） | `add_conditional_edges` + `route_speaker` |
| **循环** | 讨论环节的多轮发言 | 条件边返回自己形成环 |
| **确定性节点** | 统计票数、防泄露校验（不调 LLM） | `tally_node`、防跑飞校验 |
| **interrupt** | 人在回路，暂停等用户输入 | `human_turn_node` + `Command(resume)` |
| **checkpointer** | 图暂停时保存进度 | `MemorySaver` + `thread_id` |

## 💡 关键设计思想

1. **流程控制 vs 内容生成分离**：LLM 负责"说什么"（生成剧本、台词），确定性逻辑负责"算什么、合不合规"（统计票数、防泄露校验）。这是从 [loverGraph](https://github.com/Baillei/loverGraph) 借鉴的核心思想。
2. **Think/Speak 双通道**：AI 玩家的"内心戏"和"公开台词"分离，凶手在心里盘算、在嘴上掩饰。
3. **拥抱不确定性**：模型输出可能不合规（泄露秘密、格式错误），通过"确定性校验 + 重试"来兜底。
4. **信息差设计**：每个玩家只看到自己的秘密和线索，真相散落在不同人手里，必须靠讨论拼凑。

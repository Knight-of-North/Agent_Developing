# AI 剧本杀主持人（Murder Mystery Host）

一个基于 **LangGraph** 的多智能体协作系统：自动生成剧本、主持游戏流程、模拟 AI 玩家发言，并管理线索与投票。**你（真人）扮演一个角色**，与多个 AI 嫌疑人一起讨论、推理、投票，体验完整的剧本杀流程。

## 技术栈

- **编排**：LangGraph（StateGraph 图编排、条件边、循环、checkpointer）
- **模型**：DeepSeek V4-Flash（`langchain-deepseek`）
- **人机交互**：LangGraph `interrupt`（人在回路 / HITL）

## 功能特性

- 🎭 **多智能体协作**：剧本生成、DM 主持、AI 玩家（多个嫌疑人）、确定性工具节点各司其职
- 🧠 **Think / Speak 双通道**：AI 玩家先内心推理（think），再公开发言（speak），只把 speak 展示给玩家
- 🗳️ **投票环节**：讨论结束后投票指认凶手，确定性节点统计票数，DM 对比投票与真相
- 🛡️ **防跑飞校验**：AI 玩家发言若泄露秘密（禁忌词），自动打回重说
- 👤 **人在回路**：用户扮演一个角色，通过 interrupt 实时参与讨论和投票

## 运行说明

```bash
# 1. 创建并激活环境
conda create -n py10 python=3.10 -y
conda activate py10

# 2. 安装依赖（建议先配清华镜像源）
pip install -r requirements.txt

# 3. 配置密钥：复制 .env.example 为 .env，填入你的 DeepSeek key
#    注意：DEEPSEEK_MODEL 用 deepseek-v4-flash（deepseek-chat 已于 2026-07 退役）

# 4. 运行
python main.py
```

## 项目结构

```
Agent_Developing/
├── main.py          # 入口：交互式游戏（含 interrupt 恢复循环）
├── graph.py         # LangGraph 图编排（节点注册、条件边、checkpointer）
├── nodes.py         # 节点函数：多智能体 + 确定性节点（纯编排，已拆薄）
├── names.py         # 嫌疑人名字池 + 抽样（_parse_names / _pick_suspect_names）
├── prompts.py       # prompt 构建（_build_script_prompt）
├── validators.py    # 解析/规范化/兜底（_parse_json / _enforce_names / _extract_speak 等）
├── game_state.py    # 共享状态定义（TypedDict + reducer）
├── tests/           # 纯函数单元测试（pytest，不烧 token）
├── diagnose.py      # 调试脚本（查看模型原始返回）
├── requirements.txt # 依赖清单
└── .env.example     # 密钥配置模板
```

## 图结构

```
START → generate_script → dm_intro → [讨论循环：用户/AI 轮流发言]
                                      （条件边 route_speaker 路由）
     → ai_vote → human_vote → tally → dm_reveal → END
```

## 核心概念（LangGraph 学习要点）

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

## 关键设计思想

1. **流程控制 vs 内容生成分离**：LLM 负责"说什么"（生成剧本、台词），确定性逻辑负责"算什么、合不合规"（统计票数、防泄露校验）。这是从 [loverGraph](https://github.com/Baillei/loverGraph) 借鉴的核心思想。
2. **Think/Speak 双通道**：AI 玩家的"内心戏"和"公开台词"分离，凶手在心里盘算、在嘴上掩饰。
3. **拥抱不确定性**：模型输出可能不合规（泄露秘密、格式错误），通过"确定性校验 + 重试"来兜底。

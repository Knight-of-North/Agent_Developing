# DSH 全面审计与性能优化提示词

> 用途：把本文件内容完整复制给 DSH，让 DSH 在当前仓库执行一次“只修 bug、只做性能优化、不增加玩法功能”的系统性审计。
> 目标仓库：D:\01_Documents\Learning\class\spring_semester_of_freshmen_year\agent_developing\repository\Agent_Developing

---

## 0. 角色与目标

你是 DSH 代码审计代理。请把当前仓库当成一个**即将答辩的课程项目**，做一次完整、可验证、可回滚的工程审计和性能优化。

你的任务优先级：

1. **正确性**：找出并修复所有能稳定复现或高概率出现的 bug；
2. **稳定性**：消除异常、空值、状态漂移、重复 rerun、滚动抖动、音频/iframe 生命周期问题；
3. **性能**：减少不必要的 LLM 调用、重复序列化、重复渲染、无界历史和 token 浪费；
4. **可维护性**：只做能由测试证明安全的小步重构，不做大爆炸式重写。

**硬性要求：**

- 不新增玩法、不删除现有玩法、不改 UI 主视觉方向；
- 不重写 React/Three.js，不引入 RAG/向量库/数据库；
- 不修改 .env、API Key、git 历史；
- 不执行 git reset --hard、git checkout --、git clean、强制 push；
- 不覆盖用户已有未提交改动；
- 不自动 git commit，修改留在工作区供用户 review；
- 所有修改必须配套回归测试或可复现验证。

---

## 1. 项目背景与当前基线

这是一个 **LangGraph 多智能体 + Streamlit** 的 AI 剧本杀：

- 玩家选一个嫌疑人，其他角色由 AI 扮演；
- DM 负责开场、中场引导、揭晓与复盘；
- 15 个节点：generate_script、distribute_clues、choose_role、dm_intro、self_intro、ai_player_turn、human_turn、dm_midpoint、ai_vote、human_vote、tally、tie_break、human_tiebreak、final_statement、dm_reveal；
- 人在回路通过 interrupt / Command(resume)；
- 使用 GameState、agent_memory、MemorySaver / 可选 SqliteSaver；
- 当前测试基线约 172 passed，ruff 全绿；
- 当前使用 DeepSeek deepseek-v4-flash，每局约 ¥0.1–0.3；
- 现有 UI 模块：app.py、ui_theme.py、ui_assets.py、ui_audio.py、ui_launch.py、ui_characters.py、ui_start.py、ui_components.py；
- 当前已移除人物关系图前端功能，不要重新引入 visualization.py 或 pyvis；
- 当前已有人物名清洗、AI 侦探助手、场景搜证、嫌疑度面板、推理笔记、复盘导出。

不要只相信 README 或之前报告里的结论；以当前代码、当前测试、当前运行结果为准。

---

## 2. 第一步：建立基线（先读、先跑、后改）

在仓库根目录执行并记录结果：

~~~powershell
git status --short
git diff --stat
python -m pytest -q -W error
python -m ruff check .
python -m compileall -q .
~~~

要求：

1. 先读 README.md、docs/optimization-proposal-2026-11.md、docs/launch-screen-design.md；
2. 再读全部 Python 源文件，不要只抽查；
3. 在修改前输出一份模块依赖关系和调用链摘要：
   - app.py 的页面分支与 session_state 生命周期；
   - graph.py 的节点、边、条件路由；
   - nodes.py 每个节点返回的 state key；
   - prompts.py 每个 prompt 的调用节点；
   - validators.py / rules.py 在哪些节点被调用；
   - ui_audio / ui_launch / ui_components 与 app.py 的交互。
4. 先建立“当前失败基线”：测试、ruff、AppTest、启动页面；
5. 不要在基线未确认前开始大改。

---

## 3. 全仓库审计清单

### A. 图与状态机

逐项检查：

- graph.py 中每个 add_node 是否真的有节点函数；
- 每个条件边返回的字符串是否都在映射表里；
- 每个 interrupt 的 payload 类型是否和 app.py / main.py 的分支一致；
- choose_role → role_loading → dm_intro 的过渡是否只执行一次；
- human_turn 的 speak / reveal_clue / accuse / investigate / silence 分支是否互斥；
- human_tiebreak 是否只在玩家属于 tie_candidates 时触发；
- tally / tie_break / final_statement / dm_reveal 是否可能死循环或跳过；
- 检查 GameState 中是否声明了所有节点返回的 key；重点看 messages、thoughts、distributed_clues、revealed_clues、investigated_clues、agent_memory、votes、tie_candidates、vote_round；
- 检查所有 reducer 是否可能被 None / 非 dict / 非 list 污染；
- 检查 checkpointer 清理、线程 ID、会话恢复、旧 checkpoint 兼容。

### B. LLM 与 prompt

逐项检查：

- 所有用户输入是否经过 _untrusted / 转义；
- 所有 prompt 是否避免 Python repr（list / dict 直接进 prompt）；
- 非 DM 节点是否可能泄露 truth / murderer / 他人 secret；
- JSON 解析失败、空响应、非字符串字段、异常结构是否有兜底；
- 重试次数、超时、并发线程池是否存在无界等待；
- 生成剧本后是否做名单校验、关系校验、线索校验；
- 名单外人物名清洗是否有误伤（例如“刘老师”不应被当独立角色）；
- dm_reveal 的最近 40 条消息截断是否仍然保留投票/线索/真相；
- AI 侦探助手是否只读取公开线索 + 玩家笔记，不读取私密线索或 truth；
- 缓存是否按输入正确失效，不能把不同对局结果串用。

### C. UI、交互与性能

逐项检查：

- app.py 中所有 st.button / st.chat_input / st.selectbox / st.slider 的 key 是否唯一；
- 是否有条件创建的 widget 导致 key 残留或重复；
- 角色选择、投票、平票、结算分支是否只渲染当前阶段；
- 主持人 Markdown 与玩家/AI 字面量消息是否走不同渲染路径；
- 滚动恢复脚本是否会在用户主动滚动时反复拉回旧位置；
- st.iframe / st.html / 音频引擎是否在每次 rerun 重复注入、重复创建 AudioContext 或造成 DOM 抖动；
- 是否有重复 json.dumps、重复遍历全部消息、重复构建大 HTML；
- 页面切换是否产生不必要的多次 rerun；
- 大列表 / 长对话是否只渲染必要内容；
- 页面刷新、空 result、旧 checkpoint、线程切换是否安全。

### D. 规则与数据一致性

- 计票、平票、弃权、无人投票、凶手/平民胜负是否一致；
- judge_ending 与 tally_node 的 game_result 是否一致；
- 玩家是凶手时平票是否正确显示平局而不是完美犯罪；
- 玩家自己的角色是否从嫌疑度面板排除；
- 场景搜证的 location 映射、重复线索、None 输入是否安全；
- 名字池、自定义名字白名单、名字清洗是否一致；
- 所有“名单外名字”路径是否清理，包括 truth、personal_script、secret、线索、relations.rel、DM 输出、AI 输出。

### E. 工程与文档

- 检查未使用 import、死代码、重复函数、可变默认参数、裸 except 的异常吞没；
- 检查 app.py / nodes.py 是否有明显的复杂度热点；
- 检查 requirements.txt / requirements.lock 与实际 import 是否一致；
- 检查 .env.example 与 config.py 的配置键是否一致；
- 检查 README 测试数、模块树、截图、运行命令是否过期；
- 检查所有 Markdown 链接是否指向真实文件。

---

## 4. 性能优化要求

不要做“看起来快”的微优化，先测量再改：

1. 统计一局中每个节点的 LLM 调用次数；
2. 统计 prompt 长度、最大 token 风险点；
3. 检查是否有重复调用同一 LLM 的路径；
4. 检查 Streamlit 每次 rerun 是否重复做昂贵工作；
5. 检查长对话 / 长历史是否没有窗口限制；
6. 检查缓存命中条件和缓存污染风险；
7. 优化后给出前后对比：
   - 测试耗时；
   - 关键函数 cProfile 或 AppTest 执行次数；
   - 每局预期 LLM 调用次数；
   - 页面 rerun 次数；
   - 大对象序列化次数。

优先优化：

- 重复 LLM 调用（缓存/复用）；
- 重复构建大 prompt（截断/摘要）；
- 重复渲染全部历史（窗口/懒加载）；
- 重复音频/iframe 注入；
- 重复 JSON 序列化；
- 无界列表/字典增长。

不要为了性能破坏可解释性或测试可读性。

---

## 5. 修复原则

- P0：崩溃、数据错乱、无法进入游戏、严重剧透、核心流程不可用——立即修复；
- P1：常见路径 bug、状态错乱、UI 明显异常、性能明显浪费——本轮修复；
- P2：边界 case、可维护性、非关键 UI——时间允许再修；
- P3：纯风格/文档/长期重构——只记录，不强做。

每次修复都要：

1. 先写/补一个能复现的测试（纯函数或 AppTest）；
2. 修改最小代码；
3. 跑测试；
4. 在报告中记录“原因—修复—验证”。

如果修复会改变玩法或产品行为，先停下来询问用户，不要自行决定。

---

## 6. 验证与验收标准

必须全部满足：

~~~powershell
python -m pytest -q -W error     # 172+ passed，0 failed
python -m ruff check .            # All checks passed
python -m compileall -q .         # 无 SyntaxError
~~~

另外：

- 用 streamlit.testing.v1.AppTest 验证开始页 / 选角 / 游戏 / 结算页面无异常；
- 用 tests/test_graph_flow.py 的 fake LLM 风格验证图流程，不调用真实 API；
- 如果修改 UI，用 AppTest 或 headless browser 截图验证；
- 最终报告里给出实际命令输出，不要写“应该通过”。

---

## 7. 交付格式

请输出一份 Markdown 审计报告，结构如下：

~~~markdown
# DSH 仓库审计与性能优化报告

## 0. 基线
- 仓库状态
- pytest / ruff / compileall 结果
- 当前测试数、模块数、Python 行数

## 1. Bug 总表
| ID | 严重度 | 模块 | 现象 | 复现 | 根因 | 修复 | 回归测试 |
|---|---|---|---|---|---|---|---|

## 2. 性能分析
- 优化前热点
- 优化项
- 前后对比数据

## 3. 实际修改
- 文件清单
- 每个文件改了什么

## 4. 验证结果
- pytest
- ruff
- compileall
- AppTest / 浏览器验证

## 5. 未解决与风险
- 未修复项及原因
- 需要用户决策的产品问题

## 6. 结论
- 是否达到“无 P0/P1、可答辩演示”的标准
~~~

报告之外，必须实际修改仓库文件，不是只给建议。

---

## 8. 最后提醒

- 这是答辩项目，稳定性优先于“炫技”；
- 不要新增玩法功能，不要重写技术栈；
- 不要为了让测试通过而改测试断言；
- 不要把“已知旧 checkpoint 脏数据”当成新 bug，但要加兼容清洗；
- 如果发现同一 bug 在多处出现，优先抽成共享函数 + 回归测试；
- 如果无法确认某个改动是否安全，选择更小、更可回滚的修复，并在报告中标注。

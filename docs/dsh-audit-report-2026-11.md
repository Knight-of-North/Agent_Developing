# DSH 仓库审计与性能优化报告

> 日期：2026-11-12
>
> 仓库：D:\01_Documents\Learning\class\spring_semester_of_freshmen_year\agent_developing\repository\Agent_Developing
>
> 范围：只修 bug、稳定性与性能优化；不新增玩法、不重写技术栈。

## 0. 基线

审计开始时：

~~~text
pytest -q -W error   → 169 passed
ruff check .         → All checks passed
compileall -q .      → 无 SyntaxError
~~~

当前结果：

~~~text
pytest -q -W error   → 172 passed（约 2.2–2.5 秒）
ruff check .         → All checks passed
compileall -q .      → 无 SyntaxError
~~~

新增 3 个回归测试。

## 1. Bug 修复总表

| ID | 严重度 | 模块 | 问题 | 修复 | 回归测试 |
|---|---|---|---|---|---|
| B1 | P1 | nodes.human_turn_node | accuse 分支绕过 F5 追问守卫，连续指控可无限续期追问权、烧穿讨论轮次 | 改用 _apply_followup_guard，与 str/dict 发言分支一致 | test_human_turn_accuse_respects_followup_guard |
| B2 | P1 | nodes.dm_reveal_node | 旧 checkpoint / 脏消息缺少 speaker/content 时，DM 揭晓直接 KeyError | history 用 .get + isinstance 过滤 | test_dm_reveal_handles_malformed_messages |
| B3 | P1 | app.py 聊天渲染 | 历史消息直接 m["speaker"] / m["content"]，坏消息导致整页崩溃 | 非 dict 跳过，字段统一 str 化 | AppTest 手工验证（含坏消息） |
| B4 | P1 | nodes / rules / app / main | 多处 state/dict 为 None 时 .get/.items 抛 AttributeError | 统一 (x or {}) / (x or []) 防御 | 172 个既有 + 新增单测 |
| B5 | P1 | config.py | VOTE_MAX_WORKERS / SCRIPT_MAX_ATTEMPTS 等环境变量为 0 时，线程池或生成循环失效 | max(1, ...) / max(0, ...) 夹紧 | 配置测试与全量测试 |
| B6 | P2 | ui_components._preserve_scroll_position | 恢复完成后仍可能有排队 timeout 再次拉回滚动位置，造成尾部抖动 | restore 入口检查 __agentScrollRestoring | AppTest/浏览器手工验证 |
| B7 | P2 | app.py 侧栏 | 连续两个 st.divider()，视觉重复 | 删除重复项 | AppTest |
| B8 | P2 | app.py 旧 checkpoint 清洗 | 每次 rerun 都重新扫描全部历史消息 | checkpoint_sanitized_<thread> 一次性标记 | AppTest |
| B9 | P2 | app.py 弃局清理 | detective_notes_/suspicion_/result_effect_ 等 per-thread widget key 跨局累积 | 下一次 rerun 顶部统一清理 | AppTest |
| B10 | P2 | validators 名单外人物名检测 | “周一早上”“刘老师”“张家界”被误判为名单外人物 | 增加时间/角色/地名后缀过滤与前后文边界 | test_outsider_name_detector_ignores_time_roles_and_places |
| B11 | P2 | game_state._merge_dict | None 参与字典合并时崩溃 | 合并前归一为空 dict | 全量测试 |
| B12 | P2 | interrupt_handler.validate_vote | suspects 为 None 时 TypeError | vote in (suspects or []) | 全量测试 |

## 2. 性能与内存优化

### 2.1 头像渲染缓存

ui_characters.avatar_html / avatar_data_uri 增加 lru_cache(maxsize=256)。

20,000 次基准：

~~~text
avatar_data_uri  uncached=0.0832s  cached=0.0013s  speedup≈64x
avatar_html      uncached=0.0431s  cached=0.0016s  speedup≈27x
~~~

聊天页每轮 rerun 都会重复渲染所有消息头像，缓存后避免重复 MD5 取色与 base64 SVG 编码。

### 2.2 侦探助手缓存上限

app._cached_assistant_analysis 增加 max_entries=64，避免不同笔记/轮次无限占用 Streamlit 缓存。

### 2.3 旧 checkpoint 清洗只执行一次

app.py 用 checkpoint_sanitized_<thread_id> 标记，旧局第一次加载时清洗一次，后续 rerun 不再重复扫描全部历史消息。

### 2.4 弃局清理 per-thread widget 状态

app.py 在 _reset_game_state 登记 _pending_session_cleanup，在下一次 rerun 顶部删除：
review_json_、assistant_advice_、detective_notes_、suspicion_、result_effect_、history_recorded_、checkpoint_sanitized_。
避免长时间运行/多局重开后 session_state 持续变大。

### 2.5 已有但保留的优化

- dm_reveal 只取最近 40 条消息，truth / 线索 / 投票账本单独传入；
- ai_player_turn 只注入最近 6~12 条历史和个人剧本摘要，不塞全文；
- 自我介绍 / AI 投票 / 平票辩护使用线程池并发；
- 侦探助手按公开信息 + 笔记 + 轮次缓存（TTL 300 秒）。

## 3. 修改文件

- app.py
- config.py
- game_state.py
- interrupt_handler.py
- main.py
- nodes.py
- rules.py
- ui_characters.py
- ui_components.py
- validators.py
- tests/test_logic.py
- README.md / docs/optimization-proposal-2026-11.md（测试数同步为 172）

## 4. 验证结果

~~~text
pytest -q -W error   → 172 passed（约 2.2–2.5 秒）
ruff check .         → All checks passed
compileall -q .      → 无输出（成功）
git diff --check     → 无空白错误
~~~

AppTest 验证：

- 开始页：0 exceptions
- 游戏页：0 exceptions
- 含坏消息（非 dict / 缺 speaker）的旧 checkpoint：0 exceptions，张浩不再出现

## 5. 剩余风险

1. 名单外人物名检测是确定性中文姓名启发式，不是完整 NER；极少数非常规姓名可能漏报，但误伤已通过时间/地名/角色后缀规则收敛。
2. 音频引擎与滚动脚本依赖浏览器 DOM/Web Audio，AppTest 只能验证不抛异常，真实浏览器行为建议答辩前手动点一遍。
3. 真实 DeepSeek API 的延迟/限流仍受网络影响；测试全部使用 fake LLM，不消耗 token。
4. 没有引入数据库/Redis，MemorySaver 仍是单进程内存方案；多人联机不在本次范围。

## 6. 结论

当前仓库达到“无已知 P0/P1、测试全绿、ruff 全绿、可稳定演示”的状态。后续如继续优化，建议优先做真实浏览器端的长局性能采样，而不是继续微调纯 Python 函数。

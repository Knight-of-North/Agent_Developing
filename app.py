"""
AI 剧本杀主持人 · 图形界面版（Streamlit）

运行方式：streamlit run app.py
"""
import json
import uuid
import streamlit as st
from langgraph.types import Command
from graph import build_graph
from nodes import generate_script_stream, _parse_names
from interrupt_handler import get_interrupt, get_interrupt_type, validate_vote
from visualization import build_relations_html


# 缓存图对象：这样 MemorySaver 的状态在 Streamlit 会话内不会丢
@st.cache_resource
def get_graph():
    return build_graph()


@st.dialog("🕸️ 人物关系图（仅公开关系）", width="large")
def _relations_dialog(relations_html):
    """屏幕中央弹出的人物关系图（模态框，右上角自带 X 可关闭）。

    侧边栏空间小，关系图挤在 380px 高的框里看不清；改成点按钮后在大弹窗里看。
    st.dialog 是 Streamlit 的模态框：调用即弹出、点右上角 X 或点弹窗外区域关闭。
    """
    st.components.v1.html(relations_html, height=700)   # L7 修复：与 visualization.py 的 canvas 680px 对齐，避免滚动条


def _run_stream(graph, cmd, config):
    """执行图，返回和 graph.invoke 一致的结果（完整 state + __interrupt__）。

    流式策略（关键：隐藏 AI 的内心戏，只展示公开发言）：
    - DM 开场/揭晓（dm_intro/dm_reveal）：逐 token 显示（台词无秘密，逐字更爽）。
    - AI 玩家发言（ai_player_turn）：LLM 输出的是 {"think": 内心戏, "speak": 发言} 的 JSON，
      若逐 token 显示会暴露 think（内心戏=剧透）。所以 AI 发言的 token 不显示，
      改用 updates 事件——每个 AI 玩家"说完"后，把它的 speak 逐条追加显示，
      形成"一个一个 NPC 说"的节奏感。

    返回的 dict 结构和 graph.invoke 相同：停在 interrupt 时含 "__interrupt__" 键，
    跑完时是完整 state。这样下游逻辑不用改。
    """
    placeholder = st.empty()
    thinking_ph = st.empty()   # AI 发言期间的"思考中"占位（独立于 segments，节点完成后清掉）
    segments = []        # 已完成的片段（DM 台词 + AI 发言，按顺序）
    current_parts = []   # 当前正在生成的 DM 台词 token（list 累积，O(n)，避免 += 的 O(n²)）
    pending = 0          # 距上次渲染累计的字符数，用于节流
    thinking = False     # 是否有 AI 玩家正在"思考"（发言 token 已到但发言未完成）

    def render():
        parts = list(segments)
        if current_parts:
            parts.append("".join(current_parts))
        placeholder.markdown("\n\n".join(parts))

    try:
        for chunk in graph.stream(cmd, config, stream_mode=["messages", "updates"], version="v2"):
            t = chunk.get("type")
            d = chunk.get("data")
            if t == "messages":
                token, meta = d
                node = meta.get("langgraph_node")
                if node == "ai_player_turn":
                    # AI 发言节点：token 含 think（内心戏=剧透）不显示，
                    # 但用首个 token 触发"思考中"占位，消除等待期的冷场感
                    if not thinking:
                        thinking = True
                        thinking_ph.markdown("💭 有嫌疑人正在思考……")
                elif node in ("dm_intro", "dm_reveal") and token.content:
                    current_parts.append(token.content)
                    pending += len(token.content)
                    if pending >= 24:      # 节流：攒够 24 字符才渲染，避免每个 token 全量重写 DOM
                        render()
                        pending = 0
            elif t == "updates":
                if "__interrupt__" in d:
                    # 图暂停在 interrupt：从 checkpoint 拿完整 state，附上 interrupt 信息返回
                    state = graph.get_state(config).values
                    return {**state, "__interrupt__": d["__interrupt__"]}
                # 节点完成：先清掉"思考中"占位，再把新产生的公开消息逐条封存显示
                thinking_ph.empty()
                thinking = False
                for node, update in d.items():
                    if isinstance(update, dict):
                        for m in update.get("messages", []):
                            segments.append(f"**{m['speaker']}**：{m['content']}")
                current_parts = []   # 节点结束，清空当前 token 累积
                pending = 0
                render()
    except Exception as e:
        # 游戏进行中 LLM 断连/超时会在这里抛出。
        # 把错误存到 st.session_state 而不是只调 st.error()——
        # 因为下面 st.stop() 会抛 StopException，新脚本不再渲染旧 st.error，
        # 错误就一闪而过看不到了。session_state 跨 rerun 保留，
        # 顶层（st.set_page_config 之后）会渲染并提供"清除错误"按钮。
        st.session_state.last_error = f"游戏运行出错：{type(e).__name__}：{e}"
        st.error(st.session_state.last_error)   # 旧脚本里也渲染一次（让用户立即看到）
        st.info("可能是网络波动或 DeepSeek 临时限流，请稍后重试。")
        st.stop()   # 停止当前脚本，st.session_state.result 保持旧值，页面回到当前 interrupt 的输入框
    render()   # 收尾：把最后不足 24 字符的 token 也渲染出来
    # 图正常跑完（没有 interrupt）
    return graph.get_state(config).values


st.set_page_config(page_title="AI 剧本杀主持人", page_icon="🎭")
graph = get_graph()

# ---- 跨 rerun 持久化的错误提示 ----
# _run_stream 内部抛异常时把错误存到 st.session_state.last_error，
# 这里在每轮脚本顶部渲染（避免"一闪而过"看不到），用户点按钮清除。
if "last_error" in st.session_state:
    st.error(st.session_state.last_error)
    if st.button("清除错误，继续游戏"):
        del st.session_state.last_error
        st.rerun()

# ---- 初始化会话状态（跨 rerun 保存）----
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())   # 每局一个唯一 ID
if "started" not in st.session_state:
    st.session_state.started = False
if "result" not in st.session_state:
    st.session_state.result = None

config = {"configurable": {"thread_id": st.session_state.thread_id}}

st.title("🎭 AI 剧本杀主持人")

# ---- 未开始：输入主题 + 选背景风格 + 可选自定义背景剧情 ----
if not st.session_state.started:
    st.markdown("输入一个主题、选一个背景风格，AI 会生成剧本、分配角色，你扮演其中一个嫌疑人参与破案。")
    theme = st.text_input("剧本杀主题（可留空，AI 自动发挥）", placeholder="例如：民国豪门恩怨 / 湖南师大规则怪谈")
    background = st.selectbox(
        "背景风格",
        ["自由发挥", "民国豪门", "校园怪谈", "古风仙侠", "现代都市", "科幻末世"],
    )
    # 可选：自定义剧情背景，优先级高于上面的主题 + 风格
    background_story = st.text_area(
        "自定义剧情背景（可选，写下具体背景剧情，AI 会理解后融入创作）",
        placeholder="例如：1935 年上海滩，顾家老爷在寿宴上离奇身亡，三个姨太与管家各怀鬼胎……\n留空则由 AI 根据主题和风格自由发挥",
    )
    # 可选：自定义故事时间 / 地点，作为"素材种子"引导 LLM 理解后融入，而不是照抄
    story_time = st.text_input(
        "故事发生时间（可选）",
        placeholder="例如：1935 年深秋 / 宋代江南 / 未来废土纪元",
    )
    story_location = st.text_input(
        "故事发生地点（可选）",
        placeholder="例如：上海滩租界 / 湖南师大图书馆 / 深山古宅",
    )
    # 嫌疑人名字模式：随机 or 自定义（自定义更有代入感，可用朋友/同学名）
    name_mode = st.radio("嫌疑人名字", ["随机生成", "自定义"], horizontal=True)
    custom_names = []
    if name_mode == "自定义":
        custom_names_text = st.text_input(
            "输入嫌疑人名字（3~6 个，用逗号或空格分隔）",
            placeholder="例如：张三, 李四, 王五, 赵六",
        )
        custom_names = _parse_names(custom_names_text)
        if custom_names and len(custom_names) < 3:
            st.caption("⚠️ 至少 3 个名字，否则会自动退回随机生成")
    # 讨论节奏：快/标准/深入 → 每人发言 2/3/4 轮（报告⑫：轮数可调，避免垃圾时间/意犹未尽）
    pace = st.radio("讨论节奏", ["快（每人2轮）", "标准（每人3轮）", "深入（每人4轮）"], horizontal=True)
    rounds_per_player = {"快（每人2轮）": 2, "标准（每人3轮）": 3, "深入（每人4轮）": 4}[pace]
    if st.button("开始游戏", type="primary"):
        st.session_state.started = True
        theme_val = theme.strip() or "自由发挥"
        story_val = background_story.strip()
        time_val = story_time.strip()
        location_val = story_location.strip()

        # 真流式生成剧本：边生成边显示，而不是盯着 spinner 干等。
        # generate_script_stream 是生成器，逐 token yield；手动 next() 迭代，
        # 从 StopIteration.value 拿到它 return 的最终 script。
        st.markdown("### 🎬 正在生成剧本...")
        gen = generate_script_stream(theme_val, background, story_val, custom_names, time_val, location_val)
        placeholder = st.empty()
        parts = []   # 用 list 累积 token（O(n)），避免 full_text += token 的 O(n²)
        pending = 0  # 距上次刷新累计的字符数，用于节流显示
        script = None
        try:
            while True:
                token = next(gen)
                parts.append(token)
                pending += len(token)
                if pending >= 48:   # 攒够 48 字符才刷新，避免每个 token 全量重写 DOM
                    placeholder.code("".join(parts), language=None)
                    pending = 0
        except StopIteration as e:
            script = e.value   # 生成器 return 的最终 script
        except Exception as e:
            # LLM 网络异常 / 限流 / 超时会在这里抛出，不能让页面直接崩溃
            st.error(f"剧本生成失败：{type(e).__name__}：{e}")
            st.info("可能是网络波动或 DeepSeek 临时限流，请稍后点击「开始游戏」重试。")
            st.session_state.started = False   # 回到输入页，允许重试
            st.stop()
        placeholder.code("".join(parts), language=None)   # 收尾：显示完整原始 JSON
        # 8-20 审查优化：完整剧本 JSON 改折叠展示（默认收起）——新手不用直面一坨 JSON，
        # 排查问题时仍可展开查看原始生成结果（演示时也保留"真流式生成"的过程感）
        # M3 修复：dict 直接给 st.code 会被 str() 转义成 \uXXXX，用 json.dumps(ensure_ascii=False) 还原中文
        with st.expander("📄 查看生成剧本（开发者/排查用）", expanded=False):
            st.code(json.dumps(script, ensure_ascii=False, indent=2), language="json")
        st.success("剧本生成完成！")

        # 把预生成好的 script 传给图（generate_script_node 检测到已有 script 会跳过）
        st.session_state.result = graph.invoke(
            {
                "script": script,
                "theme": theme_val,
                "background_style": background,
                "background_story": story_val,
                "story_time": time_val,
                "story_location": location_val,
                "custom_names": custom_names,
                "rounds_per_player": rounds_per_player,
                "messages": [],
                "thoughts": [],
            },
            config,
        )
        st.rerun()

# ---- 已开始：显示游戏 ----
else:
    result = st.session_state.result

    # ---- 阶段 1：开局选角色（图停在 choose_role interrupt，此时 user_role 还没定）----
    if get_interrupt_type(result) == "choose_role":
        info = get_interrupt(result)
        st.markdown("### 🎭 选择你想扮演的角色")
        st.markdown("剧本已生成，请选一个嫌疑人扮演：")
        cols = st.columns(len(info["suspects"]))
        for i, name in enumerate(info["suspects"]):
            if cols[i].button(name, key=f"role_{i}", use_container_width=True):
                st.session_state.result = _run_stream(graph, Command(resume=name), config)
                st.rerun()
        with st.sidebar:
            st.header("🪪 你的角色卡")
            st.caption("请先在主界面选择你的角色")
        st.stop()   # 选角色阶段不渲染下面的角色卡 / 对话 / 结算

    # ---- 阶段 2：游戏中（user_role 已确定）----
    script = result.get("script", {})
    user_role = result.get("user_role", "你")
    suspects = script.get("suspects", [])
    user_secret = next((s.get("secret", "") for s in suspects if s.get("name") == user_role), "")
    # 信息差：只显示你自己持有的私密线索
    user_clues = result.get("distributed_clues", {}).get(user_role, [])
    # 个人剧本：玩家自己的完整背景故事（开局必读，发言和推理的根基）
    user_script = next((s.get("personal_script", "") for s in suspects if s.get("name") == user_role), "")
    # 结构化角色信息（职业/与死者关系/不在场证明/任务），角色卡要展示、让玩家快速锚定"我是谁我要干嘛"
    user_suspect = next((s for s in suspects if s.get("name") == user_role), {})
    user_profession = user_suspect.get("profession", "")
    user_relation = user_suspect.get("relation_to_victim", "")
    user_alibi = user_suspect.get("alibi", "")
    user_task = user_suspect.get("task", "")

    # 角色卡（侧边栏）
    with st.sidebar:
        st.header("🪪 你的角色卡")
        st.subheader(user_role)
        # 玩家是凶手时给特殊提示（否则会陷入"知道自己是凶手却无事可做"的断裂）
        if result.get("user_is_murderer"):
            st.warning("🩸 你是真凶！你的目标：误导其他人、隐藏证据、别被投出去。")
        # 结构化角色信息：让玩家一眼锚定"我是谁、我和死者什么关系、我要干嘛"
        if user_profession:
            st.caption("💼 职业")
            st.markdown(user_profession)
        if user_relation:
            st.caption("🔗 与死者的关系")
            st.markdown(user_relation)
        if user_alibi:
            st.caption("🕐 你的不在场证明")
            st.markdown(user_alibi)
        if user_task:
            st.caption("🎯 你的任务")
            st.markdown(user_task)
        st.divider()
        st.caption("你的秘密（别主动暴露）")
        st.info(user_secret)
        st.divider()
        st.caption("🔍 你掌握的私密线索（只有你知道）")
        if user_clues:
            for c in user_clues:
                st.markdown(f"· {c}")
        else:
            st.markdown("· （你没有任何私密线索，只能靠盘问别人）")
        st.divider()
        st.caption("嫌疑人名单")
        for s in suspects:
            mark = "（你）" if s.get("name") == user_role else ""
            st.write(f"· {s.get('name')}{mark}")
        st.divider()
        # 人物关系图（仅公开关系，私密关系属信息差不显示）：侧边栏放按钮，点击后屏幕中央弹大图
        st.caption("🕸️ 人物关系图")
        relations_html = build_relations_html(script.get("relations", []), [s.get("name", "?") for s in suspects])
        if relations_html:
            if st.button("🔍 全屏查看人物关系图", use_container_width=True):
                _relations_dialog(relations_html)
        else:
            st.caption("（剧本未生成公开关系）")

    # 个人剧本册子（开局必读）：玩家自己的完整背景故事 + 自己与他人的关系。
    # 信息差铁律：只展示「玩家自己」的剧本；其他角色的 secret / personal_script 绝不在此公开，
    # 否则凶手开局就暴露。关系清单也只提取「涉及玩家自己」的边（自己知情，无论是否公开）。
    if user_script:
        with st.expander("📖 你的个人剧本（开局先读，发言和推理都靠它）", expanded=True):
            st.markdown(user_script)
            my_relations = [
                r for r in script.get("relations", [])
                if r.get("from") == user_role or r.get("to") == user_role
            ]
            if my_relations:
                st.divider()
                st.caption("你与他人的关系")
                for r in my_relations:
                    rel_text = r.get("rel", "")
                    if r.get("from") == user_role:
                        # 玩家是 from：rel 已经是"我..."第一人称，直接展示
                        other = r.get("to", "?")
                        st.markdown(f"· **{other}**：{rel_text}")
                    else:
                        # 玩家是 to：rel 是 from 视角的"我..."，把"我"替换成 from 名字。
                        # 飞哥 8-19 反馈：括号里"X视角"暗示第一人称，改成"X·第三人称"
                        # 明确这是第三人称描述，避免玩家误读为"我视角"。
                        other = r.get("from", "?")
                        # 占位符保住"我们"里的"我"不被误替换（"我们互相看不惯" → 不能变"张三们互相看不惯"）
                        rel_translated = rel_text.replace("我们", "⌈W⌉").replace("我", other).replace("⌈W⌉", "我们")
                        st.markdown(f"· **{other}**（{other}·第三人称）：{rel_translated}")

    # 对话历史（主区域）：直接渲染。
    # 真流式已经在"等待时"实时显示过了，这里无需再打字机回放。
    for m in result.get("messages", []):
        is_user = m["speaker"] == user_role
        role = "user" if is_user else "assistant"
        with st.chat_message(role):
            st.markdown(f"**{m['speaker']}**：{m['content']}")

    # 处理 interrupt（human_turn / human_vote）
    info = get_interrupt(result)
    if info:
        if info["type"] == "human_turn":
            # 玩家回合现在支持「发言」+「公开线索 / 指控 / 调查」三种行动。
            # 行动按钮在 expander 里，默认玩家直接用 chat_input 发言（str），
            # 点行动按钮则 resume 传 dict（human_turn_node 里区分处理）。
            own_clues = info.get("own_clues", [])
            targets = info.get("targets", [])
            can_investigate = info.get("can_investigate", False)

            with st.expander("⚡ 行动选项（公开线索 / 指控 / 调查）", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    if own_clues:
                        clue = st.selectbox("📣 公开哪条线索", own_clues, key="reveal_select")
                        if st.button("公开这条线索", key="reveal_btn"):
                            st.session_state.result = _run_stream(
                                graph, Command(resume={"action": "reveal_clue", "clue": clue}), config
                            )
                            st.rerun()
                    else:
                        st.caption("（你没有可公开的私密线索）")
                with col2:
                    if targets:
                        target = st.selectbox("👉 指控谁", targets, key="accuse_select")
                        if st.button("正式指控 TA", key="accuse_btn"):
                            st.session_state.result = _run_stream(
                                graph, Command(resume={"action": "accuse", "target": target}), config
                            )
                            st.rerun()
                    else:
                        st.caption("（没有可指控的对象）")
                if can_investigate:
                    if st.button("🔍 调查现场（发现隐藏线索）", key="investigate_btn"):
                        st.session_state.result = _run_stream(
                            graph, Command(resume={"action": "investigate"}), config
                        )
                        st.rerun()
                else:
                    st.caption("（现场已经搜遍，没有新发现了）")

            # 发言输入（默认动作，显式 key 与投票输入框隔离，防止值残留串台）
            user_input = st.chat_input(f"轮到你了（{info['speaker']}），输入你的发言...", key="speak_input")
            if user_input:
                st.session_state.result = _run_stream(graph, Command(resume=user_input), config)
                st.rerun()
        elif info["type"] == "human_vote":
            user_input = st.chat_input(f"投票：{', '.join(info['suspects'])}，输入你投谁的名字", key="vote_input")
            if user_input:
                # 校验：必须是合法嫌疑人名字，防止残留的发言文本被当成投票
                if validate_vote(user_input, info["suspects"]):
                    st.session_state.result = _run_stream(graph, Command(resume=user_input), config)
                    st.rerun()
                else:
                    st.warning(f"「{user_input}」不在嫌疑人名单里，请重新输入正确的名字")

    # 游戏结束：显示结算
    else:
        st.success("🎉 游戏结束！")
        with st.expander("📊 投票结果", expanded=True):
            for voter, target in result.get("votes", {}).items():
                st.write(f"· {voter} → {target}")
            st.write(f"**得票最多：{result.get('vote_winner', '无人')}**")
            st.write(f"票数分布：{result.get('vote_counts', {})}")
        if st.button("再来一局", type="primary"):
            # 清理当前 thread 的所有 checkpoint，释放 MemorySaver 内存。
            # @st.cache_resource 让图单例常驻，MemorySaver 会累积每局的每一步快照，
            # 不清理的话长时间运行内存持续增长（checkpointer.delete 是 langgraph>=1.0 的接口）
            old_config = {"configurable": {"thread_id": st.session_state.thread_id}}
            try:
                graph.checkpointer.delete(old_config)
            except Exception:
                pass
            for key in ["started", "result", "thread_id"]:
                st.session_state.pop(key, None)
            st.rerun()

"""
AI 剧本杀主持人 · 图形界面版（Streamlit）

运行方式：streamlit run app.py
"""
import uuid
import streamlit as st
from langgraph.types import Command
from graph import build_graph
from nodes import generate_script_stream, _parse_names


# 缓存图对象：这样 MemorySaver 的状态在 Streamlit 会话内不会丢
@st.cache_resource
def get_graph():
    return build_graph()


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
    segments = []   # 已完成的片段（DM 台词 + AI 发言，按顺序）
    current = ""    # 当前正在生成的 DM 台词 token

    def render():
        parts = list(segments)
        if current:
            parts.append(current)
        placeholder.markdown("\n\n".join(parts))

    for chunk in graph.stream(cmd, config, stream_mode=["messages", "updates"], version="v2"):
        t = chunk.get("type")
        d = chunk.get("data")
        if t == "messages":
            token, meta = d
            node = meta.get("langgraph_node")
            # 只显示 DM 台词的 token；AI 发言的 token（含 think）不显示
            if node in ("dm_intro", "dm_reveal") and token.content:
                current += token.content
                render()
        elif t == "updates":
            if "__interrupt__" in d:
                # 图暂停在 interrupt：从 checkpoint 拿完整 state，附上 interrupt 信息返回
                state = graph.get_state(config).values
                return {**state, "__interrupt__": d["__interrupt__"]}
            # 节点完成：把新产生的公开消息（speak，不含 think）逐条封存显示
            for node, update in d.items():
                if isinstance(update, dict):
                    for m in update.get("messages", []):
                        segments.append(f"**{m['speaker']}**：{m['content']}")
            current = ""   # 节点结束，清空当前 token 累积
            render()
    # 图正常跑完（没有 interrupt）
    return graph.get_state(config).values


st.set_page_config(page_title="AI 剧本杀主持人", page_icon="🎭")
graph = get_graph()

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
        full_text = ""
        script = None
        try:
            while True:
                token = next(gen)
                full_text += token
                placeholder.code(full_text, language=None)   # 实时显示生成中的原始 JSON
        except StopIteration as e:
            script = e.value   # 生成器 return 的最终 script
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
    if "__interrupt__" in result and result["__interrupt__"][0].value["type"] == "choose_role":
        info = result["__interrupt__"][0].value
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

    # 角色卡（侧边栏）
    with st.sidebar:
        st.header("🪪 你的角色卡")
        st.subheader(user_role)
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

    # 对话历史（主区域）：直接渲染。
    # 真流式已经在"等待时"实时显示过了，这里无需再打字机回放。
    for m in result.get("messages", []):
        is_user = m["speaker"] == user_role
        role = "user" if is_user else "assistant"
        with st.chat_message(role):
            st.markdown(f"**{m['speaker']}**：{m['content']}")

    # 处理 interrupt（human_turn / human_vote）
    if "__interrupt__" in result:
        info = result["__interrupt__"][0].value
        if info["type"] == "human_turn":
            # 显式 key：让"发言输入框"和"投票输入框"完全独立，
            # 避免 st.chat_input 值残留把发言文本带进投票环节
            user_input = st.chat_input(f"轮到你了（{info['speaker']}），输入你的发言...", key="speak_input")
            if user_input:
                st.session_state.result = _run_stream(graph, Command(resume=user_input), config)
                st.rerun()
        elif info["type"] == "human_vote":
            user_input = st.chat_input(f"投票：{', '.join(info['suspects'])}，输入你投谁的名字", key="vote_input")
            if user_input:
                # 校验：必须是合法嫌疑人名字，防止残留的发言文本被当成投票
                if user_input in info["suspects"]:
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
            for key in ["started", "result", "thread_id"]:
                st.session_state.pop(key, None)
            st.rerun()

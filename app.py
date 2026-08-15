"""
AI 剧本杀主持人 · 图形界面版（Streamlit）

运行方式：streamlit run app.py
"""
import uuid
import streamlit as st
from langgraph.types import Command
from graph import build_graph


# 缓存图对象：这样 MemorySaver 的状态在 Streamlit 会话内不会丢
@st.cache_resource
def get_graph():
    return build_graph()


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

# ---- 未开始：输入主题 + 选背景风格 ----
if not st.session_state.started:
    st.markdown("输入一个主题、选一个背景风格，AI 会生成剧本、分配角色，你扮演其中一个嫌疑人参与破案。")
    theme = st.text_input("剧本杀主题（可留空，AI 自动发挥）", placeholder="例如：民国豪门恩怨 / 湖南师大规则怪谈")
    background = st.selectbox(
        "背景风格",
        ["自由发挥", "民国豪门", "校园怪谈", "古风仙侠", "现代都市", "科幻末世"],
    )
    if st.button("开始游戏", type="primary"):
        st.session_state.started = True
        with st.spinner("正在生成剧本..."):
            st.session_state.result = graph.invoke(
                {
                    "theme": theme.strip() or "自由发挥",
                    "background_style": background,
                    "messages": [],
                    "thoughts": [],
                },
                config,
            )
        st.rerun()

# ---- 已开始：显示游戏 ----
else:
    result = st.session_state.result

    # 角色卡（侧边栏）
    script = result.get("script", {})
    user_role = result.get("user_role", "你")
    suspects = script.get("suspects", [])
    user_secret = next((s.get("secret", "") for s in suspects if s.get("name") == user_role), "")
    # 信息差：只显示你自己持有的私密线索
    user_clues = result.get("distributed_clues", {}).get(user_role, [])

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

    # 对话历史（主区域）
    for m in result.get("messages", []):
        is_user = m["speaker"] == user_role
        role = "user" if is_user else "assistant"
        with st.chat_message(role):
            st.markdown(f"**{m['speaker']}**：{m['content']}")

    # 处理 interrupt（图暂停，等用户输入）
    if "__interrupt__" in result:
        info = result["__interrupt__"][0].value
        if info["type"] == "human_turn":
            user_input = st.chat_input(f"轮到你了（{info['speaker']}），输入你的发言...")
            if user_input:
                with st.spinner("AI 玩家思考中..."):
                    st.session_state.result = graph.invoke(Command(resume=user_input), config)
                st.rerun()
        elif info["type"] == "human_vote":
            user_input = st.chat_input(f"投票：{', '.join(info['suspects'])}，输入你投谁的名字")
            if user_input:
                with st.spinner("统计票数中..."):
                    st.session_state.result = graph.invoke(Command(resume=user_input), config)
                st.rerun()

    # 游戏结束：显示结算
    else:
        st.success("🎉 游戏结束！")
        with st.expander("📊 投票结果", expanded=True):
            for voter, target in result.get("votes", {}).items():
                st.write(f"· {voter} → {target}")
            st.write(f"**得票最多：{result.get('vote_winner', '无人')}**")
            st.write(f"票数分布：{result.get('vote_counts', {})}")
        with st.expander("🧠 AI 玩家内心戏（调试）"):
            for t in result.get("thoughts", []):
                st.write(f"· {t}")
        if st.button("再来一局", type="primary"):
            for key in ["started", "result", "thread_id"]:
                st.session_state.pop(key, None)
            st.rerun()

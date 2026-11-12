"""开始页配置与渲染函数（从 app.py 拆出）。"""
import html

import streamlit as st

_START_STYLES = {
    "自由发挥": ("自", "AI 混合生成"),
    "民国豪门": ("民", "宅门恩怨"),
    "校园怪谈": ("校", "规则与禁忌"),
    "古风仙侠": ("古", "宗门旧案"),
    "现代都市": ("都", "霓虹迷局"),
    "科幻末世": ("科", "废土档案"),
}

_PACE_OPTIONS = {
    "快 · 10–15 分钟": 2,
    "标准 · 15–22 分钟": 3,
    "深入 · 22–30 分钟": 4,
}

_PACE_HELP = "快 = 每人 2 轮；标准 = 每人 3 轮；深入 = 每人 4 轮。轮数越多，盘问和反转越充分。"

_START_HERO_HTML = """
<div class="start-hero">
  <div class="start-eyebrow">MURDER MYSTERY · 今夜卷宗</div>
  <h1 class="start-title">剧本杀 · 推理之夜</h1>
  <p class="start-sub">一桩命案，六位嫌疑人，真凶就在你们之中。</p>
  <div class="start-chips">
    <span class="start-chip"><b>4–6</b> 名嫌疑人</span>
    <span class="start-chip"><b>15–30</b> 分钟一局</span>
    <span class="start-chip"><b>¥0.1–0.3</b> API 成本</span>
  </div>
</div>
"""

_START_SIDE_HTML = """
<div class="start-side">
  <div class="side-card">
    <div class="side-title">怎么玩 · 四步入局</div>
    <div class="side-step"><div class="side-num">01</div><div><div class="t">设定案件</div><div class="d">输入主题或让 AI 自由发挥</div></div></div>
    <div class="side-step"><div class="side-num">02</div><div><div class="t">挑选身份</div><div class="d">从嫌疑人中选一个扮演</div></div></div>
    <div class="side-step"><div class="side-num">03</div><div><div class="t">审讯调查</div><div class="d">盘问 AI，收集线索，拼出真相</div></div></div>
    <div class="side-step"><div class="side-num">04</div><div><div class="t">投票揭晓</div><div class="d">指认真凶，听 DM 复盘</div></div></div>
    <div class="side-seal">机密</div>
  </div>
  <div class="side-card">
    <div class="side-title">本局档案</div>
    <div class="side-facts">
      <div class="side-fact"><div class="k">模式</div><div class="v">单人 vs AI 群</div></div>
      <div class="side-fact"><div class="k">嫌疑人</div><div class="v">随机 4–6 名</div></div>
      <div class="side-fact"><div class="k">剧本</div><div class="v">即时生成</div></div>
      <div class="side-fact"><div class="k">线索</div><div class="v">私密 + 隐藏</div></div>
    </div>
  </div>
</div>
"""


def render_start_hero() -> None:
    st.markdown(_START_HERO_HTML, unsafe_allow_html=True)


def render_start_side() -> None:
    st.markdown(_START_SIDE_HTML, unsafe_allow_html=True)


def render_recent_history(history: list[dict]) -> None:
    """开始页右侧「最近战绩」卡：只展示当前会话最近 5 局，刷新即清空。"""
    rows = []
    for item in list(history or [])[:5]:
        if not isinstance(item, dict):
            continue
        correct = bool(item.get("correct"))
        badge = "✓" if correct else "✗"
        badge_cls = "win" if correct else "lose"
        case = html.escape(str(item.get("case", "未命名卷宗")))
        role = html.escape(str(item.get("role", "—")))
        vote = html.escape(str(item.get("vote", "弃权")))
        result_text = html.escape(str(item.get("result", "")))
        meta = f"扮演 {role} · 指认 {vote}"
        if result_text:
            meta += f" · {result_text}"
        rows.append(
            f'<div class="history-row">'
            f'<span class="history-badge {badge_cls}">{badge}</span>'
            f'<div class="history-main"><div class="history-case">{case}</div>'
            f'<div class="history-meta">{meta}</div></div>'
            f'<span class="history-time">{html.escape(str(item.get("time", "")))}</span>'
            f"</div>"
        )
    if not rows:
        return
    st.markdown(
        '<div class="side-card history-card">'
        '<div class="side-title">最近战绩 · 本会话</div>'
        f'<div class="history-list">{"".join(rows)}</div>'
        "</div>",
        unsafe_allow_html=True,
    )

"""
AI 剧本杀主持人 · 图形界面版（Streamlit）

运行方式：streamlit run app.py

8-20 沉浸 UI 层（飞哥反馈"界面太简洁无剧本杀紧张感"）：
- G0 暗色档案风主题（全局 CSS：暗色背景 + 红/金点缀 + 衬线字体 + 按钮文案换戏）
- C-1 开场动画（案件标题打字机 + 红章盖下 + 幕布，仅开局播一次）
- C-2 声音系统（ui_audio.py：默认 Minecraft 风格原创钢琴 BGM，可在面板切换悬疑档案；音乐/音效独立开关 + 音量记忆）
- C-3/C-4 阶段过渡幕布 + 事件音效（讨论开始/烛光投票/真相大白，钟声/鼓点/法槌）

2026-11 开始页重构：
- 开始页改为「卷宗封面 + 案件设定卡 + 玩法/本局档案侧栏」，首屏只留核心决策；
- 生成期只显示阶段进度，不渲染 LLM 原始 token；原始剧本 JSON 仅 DEV_MODE 可见。

2026-11 启动封面（ui_launch.py）：
- 首次打开不再直接露出案件设定表单，先播「深夜档案室」启动动画；
- 单光源 + 夜雨 + 尘埃 + 火漆印章，点击「开始游戏」后才进入开始页。

2026-11 角色视觉（ui_characters.py）：
- 选角页使用「首字头像 + 身份关系」嫌疑人卡，侧栏角色卡 / 嫌疑人总览 / 聊天头像统一；
- 头像颜色按姓名哈希稳定生成，不接外部图片服务。

沉浸层设计原则：
1. 纯 UI 层——不碰 graph / nodes / validators，业务逻辑零改动
2. 组件用 st.iframe 内嵌 CSS/JS，颜色全部内联（iframe 是沙箱，不吃全局 CSS）
3. 音乐/动画状态由页面顶层 JS 持有，不经过 Streamlit rerun（防闪断；音频引擎在 ui_audio.py）
4. 阶段幕布 key = thread_id + phase，跨局重玩不串动画
"""
import json
import logging
import re
import time
import uuid

import streamlit as st
from langgraph.types import Command

from config import (
    DEV_MODE,
    INVESTIGATE_LIMIT,
    MAX_BG_STORY_LEN,
    MAX_CHAT_LEN,
    MAX_STORY_LOCATION_LEN,
    MAX_STORY_TIME_LEN,
    MAX_THEME_LEN,
)
from detective_assistant import analyze_case
from game_session import build_config
from graph import build_graph
from interrupt_handler import get_interrupt, get_interrupt_type, is_abstain, is_silence, validate_vote
from logging_config import set_game_id, setup_logging
from names import _parse_names, sanitize_custom_names
from nodes import ScriptGenerationError, generate_script_stream
from ui_audio import init_audio_engine, play_audio_cue, render_audio_panel, set_audio_scene
from ui_characters import (
    avatar_data_uri,
    character_card_html,
    render_character_styles,
    role_header_html,
    suspect_roster_html,
)
from ui_components import (
    _GEN_STAGES,
    _preserve_scroll_position,
    _render_generation_progress,
    _render_intro_animation,
    _render_phase_banner,
    _resolve_case_title,
    render_result_effect,
)
from ui_launch import render_launch_screen
from ui_start import (
    _PACE_HELP,
    _PACE_OPTIONS,
    _START_STYLES,
    render_recent_history,
    render_start_hero,
    render_start_side,
)
from ui_theme import _THEME_CSS
from validators import _sanitize_outsider_names, _sanitize_text_for_script

setup_logging()
logger = logging.getLogger(__name__)


# ==================== UI 资源与组件 ====================
# 主题 CSS / 内嵌 HTML 在 ui_theme.py、ui_assets.py；开始页在 ui_start.py；
# 游戏页渲染组件在 ui_components.py。app.py 只保留页面编排和会话状态。


# 缓存图对象：这样 MemorySaver 的状态在 Streamlit 会话内不会丢
@st.cache_resource
def get_graph():
    return build_graph()


# ==================== 沉浸 UI 渲染函数 ====================

# L5：markdown 控制字符转义。messages 的 content / speaker 来自用户输入和 LLM 输出，
# 直接拼进 st.markdown 会被当成排版指令（标题/链接/图片/表格）注入聊天流。
# Streamlit 会剥 HTML 标签，但 markdown 语法不在其消毒范围——逐字符转义按字面量渲染。
_MD_ESCAPE_RE = re.compile(r"([\\`*_\[\]|~#])")


def _sanitize_md(text: str) -> str:
    """转义 markdown 控制字符 + 尖括号，让外部文本按字面量显示。"""
    if not isinstance(text, str):
        return str(text)
    escaped = _MD_ESCAPE_RE.sub(r"\\\1", text)
    return escaped.replace("<", "&lt;").replace(">", "&gt;")

















def _format_chat_message(speaker: str, content: str) -> str:
    """聊天消息格式化：DM 台词按 Markdown 渲染，其他角色按字面量渲染。

    修复：DM 的揭晓/复盘文本包含 ## 标题、**加粗**、列表等 Markdown，
    之前统一走 _sanitize_md 导致页面上显示成原始 # / ** 符号。
    DM 是受控的系统提示输出，不开放 unsafe HTML；玩家/AI 发言仍逐字转义，
    防止聊天流被 Markdown 排版指令污染。
    """
    speaker_text = _sanitize_md(str(speaker))
    body = "" if content is None else str(content)
    if str(speaker) in {"主持人", "DM"}:
        return f"**{speaker_text}**：\n\n{body}"
    return f"**{speaker_text}**：{_sanitize_md(body)}"


@st.cache_data(show_spinner=False, ttl=300, max_entries=64)
def _cached_assistant_analysis(
    suspects: tuple[str, ...],
    public_clues: tuple[str, ...],
    relations: tuple[str, ...],
    notes: str,
    round_num: int,
) -> str:
    """缓存侦探助手结果：相同公开信息/笔记/轮次不重复烧 token。"""
    return analyze_case(
        suspects=list(suspects),
        public_clues=list(public_clues),
        relations=list(relations),
        notes=notes,
        round_num=round_num,
    )


def _reset_game_state() -> None:
    """清理当前 thread 的 checkpoint 并复位会话状态（R5：弃局/新局共用一份逻辑）。

    @st.cache_resource 让图单例常驻，MemorySaver 会累积每局的每一步快照，
    不清理的话长时间运行内存持续增长。此前只有结算页能清理，中途弃局
    （直接关页/弃玩）的 checkpoint 永久残留——侧边栏"弃局重开"补上入口，
    两处共用本函数，避免清理逻辑写两遍。
    """
    tid = st.session_state.get("thread_id")
    if tid:
        try:
            graph.checkpointer.delete_thread(tid)
        except Exception as e:
            logger.warning("checkpoint 清理失败 thread=%s: %s", tid, e)
    for key in [
        "started", "result", "thread_id", "intro_played", "personal_script_seen", "last_error",
        "role_loading", "pending_role",
    ]:
        st.session_state.pop(key, None)
    # 清理非 widget 的大对象缓存（复盘 JSON / 助手建议），避免多局累积。
    for key in list(st.session_state):
        if key.startswith("review_json_") or key.startswith("assistant_advice_"):
            st.session_state.pop(key, None)
    # widget key（笔记/嫌疑度/结算特效标记）留到下一次 rerun 顶部再删，
    # 避免“widget 已实例化后修改 session_state”的 Streamlit 异常。
    st.session_state["_pending_session_cleanup"] = True


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
                elif node in ("self_intro", "tie_break", "ai_vote"):
                    # M3/M8：这三个节点是多角色串行/并发 LLM 调用，token 流式会把
                    # 不同角色的台词混在一起（ai_vote 更是 think 推理，全为剧透），
                    # 只给"思考中"占位消除冷场，不逐字显示。
                    if not thinking:
                        thinking = True
                        hint = "🕯 各嫌疑人正在写下各自的指认……" if node == "ai_vote" else "💭 有嫌疑人正在发言……"
                        thinking_ph.markdown(hint)
                elif node in ("dm_intro", "dm_reveal", "dm_midpoint", "final_statement") and token.content:
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
                for _node, update in d.items():
                    if isinstance(update, dict):
                        for m in (update.get("messages") or []):
                            if not isinstance(m, dict):
                                continue
                            # DM 台词渲染 Markdown；其他角色仍按字面量渲染。
                            segments.append(_format_chat_message(str(m.get("speaker", "?")), m.get("content", "")))
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


st.set_page_config(page_title="AI 剧本杀主持人", page_icon="🕯", layout="wide")
st.markdown(_THEME_CSS, unsafe_allow_html=True)
render_character_styles()
init_audio_engine()
graph = get_graph()

# ---- 跨 rerun 持久化的错误提示 ----
# _run_stream 内部抛异常时把错误存到 st.session_state.last_error，
# 这里在每轮脚本顶部渲染（避免"一闪而过"看不到），用户点按钮清除。
if "last_error" in st.session_state:
    st.error(st.session_state.last_error)
    if st.button("清除错误，继续游戏"):
        del st.session_state.last_error
        st.rerun()
if "interrupt_notice" in st.session_state:
    # 8-20：剧本生成被中断（刷新页面）后回滚输入页时的一次性提示
    st.warning(st.session_state.pop("interrupt_notice"))

# ---- 初始化会话状态（跨 rerun 保存）----
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())   # 每局一个唯一 ID
if "used_names" not in st.session_state:
    # R8：跨局名字回避集合（浏览器会话粒度）。_pick_suspect_names 会对传入集合
    # 原地 update，抽完名字自动写回；此前流式主路径用 names.py 模块级全局，
    # GameState.used_names 成死字段、跨进程重启后撞名回归。
    st.session_state.used_names = set()
if "started" not in st.session_state:
    st.session_state.started = False
if "result" not in st.session_state:
    st.session_state.result = None
if "intro_played" not in st.session_state:
    st.session_state.intro_played = False   # 开场动画只播一次
if "personal_script_seen" not in st.session_state:
    st.session_state.personal_script_seen = False   # 个人剧本只在开局默认展开一次
if "launched" not in st.session_state:
    # 启动封面只在本会话第一次打开时出现；点「开始游戏」后才进入案件设定页。
    st.session_state.launched = False
if "game_history" not in st.session_state:
    # 本会话最近战绩（最多保留 10 局）：不落库，刷新即清空。
    st.session_state.game_history = []

# 弃局时只做“登记”，真正的清理放到下一次 rerun 的最顶部——
# 那时旧页面 widget 还没创建，删除 detective_notes_* / suspicion_* 等
# widget key 不会被 Streamlit 拒绝，同时避免旧 thread 的 per-key 状态跨局累积。
if st.session_state.pop("_pending_session_cleanup", False):
    cleanup_prefixes = (
        "review_json_", "assistant_advice_", "detective_notes_", "suspicion_",
        "result_effect_", "history_recorded_", "checkpoint_sanitized_",
    )
    for key in list(st.session_state):
        if key.startswith(cleanup_prefixes):
            st.session_state.pop(key, None)

# M1：thread_id → config 全项目唯一构造点（与 GameSession/终端版同一语义）
config = build_config(st.session_state.thread_id)
# M12：对局 ID 注入日志上下文（rerun 时重复 set 无害，contextvars 是本协程私有）
set_game_id(st.session_state.thread_id)

# ---- 启动封面：首次打开项目的沉浸式入口（不直接露出设定表单） ----
if not st.session_state.launched:
    set_audio_scene("menu")
    if render_launch_screen():
        # 封口印章音效放在 rerun 后的开始页播放，避免 st.rerun 丢弃刚注入的脚本
        st.session_state.launch_seal = True
        st.session_state.launched = True
        st.rerun()
    st.stop()

# ---- 未开始：开始页（卷宗封面 + 表单 + 玩法说明卡） ----
if not st.session_state.started:
    set_audio_scene("menu")
    if st.session_state.pop("launch_seal", False):
        play_audio_cue("seal", token=f"{st.session_state.thread_id}:launch")
    with st.container(key="start_page"):
        render_start_hero()

        col_form, col_side = st.columns([1.85, 1], gap="large")
        with col_form:
            # 进度占位放在表单卡上方：点击后用户不用滚动就能看到“正在生成”。
            progress = st.empty()
            with st.container(key="start_form_card"):
                st.markdown('<div class="start-section">① 案件设定</div>', unsafe_allow_html=True)
                theme = st.text_input(
                    "剧本杀主题",
                    placeholder="例如：民国豪门恩怨 / 湖南师大规则怪谈",
                    help="留空则由 AI 自动发挥；最多 50 字。",
                    max_chars=MAX_THEME_LEN,
                )
                st.caption("留空则由 AI 自动发挥。")

                background = st.pills(
                    "背景风格",
                    list(_START_STYLES.keys()),
                    selection_mode="single",
                    default="自由发挥",
                    required=True,
                    help="选择世界观的底色；它也会影响嫌疑人名字池与整体语气。",
                    width="stretch",
                )
                if not background:
                    background = "自由发挥"
                st.caption(f"{_START_STYLES[background][0]} · {_START_STYLES[background][1]}")

                st.markdown('<div class="start-section">② 对局节奏</div>', unsafe_allow_html=True)
                pace = st.segmented_control(
                    "讨论节奏",
                    list(_PACE_OPTIONS.keys()),
                    selection_mode="single",
                    default="标准 · 15–22 分钟",
                    required=True,
                    help=_PACE_HELP,
                    width="stretch",
                )
                if not pace:
                    pace = "标准 · 15–22 分钟"
                rounds_per_player = _PACE_OPTIONS[pace]
                st.caption("快 = 每人 2 轮；标准 = 每人 3 轮；深入 = 每人 4 轮。一局约 15–30 分钟，费用约 ¥0.1–0.3。")

                with st.expander("＋ 进阶设定：自定义剧情 · 时间 · 地点 · 嫌疑人名字", expanded=False):
                    st.caption("自定义剧情背景优先级最高：填了它，主题与风格只作为气质参考。")
                    background_story = st.text_area(
                        "自定义剧情背景",
                        placeholder="例如：1935 年上海滩，顾家老爷在寿宴上离奇身亡，三个姨太与管家各怀鬼胎……",
                        help="可选。写下具体背景剧情，AI 会理解后融入创作；留空则由 AI 根据主题和风格自由发挥。",
                        max_chars=MAX_BG_STORY_LEN,
                        height=120,
                    )
                    time_col, loc_col = st.columns(2)
                    story_time = time_col.text_input(
                        "故事发生时间",
                        placeholder="例如：1935 年深秋 / 未来废土纪元",
                        help="可选。作为时代氛围的素材种子，AI 会理解后融入，不会照抄。",
                        max_chars=MAX_STORY_TIME_LEN,
                    )
                    story_location = loc_col.text_input(
                        "故事发生地点",
                        placeholder="例如：上海滩租界 / 湖南师大图书馆",
                        help="可选。作为空间与人文环境的素材种子。",
                        max_chars=MAX_STORY_LOCATION_LEN,
                    )
                    use_custom_names = st.toggle(
                        "使用自定义嫌疑人名字",
                        value=False,
                        help="默认随机抽取 4–6 名；自定义需填写 3–6 个名字。",
                    )
                    custom_names = []
                    if use_custom_names:
                        custom_names_text = st.text_input(
                            "嫌疑人名字（3–6 个，用逗号或空格分隔）",
                            placeholder="例如：张三, 李四, 王五, 赵六",
                            max_chars=80,
                        )
                        custom_names = _parse_names(custom_names_text)
                        if custom_names and len(custom_names) < 3:
                            st.caption("⚠️ 至少 3 个名字，否则会自动退回随机生成。")
                        elif len(custom_names) > 6:
                            st.caption("⚠️ 最多 6 个名字，超出部分会被忽略。")

                generate_clicked = st.button(
                    "立 案 侦 查",
                    type="primary",
                    use_container_width=True,
                    icon=":material/search:",
                )
                st.markdown(
                    '<div class="start-cta-note">剧本生成约 30–60 秒 · 请勿刷新页面</div>',
                    unsafe_allow_html=True,
                )

            # 生成进度：只展示阶段，不渲染任何 LLM token（防止选中角色前看到凶手/真相）。
            if generate_clicked:
                st.session_state.started = True
                play_audio_cue(
                    "case_open",
                    token=f"{st.session_state.thread_id}:generate:{time.time_ns()}",
                )
                # F2：输入长度限制，防超长输入烧 token + 降低注入攻击面
                theme_val = (theme.strip() or "自由发挥")[:MAX_THEME_LEN]
                story_val = background_story.strip()[:MAX_BG_STORY_LEN]
                time_val = story_time.strip()[:MAX_STORY_TIME_LEN]
                location_val = story_location.strip()[:MAX_STORY_LOCATION_LEN]

                # F2：自定义名字白名单校验（只允许中英数字和间隔号，2-10 字，防名字里塞 prompt）
                custom_names = sanitize_custom_names(custom_names)

                # 真流式生成：保留“边生成边推进度”的体感，但 token 内容绝不进入 DOM。
                # generate_script_stream 是生成器，逐 token yield；从 StopIteration.value 拿最终 script。
                # R8：传跨局已用名字集合（生成器内部原地 update，抽用自动写回）。
                gen = generate_script_stream(
                    theme_val, background, story_val, custom_names,
                    time_val, location_val, used=st.session_state.used_names,
                )
                started_at = time.time()
                stage_idx = 0
                retry_count = 0
                last_render = 0.0
                _render_generation_progress(progress, stage_idx, 0)
                script = None
                try:
                    while True:
                        item = next(gen)
                        now = time.time()
                        elapsed = now - started_at
                        if isinstance(item, tuple) and item[0] == "retry":
                            retry_count += 1
                            stage_idx = len(_GEN_STAGES) - 1
                            _render_generation_progress(progress, stage_idx, elapsed, retry_count)
                            last_render = now
                            continue
                        # 只按耗时推进阶段，token 内容不读取、不渲染。
                        if elapsed >= 28:
                            new_stage = 3
                        elif elapsed >= 16:
                            new_stage = 2
                        elif elapsed >= 7:
                            new_stage = 1
                        else:
                            new_stage = 0
                        if new_stage > stage_idx or now - last_render >= 1.0:
                            stage_idx = max(stage_idx, new_stage)
                            _render_generation_progress(progress, stage_idx, elapsed, retry_count)
                            last_render = now
                except StopIteration as e:
                    script = e.value   # 生成器 return 的最终 script
                except ScriptGenerationError as e:
                    # H1：剧本致命问题（凶手无效/无线索）重试耗尽，提示重开而非带病启动
                    progress.empty()
                    st.error(f"剧本生成质量不达标：{e}")
                    st.info("请调整主题或背景后重新立案侦查。")
                    st.session_state.started = False
                    st.stop()
                except Exception as e:
                    # LLM 网络异常 / 限流 / 超时会在这里抛出，不能让页面直接崩溃
                    progress.empty()
                    st.error(f"剧本生成失败：{type(e).__name__}：{e}")
                    st.info("可能是网络波动或 DeepSeek 临时限流，请稍后点击「立案侦查」重试。")
                    st.session_state.started = False   # 回到输入页，允许重试
                    st.stop()

                _render_generation_progress(
                    progress, len(_GEN_STAGES) - 1, time.time() - started_at,
                    retry_count, complete=True,
                )

                # 把预生成好的 script 传给图（generate_script_node 检测到已有 script 会跳过）
                # R8：used_names 一并写入状态，GameState.used_names 不再是死字段
                try:
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
                            "used_names": sorted(st.session_state.used_names),
                            "messages": [],
                            "thoughts": [],
                        },
                        config,
                    )
                except Exception as e:
                    progress.empty()
                    st.error(f"进入游戏失败：{type(e).__name__}：{e}")
                    st.info("请稍后重试；若持续失败，请检查网络或重新立案侦查。")
                    st.session_state.started = False
                    st.stop()
                st.rerun()

        with col_side:
            render_audio_panel()
            render_start_side()
            render_recent_history(st.session_state.get("game_history", []))

# ---- 已开始：显示游戏 ----
else:
    st.title("🕯 剧本杀 · 推理之夜")
    st.caption("卷宗已开启——你扮演其中一个嫌疑人，找出真凶。")
    result = st.session_state.result
    # 8-20 防御：started=True 但 result 仍为 None（剧本生成中刷新页面/生成被中断残留）。
    # 刷新会终止正在执行的生成循环，result 永远不会被赋值——直接回滚到输入页
    # 并提示重新开始，而不是让 get_interrupt_type(None) 抛 AttributeError。
    if result is None:
        st.session_state.started = False
        st.session_state.interrupt_notice = "剧本生成被中断（可能是页面刷新导致），请重新立案侦查。"
        st.rerun()

    # 兼容旧 checkpoint：清掉历史剧本/消息里已经出现的名单外人物名
    # （例如旧版本 LLM 编出的「张浩」），新生成内容也会在 validators 中清洗。
    sanitize_key = f"checkpoint_sanitized_{st.session_state.get('thread_id', '')}"
    if (isinstance(result, dict) and isinstance(result.get("script"), dict)
            and not st.session_state.get(sanitize_key)):
        old_script = result["script"]
        if old_script.get("suspects"):
            result["script"] = _sanitize_outsider_names(old_script)
            for old_msg in (result.get("messages") or []):
                if isinstance(old_msg, dict):
                    old_msg["content"] = _sanitize_text_for_script(
                        old_msg.get("content", ""), result["script"]
                    )
            st.session_state[sanitize_key] = True

    interrupt_type = get_interrupt_type(result)

    # ---- 阶段 1：开局选角色（图停在 choose_role interrupt，此时 user_role 还没定）----
    if interrupt_type == "choose_role":
        set_audio_scene("menu")
        # 点击角色后先切到“进入案件”页，再跑图；否则生成 DM 开场/AI 自我介绍时，
        # 选角卡片仍留在页面上，主持人台词会出现在角色卡下面（用户反馈图一）。
        if st.session_state.get("role_loading"):
            st.markdown("### 🎭 正在进入案件……")
            st.caption("主持人正在整理卷宗和开场白，请稍候。")
            selected_role = st.session_state.pop("pending_role", "")
            if not selected_role:
                st.session_state.role_loading = False
                st.rerun()
            try:
                with st.spinner("正在进入案件，生成开场与自我介绍……"):
                    st.session_state.result = _run_stream(
                        graph, Command(resume=selected_role), config
                    )
            finally:
                st.session_state.role_loading = False
            st.rerun()
        info = get_interrupt(result)
        # C-1：开场动画只播一次（session_state 标记），播完显示选角引导
        if not st.session_state.intro_played:
            _render_intro_animation(result)
            play_audio_cue(
                "intro",
                token=f"{st.session_state.thread_id}:intro",
                params={"titleLen": len(_resolve_case_title(result))},
            )
            st.session_state.intro_played = True
        st.markdown("### 🎭 选择你想扮演的角色")
        st.markdown("剧本已生成，请选一个嫌疑人扮演：")
        # 选角前先展示公开身份信息（职业/与死者关系），避免“看名字盲选”。
        # 注意：只给公开字段，secret / personal_script 不进入选角 payload。
        profiles = {
            p.get("name"): p for p in info.get("profiles", [])
            if isinstance(p, dict) and p.get("name")
        }
        # L4：6 列按钮在窄屏挤压，每行最多 3 个
        n_sus = len(info["suspects"])
        cols = st.columns(min(n_sus, 3), gap="medium")
        for i, name in enumerate(info["suspects"]):
            p = profiles.get(name, {})
            profession = str(p.get("profession", "")).strip()
            relation = str(p.get("relation_to_victim", "")).strip()
            with cols[i % 3]:
                with st.container(key=f"role_card_{i}"):
                    st.markdown(
                        character_card_html(name, profession, relation),
                        unsafe_allow_html=True,
                    )
                    if st.button(
                        "选 择 此 人",
                        key=f"role_{i}",
                        use_container_width=True,
                        help=relation or None,
                    ):
                        # 先记录角色并 rerun 到“进入案件”页；真正的图执行在下一轮做，
                        # 避免选角卡片与 DM 开场台词同屏。
                        st.session_state.pending_role = name
                        st.session_state.role_loading = True
                        st.rerun()
        with st.sidebar:
            render_audio_panel()
            st.header("🪪 你的角色卡")
            st.caption("请先在主界面选择你的角色")
        st.stop()   # 选角色阶段不渲染下面的角色卡 / 对话 / 结算

    # ---- 阶段 2：游戏中（user_role 已确定）----
    script = result.get("script") or {}
    user_role = result.get("user_role", "你")
    suspects = script.get("suspects") or []
    user_secret = next((s.get("secret") or "" for s in suspects if s.get("name") == user_role), "")
    # 信息差：只显示你自己持有的私密线索
    user_clues = (result.get("distributed_clues") or {}).get(user_role, [])
    # 个人剧本：玩家自己的完整背景故事（开局必读，发言和推理的根基）
    user_script = next((s.get("personal_script", "") for s in suspects if s.get("name") == user_role), "")
    # 结构化角色信息（职业/与死者关系/不在场证明/任务），角色卡要展示、让玩家快速锚定"我是谁我要干嘛"
    user_suspect = next((s for s in suspects if s.get("name") == user_role), {})
    user_profession = user_suspect.get("profession", "")
    user_relation = user_suspect.get("relation_to_victim", "")
    user_alibi = user_suspect.get("alibi", "")
    user_task = user_suspect.get("task", "")

    # 音频：按阶段切换 BGM，并在进入关键节点时播放提示音。
    # 音效 token = 本局 thread_id + 节点，重复 rerun 不会重复响；跨局换 thread_id 自动重播。
    _tid = st.session_state.thread_id
    _phase = result.get("current_phase", "")
    _player_won = (
        (result.get("user_is_murderer") and result.get("game_result") == "凶手胜利")
        or (not result.get("user_is_murderer") and result.get("game_result") == "平民胜利")
    )
    if _phase == "discuss":
        set_audio_scene("discuss")
        play_audio_cue("role_reveal", token=f"{_tid}:role_reveal")
        play_audio_cue("discuss_start", token=f"{_tid}:discuss:start")
    elif _phase == "vote":
        set_audio_scene("vote")
        play_audio_cue("role_reveal", token=f"{_tid}:role_reveal")
        play_audio_cue("vote_start", token=f"{_tid}:vote:{result.get('vote_round', 1)}")
    elif _phase == "reveal":
        set_audio_scene("ending_win" if _player_won else "ending_lose")
        play_audio_cue("reveal", token=f"{_tid}:reveal")
        play_audio_cue("win" if _player_won else "lose", token=f"{_tid}:ending", delay_ms=1800)
    else:
        set_audio_scene("menu")

    # C-4：阶段过渡幕布（纯视觉；音效由 ui_audio 统一播放，key 随 thread_id + phase）
    _render_phase_banner(result)
    # 轮次进度（普通 markdown，每次 rerun 更新；无动画，不干扰幕布）
    round_num = result.get("phase_round", 0)
    total_rounds = len(suspects) * result.get("rounds_per_player", 3)
    if total_rounds > 0 and round_num < total_rounds:
        remaining = total_rounds - round_num
        st.caption(f"🗝 讨论进度 · 已进行 {round_num} 轮 / 共 {total_rounds} 轮 · 剩余 {remaining} 轮")

    # 角色卡（侧边栏）
    with st.sidebar:
        # C-2：声音控制面板（音乐/音效独立开关 + 音量；状态由页面顶层音频引擎持有）
        render_audio_panel()
        st.header("🪪 你的角色卡")
        st.markdown(
            role_header_html(user_role, user_profession, user_relation),
            unsafe_allow_html=True,
        )
        # 嫌疑人总览：提到角色卡顶部最显眼位置（飞哥 8-20 反馈"选完角色后原本 4 人
        # 只剩 2 个"的根因——这条原本在 sidebar 最底部，被长角色卡挤出可见区域 +
        # st.write 在长 sidebar 里循环渲染不稳定）。改用 expander 折叠（默认展开）+
        # 小头像列表，确保全员任何时候都可见。
        if suspects:
            with st.expander(f"👥 嫌疑人总览（共 {len(suspects)} 人）", expanded=True):
                st.markdown(suspect_roster_html(suspects, user_role), unsafe_allow_html=True)
        st.divider()
        # 玩家是凶手时给特殊提示（否则会陷入"知道自己是凶手却无事可做"的断裂）
        if result.get("user_is_murderer"):
            st.warning("🩸 你是真凶！你的目标：误导其他人、隐藏证据、别被投出去。")
        # 结构化角色信息：职业/与死者关系已在角色卡头展示，这里补充行动向信息。
        if user_alibi:
            st.caption("🕐 你的不在场证明")
            st.markdown(_sanitize_md(user_alibi))
        if user_task:
            st.caption("🎯 你的任务")
            st.markdown(_sanitize_md(user_task))
        st.divider()
        st.caption("你的秘密（别主动暴露）")
        st.info(_sanitize_md(user_secret))
        st.divider()
        # 线索板：把「自己的私密线索 / 已公开线索 / 调查所得」分开展示，
        # 避免玩家在聊天记录里来回翻找，解决“线索记不住”的体验问题（P2）。
        investigated = result.get("investigated_clues") or []
        own_private = [c for c in user_clues if c not in investigated]
        public_clues = result.get("revealed_clues") or {}
        with st.expander("🗂 线索板（私密 / 已公开 / 已调查）", expanded=False):
            st.caption("🔒 只有你知道的线索")
            if own_private:
                for c in own_private:
                    st.markdown(f"· {_sanitize_md(c)}")
            else:
                st.markdown("· （没有私密线索，靠盘问别人）")
            st.caption("📣 已公开线索")
            if public_clues:
                for clue, holder in public_clues.items():
                    st.markdown(f"· {_sanitize_md(str(holder))} 公开：{_sanitize_md(str(clue))}")
            else:
                st.markdown("· （还没有人公开线索）")
            st.caption("🔍 你调查到的隐藏线索")
            if investigated:
                for c in investigated:
                    st.markdown(f"· {_sanitize_md(c)}")
            else:
                st.markdown("· （还没有调查到隐藏线索）")
            st.caption(
                f"调查次数：{result.get('investigations_used', 0)} / {INVESTIGATE_LIMIT}"
            )
        # 侦探笔记：参考 AI Murder Mystery 的 EnhancedNotesPanel，
        # 让玩家不用在聊天记录里反复找线索；纯会话状态，不进入图状态。
        with st.expander("📝 我的推理笔记", expanded=False):
            st.text_area(
                "记录你的怀疑、时间线和线索（仅自己可见）",
                key=f"detective_notes_{st.session_state.thread_id}",
                height=110,
                placeholder="例如：李四说 21:00 在图书馆，但监控显示 21:10 才出现……",
            )
        # 嫌疑度面板：参考 LieGraph 的 suspicion matrix，只给玩家自己看，
        # 不把分数传给 AI，避免 AI 迎合玩家或反向剧透。
        with st.expander("🎯 嫌疑度面板", expanded=False):
            st.caption("给你自己打分的推理辅助，不会传给 AI。")
            for s in suspects:
                sname = s.get("name", "")
                # 自己的角色不需要“嫌疑度”，面板只给其他嫌疑人打分。
                if sname and sname != user_role:
                    st.slider(
                        sname,
                        min_value=0,
                        max_value=100,
                        value=50,
                        key=f"suspicion_{st.session_state.thread_id}_{sname}",
                    )
        # AI 侦探助手：只喂公开信息 + 玩家笔记，绝不喂 truth/私密线索（参考 Scottish AI 搭档）。
        with st.expander("🧠 AI 侦探助手", expanded=False):
            st.caption("只基于公开线索和你自己的笔记分析，不会读取私密线索或真相。")
            if st.button("分析当前线索", key="assistant_btn", use_container_width=True):
                public_clue_list = list(dict.fromkeys(
                    list(script.get("public_clues") or [])
                    + list((result.get("revealed_clues") or {}).keys())
                ))
                relation_lines = [
                    f"{r.get('from', '?')} → {r.get('to', '?')}：{r.get('rel', '')}"
                    for r in script.get("relations", [])
                    if r.get("public")
                ]
                notes = st.session_state.get(f"detective_notes_{st.session_state.thread_id}", "")
                with st.spinner("侦探助手正在整理线索..."):
                    advice = _cached_assistant_analysis(
                        tuple(s.get("name", "") for s in suspects if s.get("name")),
                        tuple(public_clue_list),
                        tuple(relation_lines),
                        notes,
                        int(result.get("phase_round", 0)),
                    )
                st.session_state[f"assistant_advice_{st.session_state.thread_id}"] = advice
            advice = st.session_state.get(f"assistant_advice_{st.session_state.thread_id}", "")
            if advice:
                st.markdown(_sanitize_md(advice))
        # 注：原"嫌疑人名单"区域已移除（已合并到顶部 expander，避免重复 + 被裁）
        st.divider()
        # R5：中途弃局的清理入口——此前只有结算页能 delete_thread，直接关页/弃玩
        # 会让 checkpoint 永久残留在 @st.cache_resource 的图单例里（内存累积）。
        if st.button("🚪 弃局重开（清理本局存档）", use_container_width=True):
            _reset_game_state()
            st.rerun()

    # 个人剧本册子（开局必读）：玩家自己的完整背景故事 + 自己与他人的关系。
    # 信息差铁律：只展示「玩家自己」的剧本；其他角色的 secret / personal_script 绝不在此公开，
    # 否则凶手开局就暴露。关系清单也只提取「涉及玩家自己」的边（自己知情，无论是否公开）。
    if user_script:
        # 首屏默认展开，方便玩家开局读；读过一次后折叠，避免长剧本在每次 rerun 时
        # 把聊天记录继续往下顶（手机/小屏尤其明显）。玩家仍可随时手动展开。
        with st.expander(
            "📖 你的个人剧本（开局先读，发言和推理都靠它）",
            expanded=not st.session_state.get("personal_script_seen", False),
        ):
            st.session_state.personal_script_seen = True
            st.markdown(_sanitize_md(user_script))
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
                        st.markdown(f"· **{_sanitize_md(other)}**：{_sanitize_md(rel_text)}")
                    else:
                        # 玩家是 to：rel 是 from 视角的"我..."，把"我"替换成 from 名字。
                        # 飞哥 8-19 反馈：括号里"X视角"暗示第一人称，改成"X·第三人称"
                        # 明确这是第三人称描述，避免玩家误读为"我视角"。
                        other = r.get("from", "?")
                        # 占位符保住"我们"里的"我"不被误替换（"我们互相看不惯" → 不能变"张三们互相看不惯"）
                        rel_translated = rel_text.replace("我们", "⌈W⌉").replace("我", other).replace("⌈W⌉", "我们")
                        st.markdown(f"· **{_sanitize_md(other)}**（{_sanitize_md(other)}·第三人称）：{_sanitize_md(rel_translated)}")

    # 对话历史（主区域）：直接渲染。
    # 真流式已经在"等待时"实时显示过了，这里无需再打字机回放。
    # L5：speaker/content 均按字面量渲染，防用户输入/AI 输出注入 markdown 排版。
    for m in (result.get("messages") or []):
        if not isinstance(m, dict):
            continue  # 防御旧 checkpoint / 手工构造 state 里的脏消息
        speaker = str(m.get("speaker") or "主持人")
        content = m.get("content")
        content = content if isinstance(content, str) else str(content or "")
        is_user = speaker == user_role
        role = "user" if is_user else "assistant"
        with st.chat_message(role, avatar=avatar_data_uri(speaker)):
            st.markdown(_format_chat_message(speaker, content))

    # H14：DEV_MODE 下显示 AI 内心戏和调试状态（默认关闭，防剧透）
    if DEV_MODE:
        thoughts = result.get("thoughts", [])
        if thoughts:
            with st.expander("🧠 AI 内心戏（DEV_MODE）", expanded=False):
                for t in thoughts:
                    st.caption(t)
        with st.expander("🔧 状态调试（DEV_MODE）", expanded=False):
            st.json({
                "phase_round": result.get("phase_round"),
                "pending_reply_to": result.get("pending_reply_to"),
                "follow_up": result.get("follow_up"),
                "consecutive_followups": result.get("consecutive_followups"),
                "midpoint_done": result.get("midpoint_done"),
                "investigations_used": result.get("investigations_used"),
                "revealed_clues": list((result.get("revealed_clues") or {}).keys()),
            })
        # 原始剧本只在 DEV_MODE 下可见：默认玩家视角绝不能在选角前看到 murderer / truth。
        with st.expander("📄 原始剧本 JSON（DEV_MODE · 含剧透）", expanded=False):
            st.code(json.dumps((result.get("script") or {}), ensure_ascii=False, indent=2), language="json")

    # 处理 interrupt（human_turn / human_vote）
    info = get_interrupt(result)
    if info:
        if info["type"] == "human_turn":
            # 轮到玩家发言：提示音按「消息数 + 发言人」去重，每次真正轮到你只响一次
            play_audio_cue(
                "turn",
                token=f"{_tid}:turn:{len(result.get('messages', []))}:{info.get('speaker', '')}",
            )
            # 玩家回合现在支持「发言」+「公开线索 / 指控 / 调查」三种行动。
            # 行动按钮在 expander 里，默认玩家直接用 chat_input 发言（str），
            # 点行动按钮则 resume 传 dict（human_turn_node 里区分处理）。
            own_clues = info.get("own_clues", [])
            targets = info.get("targets", [])
            can_investigate = info.get("can_investigate", False)

            with st.expander("⚡ 行动选项（公开证词 / 指控 / 调查）", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    if own_clues:
                        clue = st.selectbox("📜 公开哪条线索", own_clues, key="reveal_select")
                        if st.button("公开这份证词", key="reveal_btn"):
                            st.session_state.result = _run_stream(
                                graph, Command(resume={"action": "reveal_clue", "clue": clue}), config
                            )
                            st.rerun()
                    else:
                        st.caption("（你没有可公开的私密线索）")
                with col2:
                    if targets:
                        target = st.selectbox("⛓ 指控谁", targets, key="accuse_select")
                        if st.button("⚡ 厉声指控", key="accuse_btn"):
                            st.session_state.result = _run_stream(
                                graph, Command(resume={"action": "accuse", "target": target}), config
                            )
                            st.rerun()
                    else:
                        st.caption("（没有可指控的对象）")
                if can_investigate:
                    remaining = info.get("investigations_remaining", INVESTIGATE_LIMIT)
                    location_rows = info.get("locations") or []
                    if not isinstance(location_rows, list):
                        location_rows = []
                    available_locations = [
                        row["name"] for row in location_rows
                        if isinstance(row, dict) and row.get("remaining", 0) > 0
                    ]
                    if available_locations:
                        selected_location = st.selectbox(
                            "选择搜证地点",
                            available_locations,
                            key="investigate_location",
                        )
                        selected_row = next(
                            row for row in location_rows
                            if isinstance(row, dict) and row.get("name") == selected_location
                        )
                        st.caption(
                            f"该地点还有 {selected_row.get('remaining', 0)} 条线索 · "
                            f"本局调查次数剩余 {remaining} 次"
                        )
                        if st.button(f"🔍 搜索{selected_location}", key="investigate_btn"):
                            st.session_state.result = _run_stream(
                                graph,
                                Command(resume={"action": "investigate", "location": selected_location}),
                                config,
                            )
                            st.rerun()
                    elif not location_rows:
                        # 兼容旧 checkpoint / 外部直接构造的 interrupt payload：
                        # 没有 locations 字段时退回原来的随机调查，不能让玩家丢动作。
                        if st.button(f"🌙 暗中调查现场（剩余 {remaining} 次）", key="investigate_btn"):
                            st.session_state.result = _run_stream(
                                graph, Command(resume={"action": "investigate"}), config
                            )
                            st.rerun()
                    else:
                        st.caption("（现场已经搜遍，没有新发现了）")
                else:
                    st.caption("（现场已经搜遍，没有新发现了）")
                # M9：发言不是强制的——保持沉默也是剧本杀的合法策略
                if st.button("🤐 保持沉默（这一轮不发言）", key="silence_btn"):
                    st.session_state.result = _run_stream(
                        graph, Command(resume={"action": "silence"}), config
                    )
                    st.rerun()

            # 发言输入（默认动作，显式 key 与投票输入框隔离，防止值残留串台）
            # M9：输入「沉默」保留字 = 点沉默按钮（终端/Web 协议一致）
            user_input = st.chat_input(f"轮到你了（{info['speaker']}），说出你的证词...", key="speak_input")
            if user_input:
                resume = "沉默" if is_silence(user_input) else user_input[:MAX_CHAT_LEN]
                st.session_state.result = _run_stream(graph, Command(resume=resume), config)
                st.rerun()
        elif info["type"] == "human_tiebreak":
            # 平票加时：AI 候选人已做完最后辩护，轮到玩家自辩（F-05）。
            play_audio_cue("tiebreak", token=f"{_tid}:tiebreak")
            st.markdown("### ⚖️ 平票加时 · 你的最后陈述")
            st.caption(f"你与 {'、'.join(info.get('suspects', []))} 进入平票加时，请做最后陈述后进入重投。")
            if st.button("🤐 保持沉默（直接进入重投）", key="tiebreak_silence"):
                st.session_state.result = _run_stream(graph, Command(resume="沉默"), config)
                st.rerun()
            user_input = st.chat_input("说出你的最后陈述（也可输入「沉默」）...", key="tiebreak_input")
            if user_input:
                st.session_state.result = _run_stream(
                    graph, Command(resume=user_input[:MAX_CHAT_LEN]), config
                )
                st.rerun()
        elif info["type"] == "human_vote":
            # M9：投票不是强制的——弃权通道显式暴露（规则层一直有 None 通道）
            if st.button("🕊 弃权（不指认任何人）", key="abstain_btn"):
                st.session_state.result = _run_stream(graph, Command(resume="弃权"), config)
                st.rerun()
            user_input = st.chat_input(f"🕯 烛光投票：{', '.join(info['suspects'])}，你指认谁？（也可点上方弃权）", key="vote_input")
            if user_input:
                # M9：弃权保留字直通（否则会掉进"非法输入"循环纠错）
                if is_abstain(user_input):
                    st.session_state.result = _run_stream(graph, Command(resume="弃权"), config)
                    st.rerun()
                # 校验：必须是合法嫌疑人名字，防止残留的发言文本被当成投票
                elif validate_vote(user_input, info["suspects"]):
                    st.session_state.result = _run_stream(graph, Command(resume=user_input), config)
                    st.rerun()
                else:
                    st.warning(f"「{user_input}」不在嫌疑人名单里，请重新输入正确的名字")

    # 游戏结束：显示结算
    else:
        st.success("🎉 真相大白！")
        # 结算动画只播一次：胜利/失败/平局三种视觉反馈。
        effect_key = f"result_effect_{st.session_state.get('thread_id', 'default')}"
        if not st.session_state.get(effect_key):
            render_result_effect(result)
            st.session_state[effect_key] = True
        # 本局复盘卡：参考 AI Murder Mystery 的“真凶/动机揭晓”和 Wolfcha 的
        # post-game review，给玩家一个可截图的结局摘要，而不是只看票数。
        end_script = (result.get("script") or {})
        true_murderer = end_script.get("murderer", "（未知）")
        your_vote = (result.get("votes") or {}).get(user_role)
        vote_text = your_vote or "（弃权）"
        verdict = "✅ 指认正确" if your_vote == true_murderer else "❌ 指认错误"
        # 本会话战绩：同一 thread_id 只记一次（rerun / 导出 / 点按钮都不重复追加）。
        history_key = f"history_recorded_{st.session_state.thread_id}"
        if not st.session_state.get(history_key):
            st.session_state.game_history.append({
                "case": _resolve_case_title(result),
                "role": user_role,
                "vote": vote_text,
                "correct": your_vote == true_murderer,
                "result": result.get("game_result", ""),
                "time": time.strftime("%m-%d"),
            })
            st.session_state.game_history = st.session_state.game_history[-10:]
            st.session_state[history_key] = True
        st.markdown("### 🧾 本局复盘")
        m1, m2, m3 = st.columns(3)
        m1.metric("真凶", true_murderer)
        m2.metric("你的指认", vote_text)
        m3.metric("本局结果", result.get("game_result", "—"))
        st.caption(f"{verdict} · 对话 {len(result.get('messages') or [])} 条 · 公开线索 {len(result.get('revealed_clues') or {})} 条 · 调查 {result.get('investigations_used', 0)} 次")
        truth_text = end_script.get("truth", "")
        if truth_text:
            st.info(f"案件真相：{_sanitize_md(truth_text)}")
        # 复盘导出：方便答辩演示后回看，也方便玩家保留本局记录。
        review_cache_key = f"review_json_{st.session_state.get('thread_id', 'default')}"
        review_json = st.session_state.get(review_cache_key)
        if not review_json:
            review_payload = {
                "case": _resolve_case_title(result),
                "user_role": user_role,
                "murderer": true_murderer,
                "truth": truth_text,
                "game_result": result.get("game_result", ""),
                "vote_outcome": result.get("vote_outcome", ""),
                "votes": (result.get("votes") or {}),
                "vote_counts": result.get("vote_counts", {}),
                "revealed_clues": (result.get("revealed_clues") or {}),
                "investigated_clues": (result.get("investigated_clues") or []),
                "messages": (result.get("messages") or []),
            }
            review_json = json.dumps(review_payload, ensure_ascii=False, indent=2)
            st.session_state[review_cache_key] = review_json
        st.download_button(
            "⬇️ 导出本局复盘 JSON",
            data=review_json,
            file_name=f"剧本杀复盘-{st.session_state.get('thread_id', '本局')}.json",
            mime="application/json",
        )
        with st.expander("📊 投票结果", expanded=True):
            # M9 配套：弃权票（None）显示为「（弃权）」而非 Python 的 None
            for voter, target in (result.get("votes") or {}).items():
                st.write(f"· {voter} → {target or '（弃权）'}")
            # 2026-11：vote_winner 只存真实人名，平票/无人投票由 vote_outcome 表达
            outcome = result.get("vote_outcome", "")
            winner = result.get("vote_winner", "")
            if outcome == "tie":
                st.write(f"**得票最多：平票（{'、'.join(result.get('tie_candidates') or [])}）**")
            elif outcome == "no_votes":
                st.write("**得票最多：无人投票（全员弃权）**")
            else:
                st.write(f"**得票最多：{winner or '无人'}**")
            if result.get("tie_candidates"):
                st.write(f"平票并列：{'、'.join(result['tie_candidates'])}")
            st.write(f"票数分布：{result.get('vote_counts', {})}")
            game_result = result.get("game_result", "")
            if game_result:
                st.write(f"全局胜负：**{game_result}**")
        if st.button("🕯 翻开新卷宗", type="primary"):
            # R5：清理逻辑抽成 _reset_game_state（与侧边栏"弃局重开"共用一份）。
            # C2 修复历史：langgraph 1.2.x 的接口是 delete_thread(thread_id: str)，
            # 旧代码调 delete(config) 必抛 AttributeError 被 except 吞掉。
            _reset_game_state()
            st.rerun()

    # 关键：投票结束/长内容生成后的 st.rerun 会把页面滚回顶部。
    # 在游戏页末尾恢复玩家刚才的 scrollTop（按 thread_id 隔离），
    # 不改变 start / role 阶段的滚动行为。
    _preserve_scroll_position()

"""
AI 剧本杀主持人 · 图形界面版（Streamlit）

运行方式：streamlit run app.py

8-20 沉浸 UI 层（飞哥反馈"界面太简洁无剧本杀紧张感"）：
- G0 暗色档案风主题（全局 CSS：暗色背景 + 红/金点缀 + 衬线字体 + 按钮文案换戏）
- C-1 开场动画（案件标题打字机 + 红章盖下 + 幕布，仅开局播一次）
- C-2 氛围音乐控制条（Web Audio 纯代码合成雨声+悬疑 drone，▶/⏸ 暂停 + 音量滑杆）
- C-3/C-4 阶段过渡幕布 + 事件音效（讨论开始/烛光投票/真相大白，钟响/鼓点）

沉浸层设计原则：
1. 纯 UI 层——不碰 graph / nodes / validators，业务逻辑零改动
2. 组件用 st.components.v1.html 内嵌 CSS/JS，颜色全部内联（iframe 是沙箱，不吃全局 CSS）
3. 音乐/动画状态由 iframe 内部 JS 持有，不经过 Streamlit rerun（防闪断）
4. 阶段幕布 key = thread_id + phase，跨局重玩不串动画
"""
import json
import uuid
import streamlit as st
from langgraph.types import Command
from graph import build_graph
from nodes import generate_script_stream, _parse_names
from interrupt_handler import get_interrupt, get_interrupt_type, validate_vote
from visualization import build_relations_html


# ==================== G0：暗色档案风主题（全局 CSS） ====================
# Streamlit 1.61 的选择器：主容器 / 侧边栏 / 按钮 / 输入 / chat 消息 / expander / alert
_THEME_CSS = """
<style>
:root {
  --murder-bg: #141110;
  --murder-card: #1d1916;
  --murder-input: #221c18;
  --murder-text: #E8DCC8;
  --murder-muted: #D4C4A0;   /* 8-20 飞哥反馈"开始页小字看不清"，从 #a2947a 提亮 */
  --murder-gold: #C9A227;
  --murder-red: #C0392B;
  --murder-border: #6b4f2a;
  --murder-serif: 'STKaiti', 'KaiTi', '楷体', 'STSong', serif;   /* 标题/按钮保留衬线感（沉浸感） */
  --murder-sans: 'Microsoft YaHei', 'PingFang SC', 'Hiragino Sans GB', 'Noto Sans CJK SC', 'Source Han Sans SC', sans-serif;   /* 8-20 新增：正文/输入/caption 用无衬线，楷体小字号笔画细太糊 */
}
html, body, [data-testid="stAppViewContainer"] {
  background: var(--murder-bg) !important;
}
[data-testid="stHeader"] {
  background: transparent !important;
}
[data-testid="stSidebar"] {
  background: var(--murder-card) !important;
  border-right: 1px solid var(--murder-border) !important;
}
.block-container {
  padding-top: 2rem;
}
[data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] {
  color: var(--murder-text);
  font-family: var(--murder-sans);
}
h1, h2, h3, h4 {
  color: var(--murder-text) !important;
  font-family: var(--murder-serif);
  letter-spacing: 2px;
}
div.stButton > button {
  background: var(--murder-input);
  color: var(--murder-gold);
  border: 1px solid var(--murder-border);
  border-radius: 8px;
  font-family: var(--murder-serif);
  transition: border-color 0.2s ease;
}
div.stButton > button:hover {
  border-color: var(--murder-gold);
  color: #E8DCC8;
}
div.stButton > button[kind="primary"] {
  background: var(--murder-red);
  border-color: #8a2020;
  color: #F5E6D8;
  font-weight: 500;
}
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea {
  background: var(--murder-input) !important;
  color: var(--murder-text) !important;
  border: 1px solid var(--murder-border) !important;
  border-radius: 8px !important;
  font-family: var(--murder-sans);
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder {
  color: #9a8e75 !important;   /* 8-20 提亮：#6f6550 → #9a8e75 */
  font-family: var(--murder-sans);
}
[data-testid="stSelectbox"] > div > div,
[data-testid="stRadio"] label {
  color: var(--murder-text);
}
[data-testid="stSelectbox"] > div {
  background: var(--murder-input);
  border: 1px solid var(--murder-border);
  border-radius: 8px;
}
[data-testid="stChatMessage"] {
  background: var(--murder-card);
  border: 1px solid var(--murder-border);
  border-radius: 10px;
  padding: 10px 14px;
}
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] {
  color: var(--murder-text);
}
[data-testid="stChatInput"] {
  background: var(--murder-input) !important;
  border: 1px solid var(--murder-border) !important;
  border-radius: 10px !important;
  color: var(--murder-text) !important;
}
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] > div > div,
[data-testid="stChatInputContainer"],
[data-testid="stChatInputTextArea"],
[data-testid="stChatInput"] textarea,
[data-testid="stChatInput"] input {
  background: var(--murder-input) !important;
  background-color: var(--murder-input) !important;
  color: var(--murder-text) !important;
  border-color: var(--murder-border) !important;
  box-shadow: none !important;
  font-family: var(--murder-sans) !important;   /* 8-20：聊天输入框改无衬线，楷体小字糊 */
}
[data-testid="stChatInput"] textarea::placeholder,
[data-testid="stChatInputTextArea"]::placeholder {
  color: #6f6550 !important;
}
/* chat_input 发送按钮：金色高亮，保持圆形 */
[data-testid="stChatInput"] button,
[data-testid="stChatInputSubmitButton"] {
  background: var(--murder-gold) !important;
  color: #141110 !important;
  border: none !important;
}
[data-testid="stChatInput"] button:hover,
[data-testid="stChatInputSubmitButton"]:hover {
  background: #E8DCC8 !important;
  color: #141110 !important;
}
[data-testid="stExpander"] {
  background: var(--murder-card);
  border: 1px solid var(--murder-border);
  border-radius: 10px;
}
.streamlit-expanderHeader {
  color: var(--murder-text) !important;
  font-family: var(--murder-serif);
}
[data-testid="stCaptionContainer"] {
  color: var(--murder-muted);
  font-family: var(--murder-sans);   /* 8-20：caption 改无衬线，避免楷体小字号笔画糊 */
  font-size: 14px;   /* 强制不小于 14px，避免太小的字 */
  font-weight: 500;   /* 略加粗，提升可读性 */
}
[data-testid="stInfo"], [data-testid="stSuccess"], [data-testid="stWarning"], [data-testid="stError"] {
  border-radius: 8px;
}
[data-testid="stInfo"] { background: #221c18; color: var(--murder-text); border: 1px solid var(--murder-border); }
[data-testid="stWarning"] { background: #2a0f0c; color: #F09595; border: 1px solid #8a2020; }
[data-testid="stSuccess"] { background: #1a2416; color: #C0DD97; border: 1px solid #3B6D11; }
hr {
  border-color: var(--murder-border) !important;
}
/* 底部 toolbar / footer 暗色化（飞哥 8-20 反馈"输入框下方大片白色与暗色主题不符"）
   Streamlit 把 chat_input 放在底部 stBottom 容器里，这个容器默认是白底，
   之前只覆盖了 stChatInput 自身，父容器 + footer 漏掉了——补刀。 */
[data-testid="stBottom"],
[data-testid="stBottomContainer"],
footer,
[data-testid="stDecoration"] {
  background: var(--murder-bg) !important;
  background-color: var(--murder-bg) !important;
  color: var(--murder-text) !important;
}
[data-testid="stBottom"] > * {
  background: var(--murder-bg) !important;
  background-color: var(--murder-bg) !important;
}
</style>
"""


# ==================== C-2：氛围音乐控制条（Web Audio 合成雨声 + 悬疑 drone） ====================
# 常量 HTML：不依赖任何 Python state。key 固定，Streamlit rerun 时同 key 同 props
# 的组件复用 iframe → 音乐不闪断。播放/音量状态由 iframe 内部 JS 持有。
_AMBIENT_AUDIO_HTML = """
<div style="font-family:'KaiTi','楷体','SimSun',serif; background:#141110; border:1px solid #6b4f2a; border-radius:10px; padding:10px 12px; color:#E8DCC8;">
  <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:8px;">
    <span style="font-size:12px; color:#C9A227; letter-spacing:2px;">⛈ 雷雨氛围</span>
    <button id="ambient-btn" onclick="toggleAmbient()" style="background:#221c18; color:#C9A227; border:1px solid #6b4f2a; border-radius:6px; padding:4px 10px; font-size:12px; cursor:pointer; font-family:inherit;">▶ 开启雷雨</button>
  </div>
  <div style="display:flex; align-items:center; gap:8px;">
    <span style="font-size:11px; color:#8a7a5f;">小</span>
    <input id="ambient-vol" type="range" min="0" max="1" step="0.05" value="0.4" oninput="setVolume(this.value)" style="flex:1; accent-color:#C9A227;">
    <span style="font-size:11px; color:#8a7a5f;">大</span>
  </div>
</div>
<script>
var ctx = null, masterGain = null, playing = false, volume = 0.4, thunderTimer = null;
function makeNoise(size) {
  var buf = ctx.createBuffer(1, size, ctx.sampleRate);
  var d = buf.getChannelData(0);
  for (var i = 0; i < size; i++) d[i] = Math.random() * 2 - 1;
  return buf;
}
function initAudio() {
  if (ctx) return;
  ctx = new (window.AudioContext || window.webkitAudioContext)();
  masterGain = ctx.createGain(); masterGain.gain.value = 0; masterGain.connect(ctx.destination);
  var size = 2 * ctx.sampleRate;
  // 雨势主体：白噪声 + 带通 1000Hz（中频沙沙声，主体）
  var bp = ctx.createBiquadFilter(); bp.type = 'bandpass'; bp.frequency.value = 1000; bp.Q.value = 0.5;
  var bodyGain = ctx.createGain(); bodyGain.gain.value = 0.30;
  var noiseBody = ctx.createBufferSource(); noiseBody.buffer = makeNoise(size); noiseBody.loop = true;
  noiseBody.connect(bp); bp.connect(bodyGain); bodyGain.connect(masterGain); noiseBody.start();
  // 雨滴细节：白噪声 + 高通 2200Hz（高频噼啪感，像雨点打窗）
  var hp = ctx.createBiquadFilter(); hp.type = 'highpass'; hp.frequency.value = 2200;
  var detailGain = ctx.createGain(); detailGain.gain.value = 0.16;
  var noiseDetail = ctx.createBufferSource(); noiseDetail.buffer = makeNoise(size); noiseDetail.loop = true;
  noiseDetail.connect(hp); hp.connect(detailGain); detailGain.connect(masterGain); noiseDetail.start();
  // 远景 drone：低频 55Hz 失谐（阴沉持续底色）
  var droneGain = ctx.createGain(); droneGain.gain.value = 0.10;
  var o1 = ctx.createOscillator(); o1.type = 'sine'; o1.frequency.value = 55;
  var o2 = ctx.createOscillator(); o2.type = 'sine'; o2.frequency.value = 55.7;
  o1.connect(droneGain); o2.connect(droneGain); droneGain.connect(masterGain);
  o1.start(); o2.start();
}
function playThunder() {
  if (!ctx) return;
  // 雷声：60Hz 振荡 + 0.05s 攻击 + 1.5s 衰减，像远处闷雷
  var g = ctx.createGain();
  g.gain.setValueAtTime(0, ctx.currentTime);
  g.gain.linearRampToValueAtTime(0.35 * volume, ctx.currentTime + 0.05);
  g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 1.5);
  g.connect(ctx.destination);
  var o = ctx.createOscillator(); o.type = 'sine'; o.frequency.value = 60;
  // LFO 给低频加微抖动，让雷声"颤抖"更真实
  var lfo = ctx.createOscillator(); lfo.type = 'sine'; lfo.frequency.value = 0.7;
  var lfoGain = ctx.createGain(); lfoGain.gain.value = 6;
  lfo.connect(lfoGain); lfoGain.connect(o.frequency);
  o.connect(g); o.start(); lfo.start();
  o.stop(ctx.currentTime + 1.5); lfo.stop(ctx.currentTime + 1.5);
}
function scheduleThunder() {
  if (thunderTimer) clearTimeout(thunderTimer);
  // 8-15 秒随机间隔，暂停时不再触发
  var delay = 8000 + Math.random() * 7000;
  thunderTimer = setTimeout(function() {
    if (playing) playThunder();
    scheduleThunder();
  }, delay);
}
function toggleAmbient() {
  var btn = document.getElementById('ambient-btn');
  if (!ctx) initAudio();
  if (ctx.state === 'suspended') ctx.resume();
  playing = !playing;
  if (playing) {
    masterGain.gain.setTargetAtTime(volume, ctx.currentTime, 0.2);
    btn.textContent = '⏸ 暂停雷雨';
    btn.style.borderColor = '#C0392B'; btn.style.color = '#F09595';
    scheduleThunder();
  } else {
    masterGain.gain.setTargetAtTime(0, ctx.currentTime, 0.2);
    btn.textContent = '▶ 开启雷雨';
    btn.style.borderColor = '#6b4f2a'; btn.style.color = '#C9A227';
    if (thunderTimer) { clearTimeout(thunderTimer); thunderTimer = null; }
  }
}
function setVolume(v) {
  volume = parseFloat(v);
  if (playing && ctx) masterGain.gain.setTargetAtTime(volume, ctx.currentTime, 0.05);
}
</script>
"""


# ==================== C-1：开场动画（打字机 + 红章 + 幕布，仅开局一次） ====================
# __TITLE__ 占位符由 _render_intro_animation 用 json.dumps 转义后替换（防引号注入）
_INTRO_HTML = """
<div data-tid="__TID__" style="font-family:'KaiTi','楷体',serif; text-align:center; padding:26px 18px; background:#141110; border:1px solid #6b4f2a; border-radius:12px; color:#E8DCC8; min-height:250px; display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; overflow:hidden;">
  <div id="intro-curtain" style="position:absolute; inset:0; background:#0d0b0a; z-index:3;"></div>
  <div id="intro-body" style="opacity:0; z-index:2;">
    <div style="font-size:12px; color:#8a7a5f; letter-spacing:4px; margin-bottom:14px;">案件卷宗 · 编号 7-0313</div>
    <div id="intro-title" style="font-size:25px; color:#C9A227; letter-spacing:8px; margin:0; font-weight:500; min-height:36px;"></div>
    <div id="intro-sub" style="font-size:12px; color:#a2947a; margin-top:14px; opacity:0; letter-spacing:2px;">真相散落在每个人手里，只有互相盘问才能拼出全貌</div>
  </div>
  <div id="intro-stamp" style="position:absolute; z-index:2; font-size:30px; color:#C0392B; border:3px solid #C0392B; padding:2px 14px; border-radius:6px; transform:rotate(-14deg) scale(2.4); opacity:0; letter-spacing:6px; font-weight:500;">机密</div>
</div>
<script>
(function(){
  var title = __TITLE__;
  var i = 0;
  var titleEl = document.getElementById('intro-title');
  var curtain = document.getElementById('intro-curtain');
  var body = document.getElementById('intro-body');
  var stamp = document.getElementById('intro-stamp');
  var sub = document.getElementById('intro-sub');
  var t = setInterval(function(){
    if (i <= title.length) {
      titleEl.textContent = title.slice(0, i);
      i++;
    } else {
      clearInterval(t);
      stamp.style.transition = 'transform 0.3s ease, opacity 0.15s ease';
      stamp.style.transform = 'rotate(-14deg) scale(1)';
      stamp.style.opacity = '1';
      setTimeout(function(){
        stamp.style.transition = 'opacity 0.5s ease';
        stamp.style.opacity = '0';
        body.style.opacity = '1';
        sub.style.transition = 'opacity 0.8s ease 0.3s';
        sub.style.opacity = '1';
        curtain.style.transition = 'opacity 0.9s ease';
        curtain.style.opacity = '0';
      }, 650);
    }
  }, 90);
})();
</script>
"""


# ==================== C-3/C-4：阶段过渡幕布 + 事件音效 ====================
# __BANNER__（大字标题）和 __SFX__（讨论/投票/揭晓 音效种类）占位符，渲染时替换。
# key = thread_id + phase：阶段切换才重挂载 → 动画 + 音效播一次；跨局重玩 key 变不串。
_PHASE_BANNER_HTML = """
<div data-tid="__TID__" style="text-align:center; padding:10px 0; font-family:'KaiTi','楷体',serif;">
  <div id="phase-banner" style="display:inline-block; background:#1d1916; border:1px solid #C9A227; border-radius:10px; padding:10px 30px; color:#C9A227; font-size:18px; letter-spacing:6px; opacity:0;">
    __BANNER__
  </div>
</div>
<style>
@keyframes bannerIn { from { opacity:0; transform:scale(0.85);} to { opacity:1; transform:scale(1);} }
</style>
<script>
(function(){
  var el = document.getElementById('phase-banner');
  el.style.animation = 'bannerIn 0.8s ease forwards';
  var c = new (window.AudioContext || window.webkitAudioContext)();
  var g = c.createGain(); g.connect(c.destination);
  var kind = '__SFX__';
  if (kind === 'bell') {
    var o = c.createOscillator(); o.type = 'sine'; o.frequency.value = 392;
    g.gain.setValueAtTime(0.22, c.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 1.8);
    o.connect(g); o.start(); o.stop(c.currentTime + 1.9);
  } else if (kind === 'drum') {
    var o = c.createOscillator(); o.type = 'sine'; o.frequency.value = 110;
    g.gain.setValueAtTime(0.3, c.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.5);
    o.connect(g); o.start(); o.stop(c.currentTime + 0.6);
  } else {
    var o = c.createOscillator(); o.type = 'triangle'; o.frequency.value = 520;
    g.gain.setValueAtTime(0.1, c.currentTime);
    g.gain.exponentialRampToValueAtTime(0.001, c.currentTime + 0.2);
    o.connect(g); o.start(); o.stop(c.currentTime + 0.25);
  }
})();
</script>
"""


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


# ==================== 沉浸 UI 渲染函数 ====================

def _render_ambient_audio():
    """侧边栏底部：氛围音乐控制条（常驻，rerun 不闪断）。

    音频状态（播放/音量）由 iframe 内部 JS 持有，不经过 Streamlit——
    组件 HTML 是常量 + 调用位置固定，Streamlit rerun 时 React 复用 iframe，
    音乐不中断（注意：st.components.v1.html 不支持 key 参数，
    复用靠"位置稳定 + HTML 不变"，跨局重玩时音乐回到暂停态是合理的）。
    """
    with st.sidebar:
        st.components.v1.html(_AMBIENT_AUDIO_HTML, height=84)


def _resolve_case_title(result: dict) -> str:
    """从 result 提取案件卷宗标题：优先 script.background 前段（具体有画面感），
    兜底 theme（不能是"自由发挥"），最后"未命名卷宗"。

    8-20 飞哥反馈：选"自由发挥"时卷宗标题直接显示"自由发挥"——没信息量且破坏神秘感。
    修复：用脚本生成的具体案件描述前段做标题，"自由发挥"兜底成"未命名卷宗"。
    """
    script = result.get("script", {}) or {}
    bg = (script.get("background", "") or "").strip()
    if bg:
        # 取前 28 字作为卷宗标题，保留"……"暗示有完整 background
        if len(bg) > 28:
            return bg[:28] + "……"
        return bg
    theme = (result.get("theme") or "").strip()
    if theme and theme != "自由发挥":
        return theme
    return "未命名卷宗"


def _render_intro_animation(result: dict):
    """开局一次性：案件封面动画（打字机 + 红章 + 幕布）。

    session_state["intro_played"] 控制只播一次；title 经 json.dumps 转义防注入。
    thread_id 拼进 HTML（data 属性）——跨局重玩时 HTML 变化触发 iframe 重挂载，
    动画才能重播（st.components.v1.html 不支持 key 参数，用此法替代）。
    """
    case_title = _resolve_case_title(result)
    safe_title = json.dumps(case_title, ensure_ascii=False)
    html = (_INTRO_HTML
            .replace("__TITLE__", safe_title)
            .replace("__TID__", st.session_state.thread_id))
    st.components.v1.html(html, height=310)


def _render_phase_banner(result: dict, total_rounds: int):
    """阶段过渡幕布 + 事件音效（HTML 含 thread_id + phase，阶段变或跨局重玩才重挂载）。

    phase: discuss →「自由讨论开始」/ vote →「烛光投票」/ reveal →「真相大白」。
    """
    phase = result.get("current_phase", "")
    labels = {
        "discuss": ("自由讨论开始", "tick"),
        "vote": ("烛光投票", "bell"),
        "reveal": ("真相大白", "drum"),
    }
    if phase not in labels:
        return
    banner, sfx = labels[phase]
    html = (_PHASE_BANNER_HTML
            .replace("__BANNER__", banner)
            .replace("__SFX__", sfx)
            .replace("__TID__", st.session_state.thread_id))
    st.components.v1.html(html, height=58)


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


st.set_page_config(page_title="AI 剧本杀主持人", page_icon="🕯", layout="wide")
st.markdown(_THEME_CSS, unsafe_allow_html=True)
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
if "intro_played" not in st.session_state:
    st.session_state.intro_played = False   # 开场动画只播一次

config = {"configurable": {"thread_id": st.session_state.thread_id}}

st.title("🕯 剧本杀 · 推理之夜")
st.caption("卷宗已开启——你扮演其中一个嫌疑人，找出真凶。")

# ---- 未开始：输入主题 + 选背景风格 + 可选自定义剧情背景 ----
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
    if st.button("🔍 立案侦查", type="primary"):
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
            st.info("可能是网络波动或 DeepSeek 临时限流，请稍后点击「立案侦查」重试。")
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
        # C-1：开场动画只播一次（session_state 标记），播完显示选角引导
        if not st.session_state.intro_played:
            _render_intro_animation(result)
            st.session_state.intro_played = True
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
            _render_ambient_audio()
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

    # C-4：阶段过渡幕布 + 事件音效（key 随 thread_id + phase，跨局重玩不串动画）
    _render_phase_banner(result, len(suspects) * result.get("rounds_per_player", 3))
    # 轮次进度（普通 markdown，每次 rerun 更新；无动画，不干扰幕布）
    round_num = result.get("phase_round", 0)
    total_rounds = len(suspects) * result.get("rounds_per_player", 3)
    if total_rounds > 0 and round_num < total_rounds:
        remaining = total_rounds - round_num
        st.caption(f"🗝 讨论进度 · 已进行 {round_num} 轮 / 共 {total_rounds} 轮 · 剩余 {remaining} 轮")

    # 角色卡（侧边栏）
    with st.sidebar:
        st.header("🪪 你的角色卡")
        st.subheader(user_role)
        # 嫌疑人总览：提到角色卡顶部最显眼位置（飞哥 8-20 反馈"选完角色后原本 4 人
        # 只剩 2 个"的根因——这条原本在 sidebar 最底部，被长角色卡挤出可见区域 +
        # st.write 在长 sidebar 里循环渲染不稳定）。改用 expander 折叠（默认展开）+
        # markdown 列表强制渲染所有嫌疑人，确保全员任何时候都可见。
        if suspects:
            with st.expander(f"👥 嫌疑人总览（共 {len(suspects)} 人）", expanded=True):
                for s in suspects:
                    sname = s.get("name", "?")
                    sprof = s.get("profession", "")
                    mark = " ←（你）" if sname == user_role else ""
                    # markdown 渲染（比 st.write 在 sidebar 长内容里更稳定，不丢字符）
                    st.markdown(f"· **{sname}**{mark}  _{sprof or '（未提供职业）'}_")
        st.divider()
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
        # 注：原"嫌疑人名单"区域已移除（已合并到顶部 expander，避免重复 + 被裁）
        st.divider()
        # 人物关系图（仅公开关系，私密关系属信息差不显示）：侧边栏放按钮，点击后屏幕中央弹大图
        st.caption("🕸️ 人物关系图")
        relations_html = build_relations_html(script.get("relations", []), [s.get("name", "?") for s in suspects])
        if relations_html:
            if st.button("🔍 展开关系网", use_container_width=True):
                _relations_dialog(relations_html)
        else:
            st.caption("（剧本未生成公开关系）")
        st.divider()
        # C-2：氛围音乐控制条（常驻，固定 key）
        _render_ambient_audio()

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
                    if st.button("🌙 暗中调查现场", key="investigate_btn"):
                        st.session_state.result = _run_stream(
                            graph, Command(resume={"action": "investigate"}), config
                        )
                        st.rerun()
                else:
                    st.caption("（现场已经搜遍，没有新发现了）")

            # 发言输入（默认动作，显式 key 与投票输入框隔离，防止值残留串台）
            user_input = st.chat_input(f"轮到你了（{info['speaker']}），说出你的证词...", key="speak_input")
            if user_input:
                st.session_state.result = _run_stream(graph, Command(resume=user_input), config)
                st.rerun()
        elif info["type"] == "human_vote":
            user_input = st.chat_input(f"🕯 烛光投票：{', '.join(info['suspects'])}，你指认谁？", key="vote_input")
            if user_input:
                # 校验：必须是合法嫌疑人名字，防止残留的发言文本被当成投票
                if validate_vote(user_input, info["suspects"]):
                    st.session_state.result = _run_stream(graph, Command(resume=user_input), config)
                    st.rerun()
                else:
                    st.warning(f"「{user_input}」不在嫌疑人名单里，请重新输入正确的名字")

    # 游戏结束：显示结算
    else:
        st.success("🎉 真相大白！")
        with st.expander("📊 投票结果", expanded=True):
            for voter, target in result.get("votes", {}).items():
                st.write(f"· {voter} → {target}")
            st.write(f"**得票最多：{result.get('vote_winner', '无人')}**")
            st.write(f"票数分布：{result.get('vote_counts', {})}")
        if st.button("🕯 翻开新卷宗", type="primary"):
            # 清理当前 thread 的所有 checkpoint，释放 MemorySaver 内存。
            # @st.cache_resource 让图单例常驻，MemorySaver 会累积每局的每一步快照，
            # 不清理的话长时间运行内存持续增长（checkpointer.delete 是 langgraph>=1.0 的接口）
            old_config = {"configurable": {"thread_id": st.session_state.thread_id}}
            try:
                graph.checkpointer.delete(old_config)
            except Exception:
                pass
            for key in ["started", "result", "thread_id", "intro_played"]:
                st.session_state.pop(key, None)
            st.rerun()

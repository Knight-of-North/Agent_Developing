"""UI 主题样式（从 app.py 拆出，便于独立维护）。"""

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
div.stButton button {
  background: var(--murder-input);
  color: var(--murder-gold);
  border: 1px solid var(--murder-border);
  border-radius: 8px;
  font-family: var(--murder-serif);
  font-size: clamp(14px, 0.72vw, 18px);
  transition: border-color 0.2s ease;
}
div.stButton button:hover {
  border-color: var(--murder-gold);
  color: #E8DCC8;
}
div.stButton button[kind="primary"] {
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
  font-size: clamp(14px, 0.72vw, 18px) !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stTextArea"] textarea::placeholder {
  color: #9a8e75 !important;   /* 8-20 提亮：#6f6550 → #9a8e75 */
  font-family: var(--murder-sans);
}
/* Selectbox 暗色化（飞哥 8-20 反馈"填入栏还是白底"——下拉打开态已 OK，但收起态触发器漏了）：
   Streamlit 1.61 baseweb Select 触发器可能用 background-image 或 box-shadow 模拟白底，
   或者有更高优先级的 specificity 元素盖在前面。必须：
   ① 列尽所有可能的元素类型（div/span/input/button + role=button/combobox）
   ③ background-image: none + box-shadow: none 兜底（baseweb 常用这两种方式做白底）
   ④ 给整个 selectbox 容器加 padding 让深色背景延伸覆盖触发器边框 */
[data-testid="stSelectbox"] div,
[data-testid="stSelectbox"] span,
[data-testid="stSelectbox"] input,
[data-testid="stSelectbox"] button,
[data-testid="stSelectbox"] [role="button"],
[data-testid="stSelectbox"] [role="combobox"],
[data-testid="stSelectbox"] [data-baseweb="select"],
[data-testid="stSelectbox"] [data-baseweb="select"] * {
  background-color: var(--murder-input) !important;
  background-image: none !important;   /* 兜底：baseweb 可能用 image 模拟白底 */
  box-shadow: none !important;         /* 兜底：baseweb 可能用 shadow 模拟白底光晕 */
  color: var(--murder-text) !important;
}
[data-testid="stSelectbox"] [data-baseweb="select"] {
  border: 1px solid var(--murder-border) !important;
  border-radius: 8px;
}
[data-testid="stSelectbox"] [data-baseweb="select"]:hover {
  border-color: var(--murder-gold) !important;
}
[data-testid="stRadio"] label {
  color: var(--murder-text);
}
/* 下拉打开后的虚拟 dropdown（baseweb Menu/Listbox，DOM 在 stSelectbox 之外的 portal）：
   同样用 * 通配穿透所有子元素 + image/shadow 兜底；hover/选中态给金色高亮提示。 */
[data-testid="stSelectboxVirtualDropdown"] * {
  background-color: var(--murder-input) !important;
  background-image: none !important;
  box-shadow: none !important;
  color: var(--murder-text) !important;
}
[data-testid="stSelectboxVirtualDropdown"] [role="option"]:hover,
[data-testid="stSelectboxVirtualDropdown"] [aria-selected="true"] {
  background-color: var(--murder-card) !important;
  background-image: none !important;
  color: var(--murder-gold) !important;
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
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p,
[data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li {
  font-size: clamp(15px, 0.74vw, 20px);
  line-height: 1.6;
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
  font-size: clamp(14px, 0.72vw, 18px) !important;
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
/* Expander 暗色化（飞哥 8-20 反馈"展开后内容区是白底"——和 selectbox 同类问题）：
   Streamlit 1.61 expander 用 baseweb Accordion，details 内容区有自己的白底样式覆盖在前面。
   必须用和 selectbox 同款的「* 通配 + image/shadow 兜底」穿透所有子元素。
   标题栏保留 serif 衬线感（沉浸感），内容区用 sans-serif（清晰可读）。
   8-20 飞哥再次反馈"expander header 还是白的"：之前漏了 <summary>（原生 HTML
   的折叠头）和 .streamlit-expanderHeader 的 background 选择器，浏览器默认
   summary 是白底，必须显式压住。 */
[data-testid="stExpander"],
[data-testid="stExpander"] details,
[data-testid="stExpander"] details > div,
[data-testid="stExpander"] details > summary,
[data-testid="stExpander"] summary,
[data-testid="stExpander"] [data-baseweb="accordion"],
[data-testid="stExpander"] [data-baseweb="accordion"] *,
[data-testid="stExpander"] [data-baseweb="accordion"] summary,
[data-testid="stExpander"] .streamlit-expanderHeader {
  background-color: var(--murder-card) !important;
  background-image: none !important;   /* 兜底：baseweb 可能用 image 模拟白底 */
  box-shadow: none !important;         /* 兜底：baseweb 可能用 shadow 模拟白底光晕 */
}
[data-testid="stExpander"] {
  border: 1px solid var(--murder-border) !important;
  border-radius: 10px;
}
.streamlit-expanderHeader {
  color: var(--murder-text) !important;
  font-family: var(--murder-serif);
  background-color: var(--murder-card) !important;
  background-image: none !important;
}
/* 内容区文字色强制米色（默认 baseweb 强制黑底白字会变白底黑字，必须显式覆盖） */
[data-testid="stExpander"] [data-testid="stMarkdownContainer"],
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] p,
[data-testid="stExpander"] [data-testid="stMarkdownContainer"] li {
  color: var(--murder-text) !important;
  background-color: transparent !important;   /* 让 expander 容器背景透出，避免嵌套白底 */
}
[data-testid="stCaptionContainer"] {
  color: var(--murder-muted);
  font-family: var(--murder-sans);   /* 8-20：caption 改无衬线，避免楷体小字号笔画糊 */
  font-size: clamp(14px, 0.85vw, 19px);
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

/* ==================== 2026-11 开始页重构 ==================== */
/* Streamlit 自带 heading 选择器比裸 h1 更具体，之前 h1 规则实际没生效；
   游戏页标题也用同一套衬线字体。 */
[data-testid="stHeadingWithActionElements"] h1,
[data-testid="stHeadingWithActionElements"] h2,
[data-testid="stHeadingWithActionElements"] h3 {
  font-family: var(--murder-serif) !important;
  letter-spacing: 2px;
}
[data-testid="stHeadingWithActionElements"] h1 { font-size: clamp(40px, 1.9vw, 56px) !important; }
[data-testid="stHeadingWithActionElements"] h2 { font-size: clamp(28px, 1.4vw, 40px) !important; }
[data-testid="stHeadingWithActionElements"] h3 { font-size: clamp(22px, 1vw, 30px) !important; }
[data-testid="stToolbar"] { display: none !important; }

/* 开始页内容列：默认 1120px；在 1500px 以上按视口放大，避免 2K/4K 全屏时缩成一小块 */
.st-key-start_page {
  max-width: 1120px;
  margin: -8px auto 0;
  padding-top: 8px;
}
@media (min-width: 1500px) {
  .st-key-start_page {
    max-width: min(92vw, 2300px);
  }
}
.start-hero { padding: 0 0 8px; }
.start-eyebrow {
  font-family: var(--murder-sans);
  font-size: clamp(11px, 0.75vw, 16px);
  letter-spacing: clamp(4px, 0.25vw, 7px);
  color: var(--murder-gold);
  margin-bottom: clamp(6px, 0.5vw, 10px);
}
.start-title {
  font-family: var(--murder-serif) !important;
  font-size: clamp(42px, 3.1vw, 84px) !important;
  line-height: 1.08 !important;
  letter-spacing: clamp(3px, 0.22vw, 6px) !important;
  font-weight: 700;
  color: var(--murder-text) !important;
  margin: 0 0 clamp(6px, 0.5vw, 12px);
}
.start-sub {
  font-size: clamp(15px, 1.05vw, 22px) !important;
  line-height: 1.45;
  color: var(--murder-muted);
  letter-spacing: 0.5px;
  margin: 0;
}
.start-chips { display: flex; flex-wrap: wrap; gap: clamp(8px, 0.55vw, 12px); margin-top: clamp(10px, 0.7vw, 16px); }
.start-chip {
  font-family: var(--murder-sans);
  font-size: clamp(11px, 0.8vw, 16px);
  color: var(--murder-muted);
  border: 1px solid rgba(107, 79, 42, .75);
  background: rgba(29, 25, 22, .6);
  border-radius: 20px;
  padding: clamp(5px, .35vw, 8px) clamp(12px, .8vw, 18px);
  letter-spacing: 1px;
}
.start-chip b { color: var(--murder-gold); font-weight: 600; }

/* 表单容器做成一张卷宗卡 */
.st-key-start_form_card {
  background: var(--murder-card);
  border: 1px solid var(--murder-border);
  border-radius: clamp(12px, .7vw, 18px);
  padding: clamp(18px, 1.45vw, 34px) clamp(22px, 1.7vw, 40px) clamp(16px, 1.2vw, 30px);
  box-shadow: 0 18px 50px rgba(0, 0, 0, .28);
}
.st-key-start_form_card [data-testid="stVerticalBlock"] { gap: 0.35rem; }
[data-testid="stWidgetLabel"] p {
  font-family: var(--murder-sans) !important;
  font-size: clamp(13px, 1vw, 19px) !important;
  color: var(--murder-text) !important;
  letter-spacing: .3px;
}
.start-section {
  font-family: var(--murder-sans);
  font-size: clamp(12px, 0.85vw, 17px);
  color: var(--murder-gold);
  letter-spacing: clamp(3px, .2vw, 5px);
  margin: clamp(4px, .3vw, 8px) 0 clamp(10px, .7vw, 16px);
  display: flex;
  align-items: center;
  gap: 10px;
}
.start-section:after {
  content: "";
  height: 1px;
  flex: 1;
  background: linear-gradient(90deg, rgba(107, 79, 42, .55), transparent);
}

/* Pills / segmented control：统一成档案风按钮（1.61 都渲染在 stButtonGroup 里） */
[data-testid="stButtonGroup"] button[data-variant] {
  background: var(--murder-input) !important;
  border: 1px solid #4a3925 !important;
  border-radius: 8px !important;
  color: var(--murder-text) !important;
  font-family: var(--murder-sans) !important;
  font-size: clamp(12px, 0.8vw, 17px) !important;
  padding: clamp(8px, .5vw, 12px) clamp(12px, .8vw, 18px) !important;
}
[data-testid="stButtonGroup"] button[data-variant]:hover {
  border-color: var(--murder-gold) !important;
  color: var(--murder-gold) !important;
}
[data-testid="stButtonGroup"] button[data-variant][aria-checked="true"] {
  background: #262016 !important;
  border-color: var(--murder-gold) !important;
  color: var(--murder-gold) !important;
}

/* 开始页 CTA：整行、明显、贴近“开局”动作 */
.st-key-start_form_card div.stButton button[kind="primary"] {
  min-height: clamp(54px, 3.8vw, 82px);
  margin-top: clamp(4px, .3vw, 8px);
  font-size: clamp(20px, 1.7vw, 34px);
  letter-spacing: clamp(4px, .35vw, 8px);
  border-radius: 8px;
  color: #FFF3E0 !important;
  box-shadow: 0 6px 20px rgba(192, 57, 43, .28);
}
.start-cta-note {
  text-align: center;
  font-family: var(--murder-sans);
  font-size: clamp(11px, 0.75vw, 16px);
  color: #9a8e75;
  margin-top: clamp(8px, .6vw, 12px);
  letter-spacing: .5px;
}

/* 右侧说明卡 */
.start-side { display: flex; flex-direction: column; gap: clamp(16px, 1vw, 24px); }
.side-card {
  background: var(--murder-card);
  border: 1px solid var(--murder-border);
  border-radius: clamp(12px, .7vw, 18px);
  padding: clamp(18px, 1.25vw, 30px);
  position: relative;
  overflow: hidden;
  box-shadow: 0 18px 50px rgba(0, 0, 0, .18);
}
.side-title {
  font-family: var(--murder-sans);
  font-size: clamp(12px, 0.85vw, 18px);
  color: var(--murder-gold);
  letter-spacing: clamp(3px, .18vw, 5px);
  margin-bottom: clamp(14px, .9vw, 20px);
}
.side-step { display: flex; gap: clamp(11px, .7vw, 16px); margin-bottom: clamp(13px, .8vw, 18px); }
.side-step:last-of-type { margin-bottom: 0; }
.side-num {
  width: clamp(22px, 1.5vw, 38px);
  height: clamp(22px, 1.5vw, 38px);
  border: 1px solid #7a6036;
  border-radius: 50%;
  color: var(--murder-gold);
  font-family: var(--murder-sans);
  font-size: clamp(10px, .65vw, 16px);
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
  margin-top: 1px;
}
.side-step .t { font-size: clamp(12.5px, 0.95vw, 19px); color: var(--murder-text); letter-spacing: .5px; }
.side-step .d { font-size: clamp(10px, 0.75vw, 15px); color: #9a8e75; line-height: 1.5; margin-top: 2px; }
.side-seal {
  position: absolute;
  right: -10px;
  bottom: -14px;
  width: clamp(82px, 5vw, 140px);
  height: clamp(82px, 5vw, 140px);
  border: 2px solid rgba(192, 57, 43, .75);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(192, 57, 43, .85);
  font-family: var(--murder-serif);
  font-size: clamp(18px, .9vw, 24px);
  letter-spacing: 3px;
  transform: rotate(-14deg);
  opacity: .85;
}
.side-seal:after {
  content: "";
  position: absolute;
  inset: 6px;
  border: 1px solid rgba(192, 57, 43, .55);
  border-radius: 50%;
}
.side-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 10px; }
.side-fact { padding: 6px 0; border-bottom: 1px solid rgba(107, 79, 42, .25); }
.side-fact .k { font-size: clamp(9px, 0.6vw, 14px); color: #9a8e75; letter-spacing: 1px; }
.side-fact .v { font-size: clamp(11.5px, 0.8vw, 18px); color: var(--murder-text); margin-top: 2px; }

/* 最近战绩（仅当前会话，刷新清空） */
.history-card { padding-bottom: 10px; }
.history-list { display: flex; flex-direction: column; gap: 3px; }
.history-row { display: flex; align-items: center; gap: clamp(9px, .55vw, 13px); padding: clamp(7px, .45vw, 10px) 0; border-bottom: 1px solid rgba(107, 79, 42, .18); }
.history-row:last-child { border-bottom: 0; padding-bottom: 2px; }
.history-badge {
  width: clamp(19px, .9vw, 26px); height: clamp(19px, .9vw, 26px); flex: none;
  display: flex; align-items: center; justify-content: center;
  border-radius: 50%;
  font-family: 'Georgia', serif;
  font-size: clamp(10px, .45vw, 13px);
}
.history-badge.win { color: #C0DD97; border: 1px solid rgba(59, 109, 17, .65); background: rgba(26, 36, 22, .5); }
.history-badge.lose { color: #F09595; border: 1px solid rgba(138, 32, 32, .65); background: rgba(42, 15, 12, .45); }
.history-main { min-width: 0; flex: 1; }
.history-case {
  font-size: clamp(11px, 0.75vw, 17px); color: var(--murder-text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.history-meta { margin-top: 2px; font-size: clamp(9px, 0.6vw, 14px); color: #9a8e75; line-height: 1.4; }
.history-time { flex: none; font-size: clamp(8px, .4vw, 11px); color: #6f6049; letter-spacing: .5px; }

/* 生成进度卡：不显示原始 JSON，避免开局前泄露凶手/真相 */
.gen-wrap {
  background: var(--murder-card);
  border: 1px solid var(--murder-border);
  border-radius: 12px;
  padding: clamp(20px, 1.3vw, 30px) clamp(22px, 1.5vw, 36px) clamp(18px, 1.1vw, 26px);
  margin-top: clamp(16px, 1vw, 24px);
}
.gen-top { display: flex; align-items: center; gap: clamp(14px, .9vw, 20px); }
.gen-spinner {
  width: clamp(28px, 1.5vw, 40px);
  height: clamp(28px, 1.5vw, 40px);
  border: 2px solid rgba(201, 162, 39, .22);
  border-top-color: var(--murder-gold);
  border-radius: 50%;
  animation: gen-spin 1s linear infinite;
  flex: none;
}
@keyframes gen-spin { to { transform: rotate(360deg); } }
.gen-title {
  font-family: var(--murder-serif);
  font-size: clamp(20px, 1.4vw, 34px);
  color: var(--murder-text);
  letter-spacing: 2px;
}
.gen-meta { font-family: var(--murder-sans); font-size: clamp(11px, .75vw, 16px); color: #9a8e75; margin-top: 3px; }
.gen-bar { margin-top: clamp(16px, 1vw, 24px); height: 4px; background: #2a2118; border-radius: 2px; overflow: hidden; }
.gen-bar > span {
  display: block;
  height: 100%;
  background: linear-gradient(90deg, #8a2020, var(--murder-gold));
  transition: width .6s ease;
}
.gen-steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: clamp(8px, .55vw, 12px); margin-top: clamp(14px, .9vw, 20px); }
.gen-step { position: relative; padding-left: 10px; font-size: clamp(10px, .7vw, 16px); color: #6f6049; line-height: 1.4; }
.gen-step:before {
  content: "";
  position: absolute;
  left: 0;
  top: 5px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #5a4a33;
}
.gen-step.active { color: var(--murder-muted); }
.gen-step.active:before { background: var(--murder-gold); box-shadow: 0 0 6px rgba(201, 162, 39, .5); }
.gen-step.done { color: #9a8e75; }
.gen-step.done:before { background: #3B6D11; }
.gen-complete {
  border-color: rgba(201, 162, 39, .55);
}
.gen-complete .gen-spinner {
  animation: none;
  border-color: var(--murder-gold);
  background: radial-gradient(circle, rgba(201,162,39,.9) 0 3px, transparent 4px);
}

@media (max-width: 900px) {
  .start-title { font-size: 36px !important; }
  .st-key-start_page { padding-left: 4px; padding-right: 4px; }
  .gen-steps { grid-template-columns: repeat(2, 1fr); }
  .side-card { margin-top: 4px; }
}
@media (max-width: 520px) {
  .start-title { font-size: 28px !important; letter-spacing: 2px !important; }
  .start-sub { font-size: 13px; }
  .start-chip { font-size: 10px; }
}
/* 1366×768 等矮屏：压掉装饰性 hero 高度，保证「立案侦查」在首屏可见 */
@media (max-height: 820px) {
  .start-eyebrow { display: none; }
  .start-title { font-size: 34px !important; letter-spacing: 3px !important; margin-bottom: 4px; }
  .start-sub { font-size: 14px; }
  .start-chips { margin-top: 8px; gap: 6px; }
  .start-chip { padding: 4px 10px; }
  .st-key-start_form_card { padding-top: 14px; }
}
</style>
"""

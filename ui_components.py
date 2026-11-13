"""游戏页 UI 渲染组件（从 app.py 拆出）。

只负责展示/浏览器交互，不包含游戏规则或图状态推进。
"""
import json

import streamlit as st

from ui_assets import _INTRO_HTML, _PHASE_BANNER_HTML

_GEN_STAGES = [
    ("铺开卷宗", "根据主题与风格搭建案件骨架"),
    ("安排嫌疑人", "为每个角色写身份、秘密与不在场证明"),
    ("埋设线索", "分配私密线索与隐藏线索"),
    ("校验剧本", "检查人物关系、凶手与线索是否自洽"),
]

def _render_generation_progress(slot, stage_idx: int, elapsed: float, retry_count: int = 0,
                                complete: bool = False) -> None:
    """渲染主题化生成进度卡；slot 是 st.empty()，反复更新不新增页面元素。"""
    stage_idx = max(0, min(stage_idx, len(_GEN_STAGES) - 1))
    pct = 100 if complete else int((stage_idx + 0.5) / len(_GEN_STAGES) * 100)
    if complete:
        title = "卷宗已封存"
        meta = f"用时 {int(elapsed)} 秒 · 正在进入选角"
    else:
        title = "正在撰写卷宗"
        attempt = f"第 {retry_count + 1} 次尝试" if retry_count else "首次尝试"
        meta = f"{attempt} · 已用 {int(elapsed)} 秒"
    steps = []
    for i, (label, desc) in enumerate(_GEN_STAGES):
        if complete or i < stage_idx:
            cls = "done"
        elif i == stage_idx:
            cls = "active"
        else:
            cls = ""
        steps.append(
            f'<div class="gen-step {cls}"><b>{label}</b><br>{desc}</div>'
        )
    spinner = "" if complete else '<div class="gen-spinner"></div>'
    slot.markdown(
        f'<div class="gen-wrap{" gen-complete" if complete else ""}">'
        f'<div class="gen-top">{spinner}<div><div class="gen-title">{title}</div>'
        f'<div class="gen-meta">{meta}</div></div></div>'
        f'<div class="gen-bar"><span style="width:{pct}%"></span></div>'
        f'<div class="gen-steps">{"".join(steps)}</div></div>',
        unsafe_allow_html=True,
    )

def _preserve_scroll_position() -> None:
    """跨 rerun 保留游戏页的滚动位置。

    Streamlit 在 st.rerun() 后会重新渲染整页，浏览器默认把 stMain 滚回顶部。
    投票结束、DM 揭晓这类“生成长内容后 rerun”的场景，玩家正看到剧情中段就会被
    强制拉回页首。这里用非 iframe 的 st.html 注入 JS：
    - 监听 [data-testid="stMain"] 的滚动，把 scrollTop 存进 sessionStorage（按 thread_id 隔离）；
    - 每次 rerun 后恢复；内容还没渲染完时用短间隔重试，避免恢复发生在 DOM 高度不足时。
    """
    key = json.dumps(f"agent_developing:scroll:{st.session_state.get('thread_id', 'default')}")
    js = """
<script>
(function () {
  const KEY = __KEY__;
  const getScroller = () => document.querySelector('[data-testid="stMain"]')
    || document.scrollingElement || document.documentElement;
  const save = () => {
    // 恢复期间不反写，避免把“恢复中的旧位置”覆盖成新值；
    // 用户一滚轮/触摸就停止恢复，避免在底部往上滑时被脚本反复拉回去（抖动）。
    if (window.__agentScrollRestoring || window.__agentScrollUserInteracted) return;
    try {
      const el = getScroller();
      if (el) sessionStorage.setItem(KEY, String(el.scrollTop || 0));
    } catch (e) {}
  };
  if (!window.__agentScrollBound) {
    window.__agentScrollBound = true;
    // document 上的捕获监听可跨 Streamlit rerun 复用（stMain 元素可能被替换）
    document.addEventListener('scroll', save, true);
    window.addEventListener('scroll', save, { passive: true });
  }
  if (!window.__agentScrollIntentBound) {
    window.__agentScrollIntentBound = true;
    const mark = () => { window.__agentScrollUserInteracted = true; };
    ['wheel', 'touchstart', 'keydown', 'pointerdown'].forEach((evt) => {
      window.addEventListener(evt, mark, { passive: true, capture: true });
    });
  }
  window.__agentScrollUserInteracted = false;
  window.__agentScrollRestoring = true;
  const raw = sessionStorage.getItem(KEY);
  if (raw === null) { window.__agentScrollRestoring = false; return; }
  const y = parseInt(raw, 10) || 0;
  let tries = 0;
  const finish = () => { window.__agentScrollRestoring = false; };
  const restore = () => {
    if (!window.__agentScrollRestoring) return;
    if (window.__agentScrollUserInteracted) { finish(); return; }
    const el = getScroller();
    if (!el) {
      if (tries++ < 10) setTimeout(restore, 100);
      else finish();
      return;
    }
    const max = Math.max(0, el.scrollHeight - el.clientHeight);
    const target = Math.max(0, Math.min(y, max));
    el.scrollTop = target;
    if (Math.abs(el.scrollTop - target) > 2 && tries++ < 10) setTimeout(restore, 100);
    else finish();
  };
  requestAnimationFrame(() => setTimeout(restore, 50));
})();
</script>
""".replace("__KEY__", key)
    st.html(js, unsafe_allow_javascript=True)

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
    动画才能重播（st.iframe 不支持 key 参数，用此法替代）。
    """
    case_title = _resolve_case_title(result)
    safe_title = json.dumps(case_title, ensure_ascii=False)
    html = (_INTRO_HTML
            .replace("__TITLE__", safe_title)
            .replace("__TID__", st.session_state.get("thread_id", "default")))
    st.iframe(html, height=310)

def _result_outcome(result: dict) -> str:
    """返回 win / lose / draw，用于结算动画。"""
    game_result = result.get("game_result", "")
    user_is_murderer = bool(result.get("user_is_murderer"))
    if game_result == "平民胜利":
        return "lose" if user_is_murderer else "win"
    if game_result == "凶手胜利":
        return "win" if user_is_murderer else "lose"
    return "draw"


def render_result_effect(result: dict) -> None:
    """结算页一次性动画：胜利金色碎片，失败暗红压场，平局灰雾。

    使用 st.html 直接挂到主文档，不依赖外部图片/第三方库；
    动画层 pointer-events:none，3 秒后淡出，不会挡住复盘按钮。
    """
    outcome = _result_outcome(result)
    if outcome == "win":
        title, subtitle, css_class = "你赢了", "真相在你手中", "fx-win"
    elif outcome == "lose":
        title, subtitle, css_class = "你输了", "真凶逃过了审判", "fx-lose"
    else:
        title, subtitle, css_class = "真相未明", "没有人赢下这场审判", "fx-draw"

    html = f"""
<style>
#agent-result-fx {{
  position: fixed; inset: 0; z-index: 99999; pointer-events: none;
  display: flex; align-items: center; justify-content: center;
  overflow: hidden; opacity: 1; transition: opacity .7s ease;
}}
#agent-result-fx.fx-out {{ opacity: 0; }}
#agent-result-fx .fx-veil {{
  position: absolute; inset: 0;
  animation: fx-veil-in .5s ease-out both;
}}
#agent-result-fx.fx-win .fx-veil {{
  background: radial-gradient(circle at 50% 45%, rgba(201,162,39,.34), transparent 36%),
              radial-gradient(circle at 50% 55%, rgba(192,57,43,.18), transparent 55%),
              rgba(8,6,5,.42);
}}
#agent-result-fx.fx-lose .fx-veil {{
  background: radial-gradient(circle at 50% 48%, rgba(120,18,18,.22), transparent 42%),
              linear-gradient(180deg, rgba(8,6,5,.18), rgba(8,6,5,.72));
}}
#agent-result-fx.fx-draw .fx-veil {{
  background: radial-gradient(circle at 50% 50%, rgba(130,120,100,.20), transparent 50%),
              rgba(8,6,5,.58);
}}
#agent-result-fx .fx-center {{
  position: relative; text-align: center; color: #f6e6c8;
  transform: translateY(8px); animation: fx-center-in .8s cubic-bezier(.2,.9,.3,1) .15s both;
}}
#agent-result-fx .fx-seal {{
  width: 86px; height: 86px; margin: 0 auto 16px; border-radius: 50%;
  display: grid; place-items: center; font: 700 34px/1 serif;
  border: 2px solid rgba(255,235,190,.72);
  box-shadow: 0 0 0 8px rgba(201,162,39,.08), 0 0 36px rgba(201,162,39,.28);
}}
#agent-result-fx.fx-lose .fx-seal {{ border-color: rgba(220,120,100,.65); box-shadow: 0 0 36px rgba(160,25,20,.30); }}
#agent-result-fx .fx-title {{ font: 700 52px/1.1 "STKaiti", "KaiTi", serif; letter-spacing: .18em; text-shadow: 0 2px 24px rgba(0,0,0,.65); }}
#agent-result-fx .fx-sub {{ margin-top: 12px; color: #d4c4a0; font: 400 16px/1.5 sans-serif; letter-spacing: .12em; }}
#agent-result-fx .fx-particle {{
  position: absolute; left: 50%; top: 48%; width: 7px; height: 7px;
  border-radius: 1px; background: #e4bf55; opacity: .9;
  animation: fx-particle 1.8s cubic-bezier(.15,.75,.35,1) both;
  animation-delay: var(--d, 0s); transform: translate(0,0) rotate(0);
}}
#agent-result-fx.fx-lose .fx-particle {{ background: #9b2d22; }}
#agent-result-fx.fx-draw .fx-particle {{ background: #9b927f; }}
@keyframes fx-veil-in {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
@keyframes fx-center-in {{ from {{ opacity: 0; transform: translateY(24px) scale(.96); }} to {{ opacity: 1; transform: translateY(8px) scale(1); }} }}
@keyframes fx-particle {{
  0% {{ opacity: 0; transform: translate(0,0) rotate(0) scale(.3); }}
  12% {{ opacity: 1; }}
  100% {{ opacity: 0; transform: translate(var(--x), var(--y)) rotate(var(--r)) scale(1); }}
}}
</style>
<div id="agent-result-fx" class="{css_class}">
  <div class="fx-veil"></div>
  <div class="fx-center">
    <div class="fx-seal">{ "✓" if outcome == "win" else ("!" if outcome == "lose" else "?") }</div>
    <div class="fx-title">{title}</div>
    <div class="fx-sub">{subtitle}</div>
  </div>
</div>
<script>
(function () {{
  const root = document.getElementById('agent-result-fx');
  if (!root) return;
  const win = {str(outcome == "win").lower()};
  const n = win ? 54 : 24;
  for (let i = 0; i < n; i++) {{
    const p = document.createElement('i');
    p.className = 'fx-particle';
    const angle = (Math.PI * 2 * i / n) + (Math.random() - .5) * .35;
    const dist = 130 + Math.random() * (win ? 420 : 220);
    p.style.setProperty('--x', (Math.cos(angle) * dist).toFixed(1) + 'px');
    p.style.setProperty('--y', (Math.sin(angle) * dist - 70).toFixed(1) + 'px');
    p.style.setProperty('--r', (Math.random() * 720 - 360).toFixed(0) + 'deg');
    p.style.setProperty('--d', (Math.random() * .35).toFixed(2) + 's');
    root.appendChild(p);
  }}
  setTimeout(() => root.classList.add('fx-out'), 2800);
  setTimeout(() => root.remove(), 3600);
}})();
</script>
"""
    st.html(html, unsafe_allow_javascript=True)


def _render_phase_banner(result: dict):
    """阶段过渡幕布（纯视觉；HTML 含 thread_id + phase，阶段变或跨局重玩才重挂载）。

    phase: discuss →「自由讨论开始」/ vote →「烛光投票」/ reveal →「真相大白」。
    对应音效由 ui_audio 的音频引擎统一播放，iframe 内不再创建 AudioContext。
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
            .replace("__TID__", st.session_state.get("thread_id", "default")))
    # 58px 会裁掉 padding + 18px 字号（实测内容约 63px），留到 72px 并压掉 body margin。
    st.iframe(html, height=72)

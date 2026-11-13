"""角色视觉层：首字头像、选角卡、侧栏角色卡、嫌疑人总览。

设计原则（对应优化方案 6.7 的低成本做法）：
- 不接 AI 生图 / 外部图片服务，只用「姓名首字 + 确定性渐变 + 身份标签」；
- 同一角色在任何页面颜色一致（hash 姓名取色，跨进程稳定）；
- 所有剧本字段先 html.escape，避免 LLM 输出被当作 HTML 注入；
- 纯展示组件，不碰 graph / 规则层。
"""
from __future__ import annotations

import base64
import hashlib
import html
from functools import lru_cache

import streamlit as st

# 深色档案风配色：暗红 / 墨绿 / 紫檀 / 古金 / 钢蓝 / 梅子 / 橄榄 / 琥珀
_AVATAR_PALETTE = (
    ("#6E2A20", "#2B1510", "#F2D9BC"),
    ("#1F4E4A", "#0E2422", "#D8E8DC"),
    ("#4A3A6B", "#1C1630", "#E4D8F0"),
    ("#5C4A22", "#241B0D", "#F3E3BE"),
    ("#2E4A63", "#111E29", "#D6E4EF"),
    ("#5A2E4A", "#24111E", "#F0D8E4"),
    ("#3F4A2A", "#171D10", "#E4EBCF"),
    ("#5C3A1F", "#24160C", "#F2DFC8"),
)


def _initial(name: str) -> str:
    """取角色头像首字；空名字兜底为问号。"""
    text = (name or "").strip()
    return text[0] if text else "？"


def _palette_for(name: str) -> tuple[str, str, str]:
    """按姓名哈希稳定取色（不用 Python 内置 hash，避免跨进程随机化）。"""
    digest = hashlib.md5((name or "").encode("utf-8")).hexdigest()
    return _AVATAR_PALETTE[int(digest[:8], 16) % len(_AVATAR_PALETTE)]


@lru_cache(maxsize=256)
def avatar_html(name: str, size: int = 64, extra_class: str = "") -> str:
    """返回首字头像的 HTML（用于卡片 / 侧栏）。

    lru_cache：每局会话页 rerun 会重复渲染同一批角色头像，缓存后避免重复
    MD5 取色 + 字符串拼接；函数纯、参数可哈希，缓存安全。
    """
    start, end, ink = _palette_for(name)
    cls = f"char-avatar {extra_class}".strip()
    return (
        f'<span class="{cls}" style="width:{size}px;height:{size}px;'
        f'font-size:{max(16, int(size * 0.42))}px;--av-a:{start};--av-b:{end};--av-ink:{ink};">'
        f"{html.escape(_initial(name))}</span>"
    )


@lru_cache(maxsize=256)
def avatar_data_uri(name: str) -> str:
    """生成 SVG data URI，供 st.chat_message(avatar=...) 使用。

    每条聊天消息每次 rerun 都会取头像；缓存后避免重复 base64 编码 SVG。
    """
    start, end, ink = _palette_for(name)
    initial = html.escape(_initial(name))
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="{start}"/><stop offset="1" stop-color="{end}"/>
</linearGradient></defs>
<circle cx="48" cy="48" r="46" fill="url(#g)" stroke="#C9A227" stroke-width="2"/>
<circle cx="48" cy="48" r="39" fill="none" stroke="rgba(255,230,190,0.18)" stroke-width="1"/>
<text x="48" y="52" text-anchor="middle" dominant-baseline="middle"
 font-family="STKaiti, KaiTi, STSong, serif" font-size="42" fill="{ink}">{initial}</text>
</svg>"""
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def character_card_html(name: str, profession: str, relation: str) -> str:
    """选角页的嫌疑人卡：头像 + 姓名 + 职业 + 与死者关系。"""
    prof = html.escape((profession or "").strip()) or "身份未明"
    rel = html.escape((relation or "").strip()) or "关系未明"
    return (
        '<div class="suspect-card">'
        '<div class="suspect-card-tag">PERSON OF INTEREST</div>'
        '<div class="suspect-card-head">'
        f"{avatar_html(name, 68)}"
        '<div class="suspect-id">'
        f'<div class="suspect-name">{html.escape(name)}</div>'
        f'<div class="suspect-prof">{prof}</div>'
        "</div></div>"
        f'<div class="suspect-rel"><b>关系</b>{rel}</div>'
        "</div>"
    )


def role_header_html(name: str, profession: str, relation: str) -> str:
    """侧栏顶部「你的角色」卡头。"""
    meta_parts = [html.escape((profession or "").strip()) or "身份未明"]
    if relation:
        meta_parts.append(f"与死者：{html.escape(relation.strip())}")
    return (
        '<div class="role-hero">'
        f"{avatar_html(name, 58)}"
        '<div class="role-hero-id">'
        f'<div class="role-hero-name">{html.escape(name)}</div>'
        f'<div class="role-hero-meta">{" · ".join(meta_parts)}</div>'
        "</div>"
        '<span class="role-hero-tag">YOU</span>'
        "</div>"
    )


def suspect_roster_html(suspects: list[dict], user_role: str) -> str:
    """侧栏嫌疑人总览：小头像列表 +「你」标记。"""
    rows = []
    for suspect in suspects:
        if not isinstance(suspect, dict):
            continue
        name = str(suspect.get("name", "") or "?")
        prof = html.escape(str(suspect.get("profession", "") or "（未提供职业）"))
        mine = name == user_role
        row_cls = "roster-row me" if mine else "roster-row"
        tag = '<span class="roster-tag">你</span>' if mine else ""
        rows.append(
            f'<div class="{row_cls}">{avatar_html(name, 30, "char-avatar-sm")}'
            f'<div class="roster-id"><div class="roster-name">{html.escape(name)}</div>'
            f'<div class="roster-prof">{prof}</div></div>{tag}</div>'
        )
    return '<div class="suspect-roster">' + "".join(rows) + "</div>"


_CHARACTER_CSS = """
<style>
/* ==================== 角色视觉层（首字头像 / 角色卡） ==================== */
.char-avatar {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  border-radius: 50%;
  overflow: hidden;
  background:
    radial-gradient(circle at 32% 26%, rgba(255, 255, 255, .16), transparent 42%),
    linear-gradient(145deg, var(--av-a), var(--av-b));
  border: 1px solid rgba(201, 162, 39, .48);
  box-shadow: 0 8px 20px rgba(0, 0, 0, .42), inset 0 0 0 3px rgba(0, 0, 0, .16);
  color: var(--av-ink);
  font-family: 'STKaiti', 'KaiTi', '楷体', 'STSong', serif;
  font-weight: 600;
  line-height: 1;
  letter-spacing: 0;
}
.char-avatar:after {
  content: "";
  position: absolute;
  inset: 4px;
  border: 1px solid rgba(255, 230, 190, .16);
  border-radius: 50%;
  pointer-events: none;
}
.char-avatar-sm { box-shadow: 0 4px 10px rgba(0, 0, 0, .35), inset 0 0 0 2px rgba(0, 0, 0, .14); }

/* 选角页：每张卡一个 keyed container（st-key-role_card_N） */
[class*="st-key-role_card_"] {
  min-height: clamp(214px, 12vw, 280px);
  padding: clamp(15px, 1vw, 24px) clamp(14px, .9vw, 22px) clamp(10px, .7vw, 16px) !important;
  background: linear-gradient(165deg, rgba(36, 29, 23, .96), rgba(20, 16, 13, .98)) !important;
  border: 1px solid #4a3925 !important;
  border-radius: clamp(14px, .8vw, 20px) !important;
  box-shadow: 0 14px 34px rgba(0, 0, 0, .30), inset 0 1px 0 rgba(255, 235, 200, .04);
  transition: border-color .25s ease, transform .25s ease, box-shadow .25s ease;
}
[class*="st-key-role_card_"]:hover {
  border-color: rgba(201, 162, 39, .78) !important;
  transform: translateY(-3px);
  box-shadow: 0 20px 42px rgba(0, 0, 0, .42), 0 0 24px rgba(201, 162, 39, .08);
}
[class*="st-key-role_card_"] [data-testid="stVerticalBlock"] { gap: 0 !important; }
[class*="st-key-role_card_"] [data-testid="stMarkdown"] { flex: 1 1 auto; }
[class*="st-key-role_card_"] .stButton button {
  width: 100%;
  min-height: clamp(38px, 2vw, 48px);
  margin-top: clamp(10px, .7vw, 16px);
  background: rgba(201, 162, 39, .055) !important;
  border-color: #5a4629 !important;
  color: #C9A227 !important;
  font-size: clamp(12px, .65vw, 16px) !important;
  letter-spacing: 3px !important;
}
[class*="st-key-role_card_"] .stButton button:hover {
  background: rgba(201, 162, 39, .14) !important;
  border-color: #C9A227 !important;
  color: #F5E6D8 !important;
}
.suspect-card { position: relative; display: flex; flex-direction: column; gap: clamp(12px, .8vw, 18px); }
.suspect-card-tag {
  position: absolute;
  top: 0;
  right: 0;
  font-family: 'Georgia', serif;
  font-size: clamp(7px, .35vw, 10px);
  letter-spacing: 2px;
  color: rgba(201, 162, 39, .30);
}
.suspect-card-head { display: flex; align-items: center; gap: clamp(13px, .8vw, 20px); padding-top: 4px; }
.suspect-id { min-width: 0; }
.suspect-name {
  font-family: 'STKaiti', 'KaiTi', '楷体', 'STSong', serif;
  font-size: clamp(21px, 1.1vw, 30px);
  line-height: 1.12;
  letter-spacing: 2px;
  color: #F0E2C8;
}
.suspect-prof {
  margin-top: 5px;
  font-size: clamp(11px, 0.62vw, 16px);
  line-height: 1.4;
  color: #B9A883;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.suspect-rel {
  min-height: 36px;
  padding-top: clamp(9px, .6vw, 14px);
  border-top: 1px dashed rgba(107, 79, 42, .35);
  font-size: clamp(10.5px, 0.58vw, 15px);
  line-height: 1.55;
  color: #9A8E75;
}
.suspect-rel b {
  margin-right: 6px;
  color: #C9A227;
  font-weight: 500;
  letter-spacing: 1px;
}

/* 侧栏：你的角色卡头 */
.role-hero {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 2px 0 10px;
  padding: 12px;
  background: linear-gradient(150deg, rgba(201, 162, 39, .07), rgba(20, 16, 13, .6));
  border: 1px solid rgba(201, 162, 39, .24);
  border-radius: 12px;
  position: relative;
}
.role-hero-id { min-width: 0; flex: 1; }
.role-hero-name {
  font-family: 'STKaiti', 'KaiTi', '楷体', 'STSong', serif;
  font-size: clamp(23px, 1.2vw, 32px);
  line-height: 1.1;
  letter-spacing: 2px;
  color: #F0E2C8;
}
.role-hero-meta {
  margin-top: 4px;
  font-size: clamp(10px, 0.55vw, 14px);
  line-height: 1.5;
  color: #B9A883;
}
.role-hero-tag {
  position: absolute;
  top: 8px;
  right: 9px;
  padding: 2px 6px;
  border: 1px solid rgba(201, 162, 39, .35);
  border-radius: 9px;
  color: rgba(201, 162, 39, .75);
  font-family: 'Georgia', serif;
  font-size: 7px;
  letter-spacing: 1px;
}

/* 侧栏：嫌疑人总览 */
.suspect-roster { display: flex; flex-direction: column; gap: 6px; }
.roster-row {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: rgba(255, 255, 255, .018);
}
.roster-row.me {
  border-color: rgba(201, 162, 39, .32);
  background: rgba(201, 162, 39, .055);
}
.roster-id { min-width: 0; flex: 1; }
.roster-name { font-size: clamp(12px, 0.65vw, 16px); color: #E8DCC8; line-height: 1.2; }
.roster-prof {
  margin-top: 1px;
  font-size: clamp(9.5px, 0.5vw, 13px);
  color: #9A8E75;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.roster-tag {
  flex: none;
  padding: 2px 6px;
  border: 1px solid rgba(201, 162, 39, .34);
  border-radius: 9px;
  color: #C9A227;
  font-size: 8px;
  letter-spacing: 1px;
}

/* 2K / 4K 全屏：头像随视口放大（内联尺寸用 !important 覆盖） */
@media (min-width: 1500px) {
  .suspect-card .char-avatar { width: clamp(82px, 3.4vw, 104px) !important; height: clamp(82px, 3.4vw, 104px) !important; font-size: clamp(34px, 1.45vw, 44px) !important; }
  .role-hero .char-avatar { width: clamp(66px, 2.8vw, 86px) !important; height: clamp(66px, 2.8vw, 86px) !important; font-size: clamp(28px, 1.2vw, 36px) !important; }
  .roster-row .char-avatar { width: clamp(32px, 1.3vw, 42px) !important; height: clamp(32px, 1.3vw, 42px) !important; font-size: clamp(14px, .55vw, 18px) !important; }
}
</style>
"""


def render_character_styles() -> None:
    """注入一次角色视觉 CSS；放在页面顶层即可，重复注入无副作用。"""
    st.markdown(_CHARACTER_CSS, unsafe_allow_html=True)

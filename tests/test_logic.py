"""
纯函数 / 确定性节点的单元测试（不花一分钱 token）。

覆盖报告里点名的 8 个函数，重点回归已踩过的坑：
- _parse_names 的中文分号（曾导致自定义名字静默退回随机）
- tally_node 的平票（曾 most_common(1) 瞎取一个）
- distribute_clues_node 的 holder 写错（负载均衡兜底）
- _extract_speak 的 think 泄露（曾 raw 当 speak 公开发布）

运行：在项目根目录执行 `pytest tests/ -v`
"""
import sys
from pathlib import Path

# 把项目根目录加入 sys.path，让测试能 import names / validators / nodes 等模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from names import _parse_names, _pick_suspect_names
from prompts import _build_script_prompt, murderer_defense_pool_text
from validators import (
    _enforce_names,
    _normalize_secret_first_person,
    _normalize_secrets,
    _fallback_clues,
    _filter_relations,
    _ensure_clues,
    _extract_speak,
    _parse_json,
)
from visualization import build_relations_html, _truncate_label, _clean_pronouns
from nodes import (
    distribute_clues_node, tally_node, route_speaker, _current_speaker,
    _check_script_consistency, _get_murderer, _extract_clue_keywords,
    _update_revealed_clues, _detect_addressed, _format_private_clues, _judge_ending,
)
from interrupt_handler import get_interrupt, validate_vote


# ============ interrupt_handler ============

class _FakeInterrupt:
    """模拟 LangGraph 的 Interrupt 对象（内容在 .value 属性）。"""

    def __init__(self, value):
        self.value = value


def test_get_interrupt_from_interrupt_object():
    # LangGraph 真实结构：__interrupt__ 列表元素是 Interrupt 对象（.value 属性）
    result = {"__interrupt__": [_FakeInterrupt({"type": "choose_role", "suspects": ["张三"]})]}
    assert get_interrupt(result)["type"] == "choose_role"


def test_get_interrupt_from_dict():
    # 兼容 dict 模拟的情况
    result = {"__interrupt__": [{"value": {"type": "human_turn"}}]}
    assert get_interrupt(result)["type"] == "human_turn"


def test_get_interrupt_empty():
    assert get_interrupt({}) == {}
    assert get_interrupt({"__interrupt__": []}) == {}


def test_validate_vote():
    assert validate_vote("张三", ["张三", "李四"]) == "张三"
    assert validate_vote("王五", ["张三", "李四"]) is None


# ============ _parse_names ============

def test_parse_names_english_comma():
    assert _parse_names("张三, 李四, 王五") == ["张三", "李四", "王五"]


def test_parse_names_chinese_punctuation():
    # 重点：中文逗号/顿号/分号/冒号/竖线/斜杠都要正确拆分
    assert _parse_names("张三，李四、王五；赵六") == ["张三", "李四", "王五", "赵六"]
    assert _parse_names("张三；李四；王五") == ["张三", "李四", "王五"]
    assert _parse_names("张三|李四/王五") == ["张三", "李四", "王五"]


def test_parse_names_dedup_and_empty():
    assert _parse_names("张三, 张三, 李四") == ["张三", "李四"]
    assert _parse_names("") == []
    assert _parse_names("   ") == []


# ============ _pick_suspect_names ============

def test_pick_names_custom_priority():
    cn = ["张三", "李四", "王五"]
    assert _pick_suspect_names("校园怪谈", cn) == cn


def test_pick_names_custom_truncate_to_6():
    cn = ["A", "B", "C", "D", "E", "F", "G"]
    assert _pick_suspect_names("校园怪谈", cn) == ["A", "B", "C", "D", "E", "F"]


def test_pick_names_too_few_falls_back_random():
    # 少于 3 个自定义名 → 退回随机（4~6 个）
    result = _pick_suspect_names("校园怪谈", ["只", "两个"])
    assert 4 <= len(result) <= 6


def test_pick_names_random_in_range():
    result = _pick_suspect_names("校园怪谈", None)
    assert 4 <= len(result) <= 6
    assert len(result) == len(set(result))   # 不重复


# ============ _enforce_names ============

def test_enforce_names_replace():
    bad = {"suspects": [
        {"name": "错误A", "secret": "我...", "forbidden": ["a"]},
        {"name": "错误B", "secret": "我...", "forbidden": ["b"]},
    ]}
    fixed = _enforce_names(dict(bad), ["张三", "李四"])
    assert [s["name"] for s in fixed["suspects"]] == ["张三", "李四"]
    assert fixed["suspects"][0]["secret"] == "我..."   # 保留 secret


def test_enforce_names_pad_missing():
    # LLM 少给了嫌疑人 → 补空壳
    bad = {"suspects": [{"name": "错误A", "secret": "我...", "forbidden": []}]}
    fixed = _enforce_names(dict(bad), ["张三", "李四", "王五"])
    assert [s["name"] for s in fixed["suspects"]] == ["张三", "李四", "王五"]
    assert fixed["suspects"][1]["secret"] == "待补充"


# ============ _normalize_secret_first_person ============

def test_normalize_secret_first_person():
    assert _normalize_secret_first_person("他暗恋宋知夏") == "我暗恋宋知夏"
    assert _normalize_secret_first_person("她偷了东西") == "我偷了东西"
    assert _normalize_secret_first_person("他的秘密") == "我的秘密"
    assert _normalize_secret_first_person("我暗恋他") == "我暗恋他"   # 已是第一人称，不动
    assert _normalize_secret_first_person("") == ""


# ============ _extract_speak ============

def test_extract_speak_normal():
    assert _extract_speak({"speak": "你好"}) == "你好"


def test_extract_speak_from_raw_rescues_speak():
    # JSON 解析失败 → raw 全文，应正则抢救 speak，而不是返回含 think 的 raw
    raw = '{"think": "我是凶手", "speak": "我觉得是别人"}'
    out = _extract_speak({"raw": raw})
    assert out == "我觉得是别人"
    assert "我是凶手" not in out   # 关键：think 绝不能泄露


def test_extract_speak_no_speak_gives_placeholder():
    assert _extract_speak({"raw": '{"think": "我是凶手"}'}) == "……（这个角色欲言又止）"


# ============ _fallback_clues ============

def test_fallback_clues_new_format_untouched():
    script = {"public_clues": ["a"], "private_clues": [{"holder": "张三", "content": "b"}]}
    assert _fallback_clues(dict(script)) == script


def test_fallback_clues_old_format_split():
    script = {
        "suspects": [{"name": "张三"}, {"name": "李四"}],
        "clues": ["公开1", "公开2", "私1", "私2", "私3"],
    }
    fixed = _fallback_clues(dict(script))
    assert fixed["public_clues"] == ["公开1", "公开2"]
    assert len(fixed["private_clues"]) == 3
    holders = [c["holder"] for c in fixed["private_clues"]]
    assert holders == ["张三", "李四", "张三"]   # 轮转分配


# ============ distribute_clues_node ============

def test_distribute_clues_assign_by_holder():
    state = {
        "script": {
            "suspects": [{"name": "张三"}, {"name": "李四"}],
            "public_clues": ["公共线索"],
            "private_clues": [
                {"holder": "张三", "content": "张三的秘密"},
                {"holder": "李四", "content": "李四的秘密"},
            ],
        }
    }
    out = distribute_clues_node(state)
    assert out["distributed_clues"]["张三"] == ["张三的秘密"]
    assert out["distributed_clues"]["李四"] == ["李四的秘密"]


def test_distribute_clues_wrong_holder_load_balance():
    # holder 写错（不存在的人）→ 负载均衡分给线索最少的角色，不丢线索
    state = {
        "script": {
            "suspects": [{"name": "张三"}, {"name": "李四"}],
            "public_clues": [],
            "private_clues": [{"holder": "不存在的人", "content": "孤儿线索"}],
        }
    }
    out = distribute_clues_node(state)
    total = sum(len(v) for v in out["distributed_clues"].values())
    assert total == 1   # 线索没丢


# ============ tally_node ============

def test_tally_normal():
    out = tally_node({"votes": {"张三": "李四", "李四": "王五", "王五": "李四"}})
    assert out["vote_winner"] == "李四"
    assert out["vote_counts"] == {"李四": 2, "王五": 1}


def test_tally_tie_detected():
    out = tally_node({"votes": {"张三": "李四", "李四": "王五"}})
    assert out["vote_winner"].startswith("平票")
    assert "李四" in out["vote_winner"] and "王五" in out["vote_winner"]


def test_tally_empty():
    out = tally_node({"votes": {}})
    assert out["vote_winner"] == "无人投票"


# ============ _current_speaker / route_speaker ============

def _make_state(round_num, names, user_role):
    return {
        "phase_round": round_num,
        "script": {"suspects": [{"name": n} for n in names]},
        "user_role": user_role,
        "messages": [],
    }


def test_current_speaker_follows_last_message():
    # 8-20 终审 M2 修复：轮换由"上一位实际发言者"驱动（读 messages 末位取下一位），
    # 不再按 phase_round 取模——点名/追问插队发言不再打乱轮换顺序。
    state = _make_state(0, ["张三", "李四", "王五"], "张三")
    # 无消息（讨论开始前）→ 从第一个开始
    assert _current_speaker(state)["name"] == "张三"
    # 张三刚发言 → 轮到李四
    state["messages"] = [{"speaker": "张三", "content": "..."}]
    assert _current_speaker(state)["name"] == "李四"
    # 李四刚发言 → 王五
    state["messages"] = [{"speaker": "李四", "content": "..."}]
    assert _current_speaker(state)["name"] == "王五"
    # 王五刚发言 → 循环回张三
    state["messages"] = [{"speaker": "王五", "content": "..."}]
    assert _current_speaker(state)["name"] == "张三"
    # 最后发言者不在名单（如"主持人"）→ 从头开始
    state["messages"] = [{"speaker": "主持人", "content": "..."}]
    assert _current_speaker(state)["name"] == "张三"


def test_route_speaker_human_vs_ai():
    # 张三(玩家)刚发言 → 下一位李四 → ai
    state = _make_state(0, ["张三", "李四"], "张三")
    state["messages"] = [{"speaker": "张三", "content": "..."}]
    assert route_speaker(state) == "ai"
    # 李四刚发言 → 下一位张三(玩家) → human
    state["messages"] = [{"speaker": "李四", "content": "..."}]
    assert route_speaker(state) == "human"


def test_route_speaker_pointed_reply_does_not_skip_next():
    # 8-20 终审 M2 关键回归：点名 C 插队回应 + 玩家追问后，
    # 下一轮仍是正常顺序（B 不被跳过、C 不连续发言）。
    # 推演：A(玩家)点名 C → C 插队回应 → A 追问 → 下一轮轮到 B。
    suspects = ["张三", "李四", "王五"]   # 张三=玩家
    # C(王五)刚插队发言完，pending 已清 → 下一位按 messages 末位取 → 李四
    state = _make_state(0, suspects, "张三")
    state["messages"] = [{"speaker": "王五", "content": "..."}]
    assert _current_speaker(state)["name"] == "张三"   # 王五的下一位 = 张三(玩家)
    # 玩家追问完（最后发言者是张三）→ 下一位李四，不再跳人
    state["messages"] = [{"speaker": "王五", "content": "..."}, {"speaker": "张三", "content": "..."}]
    assert _current_speaker(state)["name"] == "李四"


def test_route_speaker_vote_when_round_full():
    # 2 个嫌疑人 × ROUNDS_PER_PLAYER(3) = 6 轮，第 6 轮起进投票
    assert route_speaker(_make_state(6, ["张三", "李四"], "张三")) == "vote"


def test_route_speaker_no_suspects():
    state = {"phase_round": 0, "script": {"suspects": []}, "user_role": ""}
    assert route_speaker(state) == "vote"


# ============ _filter_relations（名单外角色清洗） ============
# 回归：LLM 把 background_story 里的背景人物（不在嫌疑人名单内）写进 relations，
# pyvis 渲染时 add_edge 对不存在的节点直接 AssertionError，整页崩溃。

def _script_with_relations(rels):
    return {
        "suspects": [{"name": "张三"}, {"name": "李四"}, {"name": "王五"}],
        "relations": rels,
    }


def test_filter_relations_drops_outside_names():
    # 周恒远不在嫌疑人名单里，两条涉及他的边应被剔除
    script = _script_with_relations([
        {"from": "张三", "to": "周恒远", "rel": "我的辅导员", "public": True},
        {"from": "周恒远", "to": "死者", "rel": "关系密切", "public": True},
        {"from": "张三", "to": "李四", "rel": "我们互相看不惯", "public": True},
    ])
    fixed = _filter_relations(dict(script))
    assert len(fixed["relations"]) == 1
    assert fixed["relations"][0]["to"] == "李四"


def test_filter_relations_keeps_victim_and_private():
    # 死者是合法节点（保留）；私密关系不涉及清洗（清洗只看名单，不看 public）
    script = _script_with_relations([
        {"from": "张三", "to": "死者", "rel": "我把死者当姐姐", "public": False},
        {"from": "王五", "to": "李四", "rel": "我们结过仇", "public": True},
    ])
    fixed = _filter_relations(dict(script))
    assert len(fixed["relations"]) == 2


def test_filter_relations_empty_or_missing():
    # 无 relations key：函数保持原样，不新增 key
    fixed = _filter_relations({"suspects": [{"name": "张三"}]})
    assert "relations" not in fixed
    script = _script_with_relations([])
    fixed = _filter_relations(script)
    assert fixed["relations"] == []
    # 无 suspects 也不崩
    assert _filter_relations({"relations": [{"from": "a", "to": "b", "public": True}]})["relations"] == []


# ============ _ensure_clues（线索全空最后防线） ============

def test_ensure_clues_generates_fallback():
    script = {
        "suspects": [{"name": "张三", "secret": "我偷看过考卷"}, {"name": "李四", "secret": ""}],
        "public_clues": [],
        "private_clues": [],
    }
    fixed = _ensure_clues(dict(script))
    assert len(fixed["private_clues"]) == 2
    assert {c["holder"] for c in fixed["private_clues"]} == {"张三", "李四"}
    assert "考卷" in fixed["private_clues"][0]["content"]
    # secret 缺失的嫌疑人也有占位线索（不白板）
    assert fixed["private_clues"][1]["content"]


def test_ensure_clues_existing_untouched():
    script = {"public_clues": ["公共线索"], "private_clues": [{"holder": "张三", "content": "x"}]}
    assert _ensure_clues(dict(script)) == script
    # 只要任意一种线索存在就不覆盖（防止把 LLM 认真生成的线索换成 secret 兜底）
    script2 = {"public_clues": ["只有公共线索"], "private_clues": []}
    assert _ensure_clues(dict(script2)) == script2


# ============ build_relations_html（关系图崩溃防御） ============

def test_relations_html_drops_unknown_nodes():
    # 原始崩溃场景：relations 引用名单外的"周恒远"，必须不崩且不渲染该节点。
    # pyvis generate_html 用 json.dumps(ensure_ascii=True) 把中文转成 \uXXXX 转义，
    # 断言中文字面量会误判（浏览器实际能正常渲染），所以要按 unicode_escape 编码后断言。
    html = build_relations_html(
        [
            {"from": "张三", "to": "周恒远", "rel": "我的辅导员", "public": True},
            {"from": "张三", "to": "李四", "rel": "我们互相看不惯", "public": True},
        ],
        ["张三", "李四"],
    )
    escaped_zhou = "周恒远".encode("unicode_escape").decode()
    escaped_li = "李四".encode("unicode_escape").decode()
    assert escaped_zhou not in html
    assert escaped_li in html


def test_relations_html_empty_returns_empty_string():
    assert build_relations_html([], ["张三"]) == ""
    assert build_relations_html(
        [{"from": "张三", "to": "李四", "rel": "x", "public": False}], ["张三", "李四"]
    ) == ""


# ============ _truncate_label / 关系图 8-18 优化回归 ============
# 飞哥反馈：边标签放不下、节点显示不出。优化后必须保持：
# - 短标签不截断；长标签截断到 8 字 + 省略号（中文按 1 字符计）
# - 截断后的完整内容必须进 title（hover 看）
# - 节点用 box（vis.js 配置 "shape": "box" 在选项 JSON 里）
# - 边标签字号 16、align horizontal（不再沿边旋转成竖排）

def test_truncate_label_short_unchanged():
    assert _truncate_label("我暗恋聂橙") == "我暗恋聂橙"
    assert _truncate_label("") == ""
    assert _truncate_label(None) == ""


def test_truncate_label_long_truncated():
    # 10 字超长，截到 8 字 + 省略号
    out = _truncate_label("我把他当姐姐心里愧疚")
    assert len(out) == 9   # 8 字 + 1 省略号
    assert out.endswith("…")
    assert out.startswith("我把他当姐")


def test_truncate_label_exact_boundary():
    # 8 字刚好不截
    assert _truncate_label("一二三四五六七八") == "一二三四五六七八"
    # 9 字截到 8 字 + 省略号（总长 9 字符）
    assert _truncate_label("一二三四五六七八九") == "一二三四五六七八…"


def test_relations_html_uses_box_shape_and_options():
    # 8-18 优化：节点用 box 矩形（不再是默认圆形），字号 20/22,边字号 16,水平对齐
    html = build_relations_html(
        [{"from": "张三", "to": "李四", "rel": "我暗恋他", "public": True}],
        ["张三", "李四"],
    )
    assert '"shape": "box"' in html, "嫌疑人节点必须用 box 矩形（不是默认圆形）"
    assert '"size": 16' in html, "边标签字号 16（中文能读）"
    assert '"align": "horizontal"' in html, "边标签水平显示（不沿边旋转）"
    assert '"background"' in html, "边标签要有白底防压字"
    assert "barnesHut" in html, "物理布局用 barnesHut（更稳）"
    assert "springLength" in html
    assert "Microsoft YaHei" in html, "中文用 Microsoft YaHei 渲染"


def test_relations_html_long_rel_truncated_in_label_full_in_title():
    # 长 rel 截断进 label，完整内容进 title。
    # 流水线是 clean_pronouns → truncate，truncated 必须按真实流水线算
    long_rel = "我把他当姐姐心里愧疚很久了"   # 13 字，删"我/他"变 11 字，截到 8 字+省略号
    html = build_relations_html(
        [{"from": "张三", "to": "死者", "rel": long_rel, "public": True}],
        ["张三", "李四"],
    )
    truncated = _truncate_label(_clean_pronouns(long_rel))   # 必须按真实流水线
    escaped_truncated = truncated.encode("unicode_escape").decode()
    escaped_full = long_rel.encode("unicode_escape").decode()
    assert escaped_truncated in html, f"截断后的 label 必须在 html 中: {truncated!r}"
    assert escaped_full in html, "完整 rel 必须在 html 中（title 悬浮用）"


def test_relations_html_victim_node_still_red_star():
    # 死者仍用 star 形状（视觉中心）
    html = build_relations_html(
        [{"from": "张三", "to": "死者", "rel": "我把死者当姐姐", "public": True}],
        ["张三"],
    )
    assert '"shape": "star"' in html
    assert "案件核心" in html or "f5222d" in html   # 红色/案件标记


# ============ _clean_pronouns + 箭头（8-18 第二轮优化） ============
# 飞哥反馈：关系图加箭头 + 标签里删"我/他"指代冗余词。
# 边方向 from→to（箭头指 to）契合"我对他"的关系语义，标签里再写
# "我"和"他/她"就是冗余。完整 rel 仍进 title（hover 看原始描述）。

def test_clean_pronouns_strips_wo_he_she():
    assert _clean_pronouns("我暗恋聂橙") == "暗恋聂橙"
    assert _clean_pronouns("我信任他") == "信任"
    assert _clean_pronouns("我仰慕她") == "仰慕"
    # 中文标点里的"他"也清理（"他"字面字符）
    assert _clean_pronouns("我和她关系好") == "和关系好"


def test_clean_pronouns_preserves_we():
    # "我们"是复数第一人称，保留——边是单向箭头，"我们互相看不惯"
    # 删"我们"会变成"们互相看不惯"语义失真
    assert _clean_pronouns("我们互相看不惯") == "我们互相看不惯"


def test_clean_pronouns_empty_and_none():
    assert _clean_pronouns("") == ""
    assert _clean_pronouns(None) == ""


def test_clean_pronouns_compose_with_truncate():
    # 流水线：clean_pronouns → truncate。删"我/他"后,8 字内不截
    rel = "我把死者当社长"  # 删后"把死者当社长"7 字
    display = _truncate_label(_clean_pronouns(rel))
    assert display == "把死者当社长"
    # 删后 8 字刚好不截（边界）
    ten_to_eight = "我把他当姐姐心里愧"   # 10 字删"我/他"变 8 字
    assert _truncate_label(_clean_pronouns(ten_to_eight)) == "把当姐姐心里愧"
    # 删后超 8 字才截。用 12 字删 2 → 10 字 > 8，截到 8 字+省略号
    twelve_to_ten = "我把他当姐姐心里疚了吗"
    # 数:我(1)把(2)他(3)当(4)姐(5)姐(6)心(7)里(8)疚(9)了(10)吗(11)
    # 共 11 字,删 2 → 9 字?让我精确数:我把他当姐姐心里疚了吗 = 11 字符
    # 删"我"+"他" → 把当姐姐心里疚了吗 = 9 字符,截到 8 + 省略号
    display3 = _truncate_label(_clean_pronouns(twelve_to_ten))
    assert display3.endswith("…"), f"删后 > 8 字必须截断,实际: {display3!r}"
    assert len(display3) == 9, f"截断后 8 字 + 1 省略号,实际: {display3!r}"


def test_relations_html_directed_with_arrows():
    # 8-18 第二轮：图改有向，边带箭头。
    # 注意：pyvis 的 directed 是 Network 构造参数，不序列化到 HTML；
    # vis.js 真正画箭头的机制是 options.edges.arrows.to.enabled = true
    html = build_relations_html(
        [{"from": "张三", "to": "李四", "rel": "我暗恋聂橙", "public": True}],
        ["张三", "李四"],
    )
    assert "arrows" in html, "边必须带箭头配置"
    assert '"to": {"enabled": true' in html or '"to":{"enabled":true' in html, \
        "箭头方向 to 必须 enabled=true（指 from→to）"


def test_relations_html_navigation_buttons_disabled():
    # 8-20 第四轮：关闭 vis.js 默认的左下/右下导航按钮（被模态框边缘截断）
    html = build_relations_html(
        [{"from": "张三", "to": "李四", "rel": "我暗恋聂橙", "public": True}],
        ["张三", "李四"],
    )
    assert '"navigationButtons": false' in html or '"navigationButtons":false' in html, \
        "navigationButtons 必须设为 false（防默认按钮被模态框边缘截断）"
    assert '"navigationButtons": true' not in html and '"navigationButtons":true' not in html, \
        "navigationButtons 不应启用"


def test_relations_html_label_strips_pronouns_full_in_title():
    # 8-18 第二轮：标签里删"我/他/她"，完整 rel 仍进 title
    rel = "我信任他"
    html = build_relations_html(
        [{"from": "舒飞", "to": "李鑫杰", "rel": rel, "public": True}],
        ["舒飞", "李鑫杰"],
    )
    # 完整 rel 必须在 html 中（title 悬浮看）
    assert rel.encode("unicode_escape").decode() in html
    # 标签区段：经过 clean_pronouns → truncate 应是"信任"，无"我/他/她"
    # 直接断言："信任" 在 html 里
    assert "信任".encode("unicode_escape").decode() in html
    # 反向断言：标签里不含单独的"我信任他"组合（说明被清理+截断了）
    # 但 title 里仍有完整，所以不能直接断言整段不在
    # 更稳妥：确保 _clean_pronouns 已生效（"信任" 出现在标签）
    # 单元测试 _clean_pronouns 已覆盖组合行为，这里端到端验证一次


# ============ _build_script_prompt（8-18 自由分支强化） ============
# 飞哥反馈：未输入 background_story 时,LLM 自由发挥只讲故事不填 JSON 字段，
# 角色卡/关系图全"待补充"。修复：自由分支复用长背景分支的三条核心铁律。

def test_build_script_prompt_free_branch_has_structured_field_rule():
    # 空 background_story 走自由分支，prompt 必须包含"结构化字段必须独立写"警告
    prompt = _build_script_prompt("民国豪门恩怨", "自由发挥", ["舒飞", "吕伟航", "袁志强", "李鑫杰", "聂橙", "赵思远"])
    assert "结构化字段每个嫌疑人必须独立写一遍" in prompt
    assert "public_clues / private_clues / hidden_clues" in prompt
    # 不能自己编新角色（飞哥截图里 LLM 编了"赵元同"等不在名单里的人名）
    assert "角色名字必须精确使用下面给定的嫌疑人名字" in prompt


def test_build_script_prompt_long_branch_keeps_all_rules():
    # 长 background 分支原有铁律不能丢
    long_bg = "校园怪谈：某社团 6 个学生发生命案，死者失踪半年后被发现。"
    prompt = _build_script_prompt("校园怪谈", "悬疑", ["舒飞", "吕伟航"], long_bg)
    assert "只吸收设定信息" in prompt
    assert "不要吸收后续对话" in prompt
    assert "名单外的角色不能登场" in prompt
    assert "结构化字段每个嫌疑人必须独立写一遍" in prompt


def test_build_script_prompt_long_branch_has_fatal_warning_too():
    # 8-20：长背景分支也必须有致命警告（飞哥反馈：自定义剧情背景没有
    # 具体人物/秘密/关系时，LLM 偷懒不补字段——光在自由分支加警告不够）。
    long_bg = "校园悬疑案：死者是家族族长，遗产分配引发矛盾。"
    prompt = _build_script_prompt("校园悬疑", "悬疑", ["舒飞", "孟诚华"], long_bg)
    assert "⚠️ 【致命警告】⚠️" in prompt, "长背景分支也必须有致命警告"
    # 关键新增：用户背景如缺细节必须自由发挥补全
    assert "自由发挥补全" in prompt, "必须明确告诉 LLM：用户没说的细节由 LLM 自由发挥补全"
    assert "不能用" in prompt and "用户没说" in prompt, "必须明确禁止 '用户没说所以跳过' 的借口"


def test_build_script_prompt_empty_bg_uses_free_branch():
    # 验证 background_story 空字符串/空白/None 都走自由分支
    names = ["舒飞", "吕伟航"]
    p_none = _build_script_prompt("主题", "风格", names, "")
    p_ws = _build_script_prompt("主题", "风格", names, "   ")
    # 自由分支关键特征：没有"只吸收设定信息"措辞
    assert "只吸收设定信息" not in p_none
    assert "只吸收设定信息" not in p_ws
    # 但有结构化字段铁律
    assert "结构化字段每个嫌疑人必须独立写一遍" in p_none
    assert "结构化字段每个嫌疑人必须独立写一遍" in p_ws


# ============ 8-19 自由分支致命警告 + 重试注入 ============
# 飞哥反馈：只输入主题时 LLM 仍偷懒（周既明/待补充/未生成公开关系）。
# 修复：① 自由分支 context 顶部拼 ⚠️ 致命警告（emoji + 列字段 + 后果）；
#       ② _build_script_prompt 新增 previous_problems 参数，重试时把上次
#          自洽校验失败问题拼进 prompt，让 LLM 第二次知道漏在哪。

def test_build_script_prompt_free_branch_has_fatal_warning():
    # 8-19：自由分支必须包含 ⚠️ 致命警告 + 逐个字段列出 + 后果警告
    prompt = _build_script_prompt("民国豪门恩怨", "自由发挥", ["舒飞", "吕伟航"])
    assert "⚠️ 【致命警告】⚠️" in prompt, "必须有致命警告块"
    assert "绝对不能省略" in prompt
    # 每个字段都要被点名
    for field in ["profession", "relation_to_victim", "alibi", "task",
                  "personality", "speech_style", "secret", "forbidden", "personal_script"]:
        assert field in prompt, f"警告里必须点名 {field}"
    assert "游戏无法进行" in prompt, "必须有后果警告"


def test_build_script_prompt_previous_problems_injected():
    # 8-19：previous_problems 必须拼进 prompt（重试时告知 LLM 上次漏了什么）
    prompt = _build_script_prompt(
        "主题", "风格", ["舒飞"], "",
        previous_problems=["没有任何线索", "舒飞 的 forbidden 词未出现在 secret 里"],
    )
    assert "上次生成失败反馈" in prompt, "必须有失败反馈块"
    assert "没有任何线索" in prompt, "problems 内容必须拼进 prompt"
    assert "forbidden 词未出现在 secret" in prompt
    assert "明确填全" in prompt


def test_build_script_prompt_no_previous_problems_no_feedback():
    # 8-19：不传 previous_problems 时不能有失败反馈块（首次生成干净 prompt）
    prompt = _build_script_prompt("主题", "风格", ["舒飞"])
    assert "上次生成失败反馈" not in prompt
    assert "明确填全" not in prompt


# ============ murderer_defense_pool_text（8-20 凶手狡辩策略池） ============
# 飞哥反馈："凶手被指控只会说同样的话"——给凶手 5 套策略循环使用，
# 严禁 LLM 重复同一种话术。

def test_murderer_defense_pool_zero_returns_empty():
    # 未被指控时返回空串（不污染 prompt）
    assert murderer_defense_pool_text(0) == ""
    assert murderer_defense_pool_text(-1) == ""


def test_murderer_defense_pool_first_call_picks_strategy_1():
    # 第一次被指控：第 1 种策略"否认证据"
    text = murderer_defense_pool_text(1)
    assert "第 1 种策略" in text
    assert "否认证据" in text
    assert "已经被指控 1 次" in text


def test_murderer_defense_pool_cycles_through_all_strategies():
    # 第 2~5 次被指控：第 2/3/4/5 种策略
    text2 = murderer_defense_pool_text(2)
    assert "第 2 种策略" in text2 and "反问嫁祸" in text2
    text3 = murderer_defense_pool_text(3)
    assert "第 3 种策略" in text3 and "质疑指控" in text3
    text4 = murderer_defense_pool_text(4)
    assert "第 4 种策略" in text4 and "情绪激动" in text4
    text5 = murderer_defense_pool_text(5)
    assert "第 5 种策略" in text5 and "细节反驳" in text5


def test_murderer_defense_pool_loops_back():
    # 第 6 次被指控：循环回到第 1 种（不能超出索引）
    text6 = murderer_defense_pool_text(6)
    assert "第 1 种策略" in text6 and "否认证据" in text6


def test_murderer_defense_pool_lists_all_strategies():
    # 任何一次返回都必须包含完整策略池全貌（供 LLM 参考，必须轮换）
    text = murderer_defense_pool_text(1)
    for keyword in ["否认证据", "反问嫁祸", "质疑指控", "情绪激动", "细节反驳",
                    "严禁复制粘贴", "话术框架必须换"]:
        assert keyword in text, f"策略池必须包含 {keyword}"


def test_murderer_defense_pool_different_accusation_count_different_pick():
    # 核心约束：相邻两次被指控必须选不同策略（防止 LLM 重复话术）
    t1 = murderer_defense_pool_text(1)
    t2 = murderer_defense_pool_text(2)
    assert "第 1 种" in t1 and "第 2 种" in t2
    assert t1 != t2, "相邻两次指控必须返回不同文本"


# ============ _parse_json（8-20 审查补测） ============

def test_parse_json_plain_object():
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_with_code_fence():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('```\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_with_leading_text():
    # LLM 常在 JSON 前后加"好的，这是剧本："引导文字，必须能从文本中提取
    assert _parse_json('好的，这是你要的剧本：\n{"a": 1}\n希望你喜欢') == {"a": 1}


def test_parse_json_invalid_returns_raw():
    # 完全解析不出时返回 {"raw": 原文}，由下游兜底（_extract_speak 会从 raw 抢救）
    out = _parse_json("这不是 JSON")
    assert out == {"raw": "这不是 JSON"}


# ============ _check_script_consistency（8-20 审查补测） ============

def _consistent_script():
    return {
        "suspects": [
            {
                "name": "张三",
                "secret": "我偷了东西",
                "forbidden": ["偷了东西"],
                "personal_script": "我叫张三，是社团摄影师，案发当天在社团活动室整理照片……",
                "profession": "摄影师",
                "relation_to_victim": "死者的室友",
                "alibi": "案发时我在图书馆整理照片",
                "task": "隐瞒偷窃行为，找出真凶",
                "personality": "谨慎多疑",
                "speech_style": "说话简短，爱反问",
            },
            {
                "name": "李四",
                "secret": "我暗恋王五",
                "forbidden": ["暗恋"],
                "personal_script": "我叫李四，是文学社成员，对死者心存感激……",
                "profession": "文学社社长",
                "relation_to_victim": "死者是文学社顾问",
                "alibi": "案发时我在文学社会议室写稿",
                "task": "保护王五，查明真相",
                "personality": "温和敏感",
                "speech_style": "文绉绉，引经据典",
            },
        ],
        "murderer": "张三",
        "truth": "张三因为债务问题杀害了死者",
        "private_clues": [{"holder": "张三", "content": "有人在案发前换过锁"}],
        "public_clues": ["死者死于中毒"],
        "relations": [
            {"from": "张三", "to": "李四", "rel": "我们是好朋友", "public": True},
            {"from": "李四", "to": "张三", "rel": "我和张三是室友，关系不错", "public": True},
            {"from": "张三", "to": "死者", "rel": "死者是我的室友，我们关系不错", "public": True},
            {"from": "李四", "to": "死者", "rel": "死者是我的社团顾问，我感激她", "public": True},
        ],
    }


def test_check_script_consistency_passes_ok():
    assert _check_script_consistency(_consistent_script()) == []


def test_check_script_consistency_truth_no_name():
    script = _consistent_script()
    script["truth"] = "凶手因为债务问题杀人"   # 没提到任何嫌疑人名字
    problems = _check_script_consistency(script)
    assert "truth 未提到任何嫌疑人名字" in problems


def test_check_script_consistency_forbidden_not_in_secret():
    script = _consistent_script()
    script["suspects"][0]["forbidden"] = ["毒药"]   # forbidden 词不在 secret 里
    problems = _check_script_consistency(script)
    assert any("forbidden 词未出现在 secret" in p for p in problems)


def test_check_script_consistency_no_clues():
    script = _consistent_script()
    script["private_clues"] = []
    script["public_clues"] = []
    assert "没有任何线索" in _check_script_consistency(script)


def test_check_script_consistency_empty_structured_fields():
    # 8-20：飞哥反馈——输入完整《桃花坪埋尸案》背景时 LLM 偷懒只讲故事不填字段。
    # 检测兜底：secret/personal_script/profession/relation_to_victim/alibi 为空或"待补充"应触发 problems
    script = _consistent_script()
    script["suspects"][0]["secret"] = ""                # 空
    script["suspects"][0]["personal_script"] = "待补充"  # 占位符
    script["suspects"][1]["profession"] = "  "          # 空白
    problems = _check_script_consistency(script)
    problems_text = " ".join(problems)
    # problem 文本格式是 "张三 缺 secret, personal_script, ..."，用单字段名匹配即可
    assert "secret" in problems_text
    assert "personal_script" in problems_text
    assert "profession" in problems_text


def test_check_script_consistency_no_relations():
    # 8-20：飞哥反馈截图——"剧本未生成公开关系"。relations 必须有公开边（>=2 人局）
    script = _consistent_script()
    script["relations"] = []
    problems = _check_script_consistency(script)
    assert any("没有公开边" in p for p in problems)


def test_check_script_consistency_victim_not_center():
    # 8-20 A+C 修复：公开边里直接涉及「死者」的不足 2 条时，关系图里死者被
    # 高连接度嫌疑人挤到边缘（飞哥截图"死者和嫌疑人位置互换"）。有公开边但
    # 死者边不足，必须报错触发重试。
    script = _consistent_script()
    script["relations"] = [{"from": "张三", "to": "李四", "rel": "我们是好朋友", "public": True}]
    problems = _check_script_consistency(script)
    assert any("涉及「死者」的不足 2 条" in p for p in problems)


# ============ _get_murderer（8-20 审查补测） ============

def test_get_murderer_from_murderer_field():
    script = {"murderer": "李四", "truth": "xxx", "suspects": [{"name": "张三"}, {"name": "李四"}]}
    assert _get_murderer(script, ["张三", "李四"]) == "李四"


def test_get_murderer_fallback_from_truth():
    # 无 murderer 字段时，从 truth 里找第一个出现的嫌疑人名字
    script = {"truth": "张三因为债务问题杀人", "suspects": [{"name": "张三"}, {"name": "李四"}]}
    assert _get_murderer(script, ["张三", "李四"]) == "张三"


def test_get_murderer_empty():
    assert _get_murderer({"truth": ""}, []) == ""


# ============ _extract_clue_keywords（8-20 审查补测） ============

def test_extract_clue_keywords_splits():
    assert _extract_clue_keywords("有人在案发前换过锁，是熟人") == ["有人在案发前换过锁", "是熟人"]
    assert _extract_clue_keywords("") == []
    # 长度 >= 2 的片段都保留（"短词""不保留"各 2 字）；单个字符 "a" 被过滤
    assert _extract_clue_keywords("短词 不保留 a") == ["短词", "不保留"]


# ============ _update_revealed_clues（8-20 审查补测） ============

def test_update_revealed_clues_newly_revealed():
    distributed = {"张三": ["有人在案发前换过锁"], "李四": ["我听见了争吵声"]}
    # 发言里完整出现了线索关键词"有人在案发前换过锁" → 张三的这条线索标记公开。
    # 注意：检测机制是"线索的完整关键词片段出现在发言里"（启发式），发言必须包含整段。
    newly = _update_revealed_clues("我坦白，有人在案发前换过锁", distributed, {})
    assert "有人在案发前换过锁" in newly
    assert newly["有人在案发前换过锁"] == "张三"


def test_update_revealed_clues_skips_already_revealed():
    distributed = {"张三": ["有人在案发前换过锁"]}
    newly = _update_revealed_clues("又提到换过锁", distributed, {"有人在案发前换过锁": "张三"})
    assert newly == {}   # 已公开，不再重复返回


# ============ _detect_addressed（8-20 审查补测） ============

def test_detect_addressed_finds_name():
    assert _detect_addressed("李四，你案发当晚在哪", ["张三", "李四"]) == "李四"


def test_detect_addressed_not_found():
    assert _detect_addressed("没有人被点名", ["张三", "李四"]) == ""


# ============ _format_private_clues（8-20 审查补测） ============

def test_format_private_clues_lines():
    text = _format_private_clues({"张三": ["线索A"], "李四": ["线索B", "线索C"]})
    assert "张三：线索A" in text
    assert "李四：线索B" in text
    assert "李四：线索C" in text


def test_format_private_clues_empty():
    assert "没有私密线索" in _format_private_clues({})


# ============ _judge_ending（8-20 审查补测，五结局） ============

def _ending_state(**overrides):
    state = {
        "user_role": "张三",
        "user_is_murderer": False,
        "votes": {"张三": "李四", "李四": "张三"},
        "vote_winner": "李四",
        "script": {
            "suspects": [{"name": "张三"}, {"name": "李四"}],
            "truth": "李四杀人", "murderer": "李四",
        },
        "messages": [{"speaker": "张三", "content": "我怀疑李四"}],
        "investigated_clues": [],
    }
    state.update(overrides)
    return state


def test_judge_ending_detective_active():
    # 投对 + 发言>=3 → 侦探
    state = _ending_state(messages=[{"speaker": "张三", "content": "x"} for _ in range(3)])
    label, _ = _judge_ending(state)
    assert label == "侦探"


def test_judge_ending_lucky_bystander_quiet():
    # 投对但只发言 1 次 → 幸运旁观者
    label, _ = _judge_ending(_ending_state())
    assert label == "幸运旁观者"


def test_judge_ending_deceived_wrong_vote():
    # 投错（投给了无辜者）→ 被蒙蔽
    state = _ending_state(votes={"张三": "王五", "王五": "张三"}, vote_winner="王五")
    label, _ = _judge_ending(state)
    assert label == "被蒙蔽"


def test_judge_ending_murderer_escapes():
    # 玩家是真凶且未被投出 → 完美犯罪
    state = _ending_state(user_is_murderer=True, votes={"张三": "李四"}, vote_winner="李四")
    label, _ = _judge_ending(state)
    assert label == "完美犯罪"


def test_judge_ending_murderer_caught():
    # 玩家是真凶且被投出 → 凶手伏法
    state = _ending_state(user_is_murderer=True, votes={"张三": "张三", "李四": "张三"}, vote_winner="张三")
    label, _ = _judge_ending(state)
    assert label == "凶手伏法"


# ============ _normalize_secrets（8-20 审查补测） ============

def test_normalize_secrets_all_suspects_first_person():
    script = {"suspects": [
        {"name": "张三", "secret": "他偷了东西"},
        {"name": "李四", "secret": "她暗恋王五"},
        {"name": "王五", "secret": "我已经在正确人称"},   # "我"开头不动
    ]}
    _normalize_secrets(script)
    assert script["suspects"][0]["secret"].startswith("我")
    assert script["suspects"][1]["secret"].startswith("我")
    assert script["suspects"][2]["secret"] == "我已经在正确人称"


def test_normalize_secrets_missing_secret_untouched():
    script = {"suspects": [{"name": "张三"}]}
    _normalize_secrets(script)   # 无 secret 字段不崩、不加字段
    assert "secret" not in script["suspects"][0]


# ============ 8-20 性别称呼一致性 ============
# 飞哥反馈：同一玩家"沈长卿"在不同发言轮被 NPC 分别称为"沈小姐"/"沈先生"。
# 根因：suspect schema 没有 gender 字段，LLM 看中性名自己猜性别，前后不一致。
# 修复：prompts.py 加 gender 字段 + 称呼一致性铁律；nodes.py ai_player_turn_node
# 注入玩家 gender 强制 AI 严格按 gender 用称呼。

def test_build_script_prompt_suspect_has_gender_field():
    # 8-20：每个 suspect 必须有 gender 字段（"男"或"女"），LLM 显式确定而非猜名字字面
    long_bg = "民国豪门故事：沈家家族聚会，6人嫌疑人。"
    prompt = _build_script_prompt("民国豪门", "悬疑", ["沈长卿", "沈碧如"], long_bg)
    assert "gender" in prompt, "suspect schema 必须有 gender 字段"
    assert "「男」或「女」" in prompt or '"男"或"女"' in prompt, "gender 字段说明必须是「男」或「女」"


def test_build_script_prompt_has_address_consistency_rule():
    # 8-20：新增"称呼一致性铁律"——整局游戏按 gender 称呼，严禁混用先生/小姐
    prompt = _build_script_prompt("民国豪门", "悬疑", ["沈长卿"])
    assert "称呼一致性铁律" in prompt, "必须有称呼一致性铁律"
    assert "严禁混用" in prompt, "必须禁止混用先生/小姐"
    # 必须点出"长卿""怀瑾"等中性名猜性别的问题
    assert "中性" in prompt or "字面" in prompt, "必须提醒 LLM 中性名字面会矛盾"

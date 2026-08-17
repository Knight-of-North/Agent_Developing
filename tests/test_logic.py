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
from validators import (
    _enforce_names,
    _normalize_secret_first_person,
    _fallback_clues,
    _extract_speak,
)
from nodes import distribute_clues_node, tally_node, route_speaker, _current_speaker
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


def test_current_speaker_round_robin():
    state = _make_state(0, ["张三", "李四", "王五"], "张三")
    assert _current_speaker(state)["name"] == "张三"
    state["phase_round"] = 1
    assert _current_speaker(state)["name"] == "李四"
    state["phase_round"] = 3
    assert _current_speaker(state)["name"] == "张三"   # 循环回第一个


def test_route_speaker_human_vs_ai():
    assert route_speaker(_make_state(0, ["张三", "李四"], "张三")) == "human"
    assert route_speaker(_make_state(1, ["张三", "李四"], "张三")) == "ai"


def test_route_speaker_vote_when_round_full():
    # 2 个嫌疑人 × ROUNDS_PER_PLAYER(3) = 6 轮，第 6 轮起进投票
    assert route_speaker(_make_state(6, ["张三", "李四"], "张三")) == "vote"


def test_route_speaker_no_suspects():
    state = {"phase_round": 0, "script": {"suspects": []}, "user_role": ""}
    assert route_speaker(state) == "vote"

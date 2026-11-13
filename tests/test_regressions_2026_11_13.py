"""2026-11-13 答辩前系统审查发现的回归测试。

覆盖：名单外人物名清洗误伤、JSON 非对象、最终陈词剧透、结局文案矛盾、
私密线索 schema、投票输入空白、线索公开误标记。
"""
import nodes
from rules import judge_ending, update_revealed_clues
from validators import (
    _ensure_clues,
    _find_outsider_names,
    _normalize_private_clues,
    _parse_json,
    _sanitize_outsider_names,
)


def test_outsider_name_detector_ignores_time_and_place_words():
    script = {
        "background": "那年夏天，明天早上，周末下午，秋天到了，他在路上遇到了一个人。",
        "truth": "秋天到了，甲在路上动手。",
        "suspects": [{"name": "甲"}, {"name": "乙"}],
    }
    assert _find_outsider_names(script) == []


def test_sanitize_outsider_names_keeps_role_and_family_terms():
    script = {
        "suspects": [{
            "name": "傅敬亭",
            "profession": "管家",
            "relation_to_victim": "顾家管家",
            "personal_script": "我是顾家的管家，顾家老爷对我有恩。",
        }],
        "background": "顾家老爷在寿宴上离奇身亡。",
        "truth": "顾家管家与私人医生合谋。",
        "public_clues": [],
        "hidden_clues": [],
        "private_clues": [],
        "relations": [],
    }
    out = _sanitize_outsider_names(script)
    assert out["suspects"][0]["profession"] == "管家"
    assert "顾家管家" in out["suspects"][0]["relation_to_victim"]
    assert "顾家老爷" in out["background"]
    assert "顾家管家" in out["truth"]


def test_parse_json_rejects_non_object_json():
    assert _parse_json("[]") == {"raw": "[]"}
    assert _parse_json("null") == {"raw": "null"}
    assert _parse_json('"ok"') == {"raw": '"ok"'}
    assert _parse_json('{"a": 1}') == {"a": 1}


def test_update_revealed_clues_only_checks_speaker_own_clues():
    distributed = {"甲": ["我昨天下午在书房整理文件"]}
    # 乙的正常发言与甲的线索有 2-gram 相似，但不应把甲的私密线索标记公开。
    assert update_revealed_clues(
        "我昨天下午在花园整理花草，没听见什么声音", distributed, {}, speaker="乙"
    ) == {}
    # 发言人自己提到的线索仍可公开。
    assert update_revealed_clues(
        "我承认，我昨天下午在书房整理文件", distributed, {}, speaker="甲"
    ) == {"我昨天下午在书房整理文件": "甲"}


def test_final_statement_prompt_does_not_contain_full_truth(monkeypatch):
    captured = {}

    def fake_safe_llm_text(prompt, **kwargs):
        captured["prompt"] = prompt
        return "我不是凶手。"

    monkeypatch.setattr(nodes, "_safe_llm_text", fake_safe_llm_text)
    state = {
        "vote_outcome": "winner",
        "vote_winner": "张三",
        "user_role": "李四",
        "script": {
            "suspects": [{"name": "张三", "secret": "我欠了债"}],
            "murderer": "张三",
            "truth": "张三在书房用刀杀死死者，随后清理现场。",
        },
    }
    nodes.final_statement_node(state)
    assert "书房用刀" not in captured["prompt"]
    assert "张三" in captured["prompt"]


def test_judge_ending_other_people_catch_murderer_does_not_say_escaped():
    state = {
        "user_role": "张三",
        "user_is_murderer": False,
        "votes": {"张三": "王五", "王五": "李四"},
        "vote_winner": "李四",
        "vote_outcome": "winner",
        "script": {
            "suspects": [{"name": "张三"}, {"name": "李四"}, {"name": "王五"}],
            "murderer": "李四",
            "truth": "李四杀人",
        },
        "messages": [{"speaker": "张三", "content": "我怀疑李四"}],
        "investigated_clues": [],
    }
    label, note = judge_ending(state)
    assert label == "被蒙蔽"
    assert "逍遥法外" not in note
    assert "投出真凶" in note


def test_private_clue_strings_are_dropped_and_secret_fallback_fills():
    script = {
        "suspects": [
            {"name": "甲", "secret": "我拿了钥匙"},
            {"name": "乙", "secret": "我看见了黑影"},
        ],
        "private_clues": ["不是标准结构"],
        "public_clues": ["公共线索"],
    }
    script = _normalize_private_clues(script)
    assert script["private_clues"] == []
    script = _ensure_clues(script)
    assert len(script["private_clues"]) == 2
    assert {c["holder"] for c in script["private_clues"]} == {"甲", "乙"}


def test_human_vote_strips_whitespace(monkeypatch):
    monkeypatch.setattr(nodes, "interrupt", lambda payload: " 李四 ")
    state = {
        "user_role": "张三",
        "script": {"suspects": [{"name": "张三"}, {"name": "李四"}]},
    }
    assert nodes.human_vote_node(state) == {"votes": {"张三": "李四"}}

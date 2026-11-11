"""2026-11 系统性优化配套回归测试。

每个用例对应一条真实缺陷（不是"补覆盖率"的凑数测试）：
1. .env 配置项在 config 求值时是否真的生效（P0，24 项静默失效）
2. forbidden 词表清洗（含"我"这类停用词会让每轮都判泄密）
3. forbidden 被写成字符串时不得退化为"逐字检测"
4. 指控计数必须认目标（此前甲指控乙，丙也被计数）
5. secret 非字符串不得炸穿剧本生成（AttributeError）
6. 剧本结构异常必须转成"可重试的问题"而不是抛出
7. 玩家投票候选人剔除自己（与 AI 侧规则对齐）
8. prompt 里不得出现 Python repr（list / dict）
9. 致命标记是数据而非字符串嗅探（str 子类保持向后兼容）
10. 并发助手：保序 + 只重跑失败项
11. distributed_clues 走合并 reducer（返回增量而非整字典）
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nodes import (
    _check_script_consistency,
    _is_fatal,
    _postprocess_script,
    _run_concurrently,
    ai_player_turn_node,
    human_turn_node,
    human_vote_node,
)
from prompts import build_midpoint_prompt, build_reveal_prompt
from rules import count_accusations_against
from validators import _normalize_secrets, _sanitize_forbidden

ROOT = Path(__file__).resolve().parent.parent


# ============ 1. 配置加载时序（P0 回归） ============

def test_config_reads_dotenv_at_import_time(tmp_path):
    """config.py 必须在读取 os.getenv 之前加载 .env。

    复现原始 bug：config 在 load_dotenv 之前 import → .env 里 24 项配置
    全部被忽略，只有调用期才读的 DEEPSEEK_* 生效。
    用子进程跑一份 config.py 副本，确保测的是"import 那一刻"的真实行为，
    而不是测试进程里已经被 import 过的模块缓存。
    """
    shutil.copy(ROOT / "config.py", tmp_path / "config.py")
    (tmp_path / ".env").write_text(
        "ROUNDS_PER_PLAYER=9\nLLM_TIMEOUT=7\nLLM_TEMP_SCRIPT=0.11\n",
        encoding="utf-8",
    )
    code = (
        "import config;"
        "print(config.ROUNDS_PER_PLAYER, config.LLM_TIMEOUT, config.LLM_CONFIG['script']['temperature'])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path,
        capture_output=True, text=True, timeout=60, check=True,
    )
    assert out.stdout.strip() == "9 7 0.11", f".env 未生效，实际输出：{out.stdout!r} {out.stderr!r}"


# ============ 2. forbidden 清洗 ============

def test_forbidden_drops_stopwords_and_names():
    cleaned = _sanitize_forbidden(
        ["我", "秘密", "张三", "秘密", "", "暗恋"],
        name="张三", suspect_names=["张三", "李四"],
    )
    assert "我" not in cleaned          # 停用词：会让每句正常台词都判泄密
    assert "张三" not in cleaned        # 自己/他人名字：每次称呼都误报
    assert "秘密" in cleaned and "暗恋" in cleaned
    assert cleaned.count("秘密") == 1   # 去重


def test_forbidden_non_list_returns_empty():
    assert _sanitize_forbidden("暗恋") == []
    assert _sanitize_forbidden(None) == []


def test_forbidden_string_not_treated_as_char_list(monkeypatch):
    """forbidden 若被写成字符串，绝不能退化成"逐字检测"。

    旧实现 `for w in "暗恋"` 会逐个字符判断，"我暗中去过"里的"暗"命中 →
    误判泄密 → 3 次重试后整句被替换成固定兜底台词。
    """
    monkeypatch.setattr(
        "nodes._safe_llm_text",
        lambda *a, **k: json.dumps({"think": "观察", "speak": "我暗中去过走廊。"}, ensure_ascii=False),
    )
    state = {
        "script": {"suspects": [
            {"name": "张三", "secret": "我暗恋李四", "forbidden": "暗恋", "gender": "男"},
            {"name": "李四", "secret": "我欠了钱", "forbidden": ["欠钱"], "gender": "女"},
        ], "murderer": "李四"},
        "messages": [],
        "phase_round": 0,
        "rounds_per_player": 3,
        "user_role": "李四",
    }
    out = ai_player_turn_node(state)
    assert "我暗中去过走廊" in out["messages"][0]["content"]
    assert "不想多谈" not in out["messages"][0]["content"]


# ============ 3. 指控计数认目标 ============

def test_accusation_count_only_counts_the_named_target():
    msgs = [{"speaker": "甲", "content": "我正式指控 乙 是凶手！"}]
    assert count_accusations_against(msgs, "乙") == 1
    assert count_accusations_against(msgs, "丙") == 0   # 此前误计为 1


def test_accusation_count_ignores_non_string_content():
    assert count_accusations_against([{"speaker": "甲", "content": None}], "乙") == 0


# ============ 4. 非字符串 secret 不得炸穿生成流程 ============

def test_normalize_secrets_tolerates_non_string():
    script = {"suspects": [{"name": "张三", "secret": 123}, {"name": "李四", "secret": ["x"]}]}
    out = _normalize_secrets(script)   # 旧实现 AttributeError: 'int' has no attribute 'startswith'
    assert out["suspects"][0]["secret"] == "123"
    assert out["suspects"][1]["secret"] == ""


def test_postprocess_script_converts_structure_error_to_retryable_problem():
    """suspects 结构荒唐时必须返回"可重试的致命问题"，而不是抛异常炸掉整局。"""
    script, problems = _postprocess_script('{"suspects": {"张三": 1}, "truth": "x"}', ["张三", "李四"])
    assert script == {}
    assert len(problems) == 1
    assert _is_fatal(problems[0]) is True


# ============ 5. 致命标记是数据而非字符串嗅探 ============

def test_fatal_flag_is_data_not_substring():
    script = {
        "suspects": [{"name": "张三", "secret": "我偷了东西", "personal_script": "x",
                      "profession": "p", "relation_to_victim": "r", "alibi": "a",
                      "forbidden": ["偷了东西"]}],
        "truth": "",                       # 致命：真相缺失
        "murderer": "张三",
        "public_clues": ["线索"],
        "relations": [{"from": "张三", "to": "死者", "rel": "我恨他", "public": True}],
    }
    problems = _check_script_consistency(script)
    fatal = [p for p in problems if _is_fatal(p)]
    assert any("truth" in str(p) for p in fatal)
    # str 子类保持向后兼容：既有 `"xxx" in problems` 写法仍然成立
    assert "truth（案件真相）缺失或为空，必须写明作案动机与完整手法" in problems


# ============ 6. 玩家不能投自己 ============

def test_human_vote_excludes_self(monkeypatch):
    captured = {}

    def fake_interrupt(payload):
        captured.update(payload)
        return "李四"

    monkeypatch.setattr("nodes.interrupt", fake_interrupt)
    state = {
        "user_role": "张三",
        "script": {"suspects": [{"name": "张三"}, {"name": "李四"}]},
    }
    out = human_vote_node(state)
    assert "张三" not in captured["suspects"]
    assert out["votes"] == {"张三": "李四"}


def test_human_vote_keeps_self_when_only_candidate(monkeypatch):
    """只剩自己一个候选人时回退原名单，避免投票环节无选项可用。"""
    captured = {}
    monkeypatch.setattr("nodes.interrupt", lambda p: captured.update(p) or "张三")
    state = {"user_role": "张三", "script": {"suspects": [{"name": "张三"}]}}
    human_vote_node(state)
    assert captured["suspects"] == ["张三"]


# ============ 7. prompt 里不得出现 Python repr ============

def test_midpoint_prompt_has_no_list_repr():
    prompt = build_midpoint_prompt({"书房有血迹": "张三"}, ["案发时间"])
    assert "['" not in prompt
    assert "书房有血迹" in prompt


def test_reveal_prompt_formats_vote_counts_human_readable():
    prompt = build_reveal_prompt(
        truth="x", votes_text="甲投乙", vote_counts={"乙": 2, "丙": 1},
        vote_winner="乙", winner_note="", user_role="甲", user_vote="乙",
        result_note="", clues_text="", history="", ending_label="侦探", ending_note="",
    )
    assert "{'乙'" not in prompt
    assert "乙 2 票" in prompt and "丙 1 票" in prompt


# ============ 8. 并发助手：保序 + 只重跑失败项 ============

def test_run_concurrently_preserves_order_and_retries_only_failed(monkeypatch):
    calls = []

    def fn(x):
        calls.append(x)
        if x == 2 and calls.count(2) == 1:
            raise RuntimeError("first attempt fails")
        return x * 10

    monkeypatch.setattr("nodes.VOTE_CONCURRENT", True)
    out = _run_concurrently(fn, [1, 2, 3], label="test")
    assert out == [10, 20, 30]          # 保序
    assert calls.count(1) == 1          # 成功的条目没有被重跑
    assert calls.count(2) == 2          # 只有失败的那条重试


# ============ 9. distributed_clues 返回增量（合并 reducer） ============

def test_investigate_returns_incremental_distributed_clues(monkeypatch):
    monkeypatch.setattr("nodes.interrupt", lambda payload: {"action": "investigate"})
    state = {
        "user_role": "张三",
        "phase_round": 0,
        "script": {"suspects": [{"name": "张三"}, {"name": "李四"}], "hidden_clues": ["隐藏线索A"]},
        "distributed_clues": {"张三": [], "李四": ["别人的线索"]},
        "investigated_clues": [],
        "investigations_used": 0,
    }
    out = human_turn_node(state)
    # 只返回自己那一份，避免整字典替换覆盖其他写入方
    assert set(out["distributed_clues"]) == {"张三"}
    assert "隐藏线索A" in out["distributed_clues"]["张三"]


def test_gamestate_merges_distributed_clues():
    from game_state import GameState

    reducer = GameState.__annotations__["distributed_clues"]
    # Annotated[dict, _merge_dict] —— reducer 在 __metadata__ 里
    merge = reducer.__metadata__[0]
    merged = merge({"张三": ["a"], "李四": ["b"]}, {"张三": ["a", "c"]})
    assert merged == {"张三": ["a", "c"], "李四": ["b"]}


# ============ 10. 空输入不得把 "None" 当台词广播 ============

@pytest.mark.parametrize("payload", [None, "", "   "])
def test_empty_action_becomes_silence_placeholder(monkeypatch, payload):
    monkeypatch.setattr("nodes.interrupt", lambda p: payload)
    state = {
        "user_role": "张三",
        "phase_round": 0,
        "script": {"suspects": [{"name": "张三"}, {"name": "李四"}]},
        "distributed_clues": {},
    }
    out = human_turn_node(state)
    assert out["messages"][0]["content"] != "None"
    assert out["messages"][0]["content"].strip()

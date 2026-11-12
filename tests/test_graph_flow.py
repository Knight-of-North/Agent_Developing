"""图级端到端烟测：用 fake LLM 跑通一局最短路径，覆盖 interrupt 路由与状态推进。

不调用真实 DeepSeek API，也不依赖网络。
"""
from langgraph.types import Command

import nodes
from graph import build_graph
from interrupt_handler import get_interrupt


class _Chunk:
    def __init__(self, text: str):
        self.content = text


class _FakeLLM:
    def __init__(self):
        self.calls = []

    def stream(self, prompt: str):
        self.calls.append(prompt)
        # 各节点按 prompt 里的标志性文本返回不同形态的输出
        if "自我介绍" in prompt:
            return [_Chunk("我是李四，先听别人说吧。")]
        if "投票" in prompt or "vote" in prompt.lower():
            return [_Chunk('{"vote": "", "think": "先不投"}')]
        if "最后陈述" in prompt or "辩护" in prompt:
            return [_Chunk("我最后说一句，我不是凶手。")]
        if "主持人" in prompt or "DM" in prompt:
            return [_Chunk("主持人讲述开场。")]
        if "真相" in prompt or "揭晓" in prompt:
            return [_Chunk("真相揭晓，案件复盘。")]
        return [_Chunk('{"think": "我在观察", "speak": "我怀疑李四。"}')]


def _script():
    return {
        "background": "一间封闭的自习室里发生了一起案件。",
        "suspects": [
            {
                "name": "张三", "gender": "男", "profession": "学生",
                "relation_to_victim": "同学", "alibi": "在宿舍", "task": "找出真凶",
                "personality": "冷静", "speech_style": "简短", "secret": "我见过可疑的人",
                "forbidden": [], "personal_script": "我是张三。",
            },
            {
                "name": "李四", "gender": "女", "profession": "学生",
                "relation_to_victim": "同学", "alibi": "在图书馆", "task": "洗清嫌疑",
                "personality": "谨慎", "speech_style": "温和", "secret": "我撒了谎",
                "forbidden": [], "personal_script": "我是李四。",
            },
        ],
        "relations": [{"from": "张三", "to": "李四", "rel": "我怀疑她", "public": True}],
        "public_clues": ["现场有一张纸条"],
        "private_clues": [],
        "murderer": "李四",
        "truth": "李四作案后回到图书馆。",
        "hidden_clues": ["监控录像"],
    }


def test_fake_llm_full_graph_smoke(monkeypatch):
    fake = _FakeLLM()
    monkeypatch.setattr(nodes, "get_llm", lambda *args, **kwargs: fake)
    graph = build_graph()
    config = {"configurable": {"thread_id": "graph-e2e-fake"}}
    state = {
        "script": _script(),
        "theme": "校园怪谈",
        "background_style": "校园怪谈",
        "rounds_per_player": 1,
        "messages": [],
        "used_names": [],
    }
    result = graph.invoke(state, config)
    seen = set()
    for _ in range(30):
        if "__interrupt__" not in result:
            break
        info = get_interrupt(result)
        seen.add(info.get("type"))
        if info["type"] == "choose_role":
            resume = "张三"
        elif info["type"] == "human_turn":
            resume = "我怀疑李四。"
        elif info["type"] == "human_vote":
            resume = "李四"
        elif info["type"] == "human_tiebreak":
            resume = "我不是凶手。"
        else:
            raise AssertionError(f"unexpected interrupt: {info}")
        result = graph.invoke(Command(resume=resume), config)

    assert "choose_role" in seen
    assert "human_turn" in seen
    assert "human_vote" in seen
    assert result.get("game_result") == "平民胜利"
    assert result.get("current_phase") == "reveal"
    assert any(m.get("speaker") == "主持人" for m in result.get("messages", []))

"""
LangGraph 图定义 —— 把节点和边组装成完整的剧本杀状态机。

流程：
  START
    → generate_script（生成剧本+线索）
    → distribute_clues（线索分发，信息差）
    → choose_role（interrupt 等用户选角色）
    → dm_intro（DM 开场）
    → self_intro（AI 自我介绍）
    → 讨论循环（route_speaker 条件路由）：
        human_turn ──→ route_speaker
        ai_player_turn ──→ route_speaker
        dm_midpoint ──→ route_speaker
        vote（讨论轮次用完）
    → ai_vote（AI 投票）
    → human_vote（interrupt 等用户投票）
    → tally（统计票数+全局胜负）
    → route_after_tally（H15：平票且未加时 → tie_break 重投一轮）
        → tie_break（平票候选人辩护）→ ai_vote → human_vote → tally
        → final_statement（被投最高者最终陈词）
    → dm_reveal（DM 揭晓真相+线索复盘）
    → END
"""
import os
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver as MemorySaver
from game_state import GameState
from nodes import (
    generate_script_node, distribute_clues_node, choose_role_node,
    dm_intro_node, self_intro_node, ai_player_turn_node, human_turn_node,
    dm_midpoint_node, ai_vote_node, human_vote_node, tally_node,
    tie_break_node, final_statement_node, dm_reveal_node,
    route_speaker, route_after_tally,
)


def _build_checkpointer():
    """构建 checkpointer（H8：默认内存；PERSISTENT_CHECKPOINT=1 时用 SqliteSaver）。

    SqliteSaver 需要 `pip install langgraph-checkpoint-sqlite`，
    未安装时自动回退 MemorySaver 并记录日志。
    """
    if os.getenv("PERSISTENT_CHECKPOINT", "").strip().lower() in ("1", "true", "yes"):
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "checkpoints.sqlite")
            import sqlite3
            conn = sqlite3.connect(db_path, check_same_thread=False)
            return SqliteSaver(conn)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("SqliteSaver 不可用（%s），回退 MemorySaver", type(e).__name__)
    return MemorySaver()


def build_graph():
    """构建并编译剧本杀状态机图。"""
    g = StateGraph(GameState)

    g.add_node("generate_script", generate_script_node)
    g.add_node("distribute_clues", distribute_clues_node)
    g.add_node("choose_role", choose_role_node)
    g.add_node("dm_intro", dm_intro_node)
    g.add_node("self_intro", self_intro_node)
    g.add_node("ai_player_turn", ai_player_turn_node)
    g.add_node("human_turn", human_turn_node)
    g.add_node("dm_midpoint", dm_midpoint_node)
    g.add_node("ai_vote", ai_vote_node)
    g.add_node("human_vote", human_vote_node)
    g.add_node("tally", tally_node)
    g.add_node("tie_break", tie_break_node)
    g.add_node("final_statement", final_statement_node)
    g.add_node("dm_reveal", dm_reveal_node)

    g.add_edge(START, "generate_script")
    g.add_edge("generate_script", "distribute_clues")
    g.add_edge("distribute_clues", "choose_role")
    g.add_edge("choose_role", "dm_intro")
    g.add_edge("dm_intro", "self_intro")
    g.add_edge("self_intro", "ai_player_turn")

    # 讨论循环：条件边路由
    g.add_conditional_edges(
        "ai_player_turn",
        route_speaker,
        {"human": "human_turn", "ai": "ai_player_turn", "midpoint": "dm_midpoint", "vote": "ai_vote"},
    )
    g.add_conditional_edges(
        "human_turn",
        route_speaker,
        {"human": "human_turn", "ai": "ai_player_turn", "midpoint": "dm_midpoint", "vote": "ai_vote"},
    )
    g.add_conditional_edges(
        "dm_midpoint",
        route_speaker,
        {"human": "human_turn", "ai": "ai_player_turn", "midpoint": "dm_midpoint", "vote": "ai_vote"},
    )

    # 投票阶段
    g.add_edge("ai_vote", "human_vote")
    g.add_edge("human_vote", "tally")
    # H15：平票加时——平票且未加时过则辩护后重投一轮，否则进入最终陈词
    g.add_conditional_edges(
        "tally",
        route_after_tally,
        {"tie_break": "tie_break", "final_statement": "final_statement"},
    )
    g.add_edge("tie_break", "ai_vote")
    g.add_edge("final_statement", "dm_reveal")
    g.add_edge("dm_reveal", END)

    return g.compile(checkpointer=_build_checkpointer())

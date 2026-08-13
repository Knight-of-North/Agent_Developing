"""
流程编排 —— Phase 3.5：用户参与（interrupt 人在回路）

图结构：
    START -> generate_script -> dm_intro -+
                                          |   （条件边 route_speaker）
              +---------------------------+
              v
    human_turn（interrupt 等用户） <----+
    ai_player_turn（AI 发言）      <----+---- 循环
              |                        |
              +---- 轮满 -> ai_vote -> human_vote -> tally -> dm_reveal -> END

关键：本图带 checkpointer（MemorySaver），才能在 interrupt 处"暂停并记住进度"。
"""
from langgraph.graph import StateGraph, START, END

try:
    from langgraph.checkpoint.memory import MemorySaver
except ImportError:
    from langgraph.checkpoint.memory import InMemorySaver as MemorySaver

from game_state import GameState
from nodes import (
    generate_script_node,
    dm_intro_node,
    ai_player_turn_node,
    human_turn_node,
    ai_vote_node,
    human_vote_node,
    tally_node,
    dm_reveal_node,
    route_speaker,
)


def build_graph():
    builder = StateGraph(GameState)

    # 注册节点
    builder.add_node("generate_script", generate_script_node)
    builder.add_node("dm_intro", dm_intro_node)
    builder.add_node("ai_player_turn", ai_player_turn_node)
    builder.add_node("human_turn", human_turn_node)   # interrupt 节点
    builder.add_node("ai_vote", ai_vote_node)
    builder.add_node("human_vote", human_vote_node)   # interrupt 节点
    builder.add_node("tally", tally_node)
    builder.add_node("dm_reveal", dm_reveal_node)

    # 前半段
    builder.add_edge(START, "generate_script")
    builder.add_edge("generate_script", "dm_intro")

    # 条件边：讨论循环的路由（挂在三个"发言入口"之后）
    # route_speaker 根据 phase_round 决定下一个发言者是用户还是 AI，或进入投票
    route_map = {
        "human": "human_turn",
        "ai": "ai_player_turn",
        "vote": "ai_vote",
    }
    builder.add_conditional_edges("dm_intro", route_speaker, route_map)
    builder.add_conditional_edges("ai_player_turn", route_speaker, route_map)
    builder.add_conditional_edges("human_turn", route_speaker, route_map)

    # 投票链：AI 投票 -> 用户投票 -> 统计 -> 揭晓
    builder.add_edge("ai_vote", "human_vote")
    builder.add_edge("human_vote", "tally")
    builder.add_edge("tally", "dm_reveal")
    builder.add_edge("dm_reveal", END)

    # 关键：带上 checkpointer，图才能在 interrupt 处暂停并记住进度
    return builder.compile(checkpointer=MemorySaver())


if __name__ == "__main__":
    graph = build_graph()
    graph.get_graph().print_ascii()

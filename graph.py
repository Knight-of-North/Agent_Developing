"""
流程编排 —— Phase 3：加入投票和统计节点

图结构：
    START -> generate_script -> dm_intro -> ai_player_turn -+
                                          ^                 |
                                          |   （条件边）     |
                                          +----- continue --+
                                          （轮次满则 vote 往下）

    ai_player_turn --vote--> ai_vote -> tally -> dm_reveal -> END
"""
from langgraph.graph import StateGraph, START, END
from game_state import GameState
from nodes import (
    generate_script_node,
    dm_intro_node,
    ai_player_turn_node,
    ai_vote_node,
    tally_node,
    dm_reveal_node,
    should_continue,
)


def build_graph():
    # 1. 创建图
    builder = StateGraph(GameState)

    # 2. 注册节点（LLM 节点 + 确定性节点）
    builder.add_node("generate_script", generate_script_node)
    builder.add_node("dm_intro", dm_intro_node)
    builder.add_node("ai_player_turn", ai_player_turn_node)
    builder.add_node("ai_vote", ai_vote_node)      # LLM 节点：投票
    builder.add_node("tally", tally_node)          # 确定性节点：统计票数
    builder.add_node("dm_reveal", dm_reveal_node)

    # 3. 前半段：无条件边
    builder.add_edge(START, "generate_script")
    builder.add_edge("generate_script", "dm_intro")
    builder.add_edge("dm_intro", "ai_player_turn")

    # 4. 条件边：讨论循环
    builder.add_conditional_edges(
        "ai_player_turn",
        should_continue,
        {
            "continue": "ai_player_turn",   # 继续 -> 循环
            "vote": "ai_vote",              # 聊够了 -> 去投票
        },
    )

    # 5. 投票 -> 统计 -> 揭晓，一条直线
    builder.add_edge("ai_vote", "tally")
    builder.add_edge("tally", "dm_reveal")
    builder.add_edge("dm_reveal", END)

    return builder.compile()


if __name__ == "__main__":
    # 自测：打印 ASCII 图
    graph = build_graph()
    graph.get_graph().print_ascii()

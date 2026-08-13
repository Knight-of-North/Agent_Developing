"""
流程编排 —— Phase 2：加入循环和条件边

图结构：
    START -> generate_script -> dm_intro -> ai_player_turn -+
                                          ^                 |
                                          |   （条件边）     |
                                          +----- continue --+
                                          （轮次满则 reveal 往下）

    ai_player_turn --reveal--> dm_reveal -> END
"""
from langgraph.graph import StateGraph, START, END
from game_state import GameState
from nodes import (
    generate_script_node,
    dm_intro_node,
    ai_player_turn_node,
    dm_reveal_node,
    should_continue,
)


def build_graph():
    # 1. 创建图
    builder = StateGraph(GameState)

    # 2. 注册节点
    builder.add_node("generate_script", generate_script_node)
    builder.add_node("dm_intro", dm_intro_node)
    builder.add_node("ai_player_turn", ai_player_turn_node)
    builder.add_node("dm_reveal", dm_reveal_node)

    # 3. 连线：前半段是无条件边（一条直线）
    builder.add_edge(START, "generate_script")
    builder.add_edge("generate_script", "dm_intro")
    builder.add_edge("dm_intro", "ai_player_turn")

    # 4. 关键：条件边。从 ai_player_turn 出发，由 should_continue 决定下一步去哪。
    #    这是 Phase 2 的核心 —— 用"状态"来决定流程走向，而不是写死顺序。
    builder.add_conditional_edges(
        "ai_player_turn",          # 从哪个节点出发
        should_continue,           # 路由函数：读 state，返回 "continue" 或 "reveal"
        {
            "continue": "ai_player_turn",   # 继续 -> 回到自己（形成循环！）
            "reveal": "dm_reveal",          # 揭晓 -> 往下走
        },
    )

    # 5. 收尾
    builder.add_edge("dm_reveal", END)

    return builder.compile()


if __name__ == "__main__":
    # 自测：打印 ASCII 图，会看到 ai_player_turn 有个"回到自己"的环
    graph = build_graph()
    graph.get_graph().print_ascii()

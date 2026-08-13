"""
流程编排 —— LangGraph 里的"导演"

用 StateGraph 把各个节点（演员）按顺序串起来，形成游戏流程。

Phase 1 是最简单的线性流程（一条直线走到底）：
    START -> generate_script_node -> dungeon_master_node -> END
"""
from langgraph.graph import StateGraph, START, END
from game_state import GameState
from nodes import generate_script_node, dungeon_master_node


def build_graph():
    # 1. 创建图，指定它共享的状态类型
    builder = StateGraph(GameState)

    # 2. 注册节点：把函数挂到图上，并起一个名字（名字用于连线）
    builder.add_node("generate_script", generate_script_node)
    builder.add_node("dungeon_master", dungeon_master_node)

    # 3. 连线（add_edge 是"无条件边"：A 走完一定去 B）
    builder.add_edge(START, "generate_script")               # 程序开始 -> 生成剧本
    builder.add_edge("generate_script", "dungeon_master")    # 剧本 -> DM 主持
    builder.add_edge("dungeon_master", END)                  # DM 主持 -> 结束

    # 4. 编译成可运行的图对象
    return builder.compile()


if __name__ == "__main__":
    # 自测：打印图的 ASCII 结构，方便看清节点怎么连的
    graph = build_graph()
    graph.get_graph().print_ascii()

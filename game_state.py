"""
游戏状态定义 —— LangGraph 里的"共享舞台"

想象一场话剧：所有演员（节点）在同一个舞台（状态）上演戏。
每个演员上台，先看一眼舞台上的道具（读状态），
演完后再往舞台上添几样东西（返回"部分更新"）。

这个文件就是定义"舞台上到底能放哪些道具"。
"""
from typing import TypedDict, Annotated
import operator


class GameState(TypedDict, total=False):
    """
    total=False 表示：这些字段不要求一开始就全部填满，
    节点可以逐步往状态里添加字段（LangGraph 节点返回"部分更新"）。
    """

    # 剧本主题（由用户在 main.py 输入）
    theme: str

    # 剧本：generate_script_node 生成的结构化数据
    # 形如 {"background": "...", "suspects": [...], "clues": [...], "truth": "..."}
    script: dict

    # 当前阶段：控制游戏走到哪一步
    # "generate" -> "intro" -> "clues" -> "vote" -> "reveal"
    current_phase: str

    # 当前阶段轮次（Phase 2 多轮对话时才用，Phase 1 先定义好占位）
    phase_round: int

    # 对话历史
    # Annotated[list, operator.add] 是关键：它给 messages 指定了一个"合并规则"（reducer）。
    # 当节点返回 {"messages": [新消息]} 时，LangGraph 会把新列表"追加"到旧列表后面，
    # 而不是直接"覆盖"旧的。这就是为什么多轮对话不会把之前的发言冲掉。
    messages: Annotated[list, operator.add]

    # 玩家信息 {玩家名: 角色名}
    players: dict

    # AI 玩家列表（Phase 2 起用）
    ai_players: list

    # 线索池（所有线索）
    clues_pool: list

    # 已分配的线索 {玩家名: [线索...]}
    distributed_clues: dict

    # 投票结果 {投票者: 被投者}
    votes: dict

"""
游戏状态定义 —— LangGraph 里的"共享舞台"

Phase 2 变化：
1. messages 的元素从"纯字符串"升级为 dict：{"speaker": 谁, "content": 说了什么}
   这样打印时能区分是谁说的，也为 Phase 3 的私聊/信息差打基础。
2. 新增 thoughts 字段：AI 玩家的"内心戏"（think 通道），不对外公开，仅供调试。
"""
from typing import TypedDict, Annotated
import operator


class GameState(TypedDict, total=False):
    """total=False：字段不必一开始就填满，节点逐步往状态里添加。"""

    # 剧本主题（用户在 main.py 输入）
    theme: str

    # 剧本：generate_script_node 生成的结构化数据
    script: dict

    # 当前阶段：generate -> intro -> discuss -> reveal
    current_phase: str

    # 讨论轮次（Phase 2 用它做循环计数，配合条件边判断是否继续）
    phase_round: int

    # 公开对话历史（元素是 dict）：{"speaker": "角色名", "content": "台词"}
    # operator.add 保证新消息"追加"而不是"覆盖"旧的
    messages: Annotated[list, operator.add]

    # AI 玩家的内心戏（think 通道，元素是字符串），不展示给"其他玩家"
    thoughts: Annotated[list, operator.add]

    # 玩家信息 {玩家名: 角色名}
    players: dict

    # AI 玩家列表
    ai_players: list

    # 线索池（所有线索）
    clues_pool: list

    # 已分配的线索 {玩家名: [线索...]}
    distributed_clues: dict

    # 投票结果 {投票者: 被投者}
    votes: dict

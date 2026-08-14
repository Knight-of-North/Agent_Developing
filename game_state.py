"""
游戏状态定义 —— LangGraph 里的"共享舞台"

Phase 3.5 变化：新增 user_role 字段（用户扮演的角色）。
"""
from typing import TypedDict, Annotated
import operator


class GameState(TypedDict, total=False):
    """total=False：字段不必一开始就填满，节点逐步往状态里添加。"""

    # 剧本主题（用户在 main.py 输入）
    theme: str

    # 背景风格（用户选择：民国豪门 / 校园怪谈 / 古风仙侠 / 现代都市 / 科幻末世 / 自由发挥）
    background_style: str

    # 剧本：generate_script_node 生成的结构化数据
    script: dict

    # 当前阶段：generate -> intro -> discuss -> vote -> reveal
    current_phase: str

    # 讨论轮次（配合条件边判断是否继续循环）
    phase_round: int

    # 用户扮演的角色名（generate_script_node 自动设为第一个嫌疑人）
    user_role: str

    # 公开对话历史（元素是 dict）：{"speaker": "角色名", "content": "台词"}
    messages: Annotated[list, operator.add]

    # AI 玩家的内心戏（think 通道），不展示给"其他玩家"
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

    # 得票最多的嫌疑人（tally_node 统计得出）
    vote_winner: str

    # 各嫌疑人得票数 {嫌疑人: 票数}（tally_node 统计得出）
    vote_counts: dict

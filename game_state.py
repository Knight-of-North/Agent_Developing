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

    # 用户自定义的剧情背景（可选，一段具体的背景剧情：人物关系/事件起因/世界观设定。
    # 提供时作为 LLM 创作的核心依据，优先级高于 theme + background_style）
    background_story: str

    # 用户自定义的故事时间（可选，如"1935 年深秋"、"宋代江南"、"未来废土纪元"）。
    # 作为"素材种子"喂给 LLM，引导它理解时代氛围后自然融入，而非把时间字样硬贴进剧情。
    story_time: str

    # 用户自定义的故事地点（可选，如"上海滩租界"、"湖南师大图书馆"、"深山古宅"）。
    # 同上：LLM 要理解地点的空间/人文特征后让场景自然生长，而非照抄地点名词。
    story_location: str

    # 用户自定义的嫌疑人名字（可选，列表。提供时优先于随机抽名，更有代入感）
    custom_names: list

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

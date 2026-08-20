"""
游戏状态定义 —— LangGraph 里的"共享舞台"

Phase 3.5 变化：新增 user_role 字段（用户扮演的角色）。
"""
from typing import TypedDict, Annotated
import operator


def _merge_dict(a: dict, b: dict) -> dict:
    """字典合并 reducer：两个节点返回的 dict 合并，后写入的覆盖同名键。

    为什么 votes 需要它？votes 是普通 dict，LangGraph 默认"后写覆盖前写"，
    会导致 ai_vote_node 返回的票被 human_vote_node 覆盖。用合并 reducer 后，
    两个节点各自 `return {"votes": {...}}`，框架自动合并成一张完整投票表，
    节点里就不用再手动 `dict(state.get("votes", {}))` 拷贝了。
    """
    return {**a, **b}


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
    custom_names: list[str]

    # 剧本：generate_script_node 生成的结构化数据
    script: dict

    # 当前阶段：generate -> intro -> discuss -> vote -> reveal
    current_phase: str

    # 讨论轮次（配合条件边判断是否继续循环）
    phase_round: int

    # 讨论节奏（每人发言轮数，默认 3；app 开局可选快/标准/深入 → 2/3/4）
    rounds_per_player: int

    # 中场引导是否已做过（dm_midpoint_node 触发一次后置 True，避免重复）
    midpoint_done: bool

    # 被玩家点名、待回应的角色名（空 = 无）。定向通信：玩家发言点名某 AI 后，
    # 该 AI 优先回应一次，回应后清空（报告⑤⑮的"点名优先发言"）
    pending_reply_to: str

    # 玩家连续追问的剩余次数（>0 时 route_speaker 再次轮到玩家）。
    # 玩家点名 AI 后置 1，AI 回应后玩家可追问一次，追问后归零——形成小交锋（报告④的轻量版）
    follow_up: int

    # 用户扮演的角色名（choose_role_node 里由用户自选；选完才确定）
    user_role: str

    # 用户扮演的角色是否为凶手（choose_role_node 里对比 user_role 与 murderer 得出）。
    # 用于差异化提示（"你是真凶，目标脱罪"）和结局演绎（完美犯罪）
    user_is_murderer: bool

    # 公开对话历史（元素是 dict）：{"speaker": "角色名", "content": "台词"}
    messages: Annotated[list, operator.add]

    # AI 玩家的内心戏（think 通道），不展示给"其他玩家"
    thoughts: Annotated[list, operator.add]

    # 已分配的线索 {玩家名: [线索...]}
    distributed_clues: dict[str, list[str]]

    # 线索公开状态 {线索内容: 首次提及的发言人}，讨论中逐条追踪哪些线索已被公开。
    # 供 DM 中场引导、AI 发言提示"未公开线索"、复盘数据化使用（报告⑨）。
    revealed_clues: Annotated[dict, _merge_dict]

    # 已调查的隐藏线索（list，追加）。玩家每「调查」一次，从 script.hidden_clues 里
    # 揭示一条、记录到这里，避免重复调查同一条。也用于判断"是否还能调查"。
    investigated_clues: Annotated[list, operator.add]

    # AI 自我记忆 {角色名: [该角色说过的关键陈述，最多保留最近 3 条]}。
    # 防 AI 自相矛盾（第 2 轮说"在书房"第 8 轮说"在厨房"），凶手忘词会意外露馅
    agent_memory: Annotated[dict, _merge_dict]

    # 投票结果 {投票者: 被投者}
    # 用合并 reducer：ai_vote_node 和 human_vote_node 各自返回自己的票，框架自动合并
    votes: Annotated[dict, _merge_dict]

    # 得票最多的嫌疑人（tally_node 统计得出）
    vote_winner: str

    # 各嫌疑人得票数 {嫌疑人: 票数}（tally_node 统计得出）
    vote_counts: dict

    # 全局胜负（tally_node 判定）：平民胜利（投出真凶）/ 凶手胜利（真凶逃脱）/ 平局（平票）。
    # 剧本杀是"平民 vs 凶手"的对抗游戏，这个字段让投票有了真正的 stakes（报告风险 2）
    game_result: str

"""
节点函数 —— LangGraph 里的"演员"

Phase 3 节点清单：
1. generate_script_node ：生成剧本（LLM）
2. dm_intro_node       ：DM 开场介绍（LLM）
3. ai_player_turn_node ：AI 玩家发言，think/speak 双通道（LLM）
4. ai_vote_node        ：AI 玩家投票指认凶手（LLM，新增）
5. tally_node          ：统计票数（确定性节点，不调 LLM，新增）
6. dm_reveal_node      ：DM 揭晓真相 + 对比投票（LLM）
7. should_continue     ：条件边的路由函数
"""
import os
import json
import re
from collections import Counter
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

load_dotenv()

llm = ChatDeepSeek(
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    temperature=0.8,
    max_tokens=4096,
    reasoning_effort="none",   # 关键：关闭 v4 默认的 thinking 模式，让答案直接进 content
)

# 讨论最多进行几轮
MAX_ROUNDS = 6


def _parse_json(text: str) -> dict:
    """从模型输出里安全提取 JSON（容错去掉 ```json 标记）"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def generate_script_node(state: dict) -> dict:
    """节点 1：根据主题生成结构化剧本"""
    theme = state.get("theme", "民国豪门恩怨")

    prompt = f"""你是一名资深剧本杀编剧。请围绕主题「{theme}」创作一个完整的剧本杀剧本。

严格输出 JSON 格式，不要输出任何 JSON 以外的文字。字段如下：
{{
  "background": "案件背景故事，约100字",
  "suspects": [
    {{"name": "嫌疑人名字", "secret": "这个人隐藏的秘密"}}
  ],
  "clues": ["线索1", "线索2", "线索3", "线索4", "线索5"],
  "truth": "案件真相：凶手是谁、动机、作案手法"
}}"""

    resp = llm.invoke(prompt)
    script = _parse_json(resp.content)
    return {"script": script, "current_phase": "intro"}


def dm_intro_node(state: dict) -> dict:
    """节点 2：DM 开场介绍，然后把阶段推进到讨论"""
    script = state.get("script", {})
    background = script.get("background", script.get("raw", ""))
    suspects = script.get("suspects", [])
    clues = script.get("clues", [])

    prompt = f"""你是一位剧本杀主持人（DM）。现在进入【开场】阶段。

案件背景：{background}
登场嫌疑人：{suspects}
可公开线索：{clues}

请用主持人的口吻：
1. 宣布案件发生，介绍背景和嫌疑人
2. 公布可公开的线索
3. 宣布进入自由讨论，规则是嫌疑人轮流发言

只输出主持台词本身，不要额外解释。"""

    resp = llm.invoke(prompt)
    return {
        "messages": [{"speaker": "主持人", "content": resp.content}],
        "current_phase": "discuss",
        "phase_round": 0,   # 讨论从第 0 轮开始计数
    }


def ai_player_turn_node(state: dict) -> dict:
    """节点 3：AI 玩家发言（think/speak 双通道）"""
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        # 剧本没生成出嫌疑人时兜底：直接推进轮次，避免死循环
        return {"phase_round": state.get("phase_round", 0) + 1}

    round_num = state.get("phase_round", 0)
    # 轮流发言：第 round_num 轮由第 (round_num % 人数) 个嫌疑人发言
    speaker = suspects[round_num % len(suspects)]
    name = speaker.get("name", "嫌疑人")
    secret = speaker.get("secret", "无")

    # 把最近几条公开对话拼进 prompt，让 AI 能接上上下文
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-6:]
    )

    prompt = f"""你正在扮演剧本杀角色「{name}」。

你的秘密（只能你自己知道，绝不能在发言中直接承认）：{secret}

最近对话：
{history if history else "（还没有人发言）"}

请以「{name}」的口吻，输出 JSON：
{{
  "think": "你的内心推理（不公开）：你在隐瞒什么、怀疑谁、想引导什么",
  "speak": "你公开说的话（1~3 句，符合人设）"
}}"""

    resp = llm.invoke(prompt)
    out = _parse_json(resp.content)
    think = out.get("think", "")
    speak = out.get("speak", out.get("raw", "……"))

    return {
        "messages": [{"speaker": name, "content": speak}],   # 公开台词，进对话历史
        "thoughts": [f"{name}（内心）: {think}"],             # 内心戏，不公开
        "phase_round": round_num + 1,                        # 轮次 +1
    }


def ai_vote_node(state: dict) -> dict:
    """节点 4：AI 玩家投票指认凶手（LLM 节点，新增）"""
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    suspect_names = [s.get("name", "?") for s in suspects]

    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-10:]
    )

    prompt = f"""讨论已经结束，现在进入【投票】环节。

嫌疑人名单：{suspect_names}

讨论记录：
{history}

请每位嫌疑人根据讨论内容，投票指认自己认为的凶手。严格输出 JSON：
{{
  "votes": {{"林晚": "王教授", "周野": "王教授", "苏晴": "林晚"}}
}}

要求：
1. votes 的键是每位嫌疑人的名字，值是他/她投的人
2. 不能投自己，被投者必须是嫌疑人名单里的人"""

    resp = llm.invoke(prompt)
    out = _parse_json(resp.content)
    votes = out.get("votes", {})
    if not isinstance(votes, dict):
        votes = {}

    # 兜底：过滤掉"投自己"或"投名单外的人"的无效票
    valid = {k: v for k, v in votes.items() if v in suspect_names and k != v}
    return {"votes": valid, "current_phase": "vote"}


def tally_node(state: dict) -> dict:
    """节点 5：统计票数（确定性节点，纯 Python 逻辑，不调 LLM，新增）

    为什么用确定性节点而不是让 LLM 统计？
    因为"数票数"是精确计算，LLM 会数错、会编造；而 Counter 又快又准又零成本。
    """
    votes = state.get("votes", {})
    counter = Counter(votes.values())
    vote_counts = dict(counter)
    # 得票最多的人；平票时取第一个（后续可优化为"平票重投"）
    vote_winner = counter.most_common(1)[0][0] if counter else "无人投票"
    return {"vote_counts": vote_counts, "vote_winner": vote_winner}


def dm_reveal_node(state: dict) -> dict:
    """节点 6：DM 揭晓真相 + 对比投票结果"""
    script = state.get("script", {})
    truth = script.get("truth", "")
    vote_counts = state.get("vote_counts", {})
    vote_winner = state.get("vote_winner", "无人")

    prompt = f"""你是一位剧本杀主持人（DM）。讨论和投票都结束了，现在进入【揭晓真相】阶段。

案件真相：{truth}
投票结果：{vote_counts}（得票最多的是：{vote_winner}）

请用主持人的口吻，依次：
1. 公布投票结果（谁投了谁、谁得票最多）
2. 揭晓真相（凶手、动机、手法）
3. 对比投票和真相：多数人投对了吗？点出投对和投错的玩家
4. 为整场游戏收尾"""

    resp = llm.invoke(prompt)
    return {
        "messages": [{"speaker": "主持人", "content": resp.content}],
        "current_phase": "reveal",
    }


def should_continue(state: dict) -> str:
    """条件边的路由函数：根据轮次决定讨论循环继续还是收尾。"""
    if state.get("phase_round", 0) >= MAX_ROUNDS:
        return "vote"        # 轮次已满 -> 进入投票
    return "continue"        # 还没聊够 -> 继续循环


if __name__ == "__main__":
    # 单文件自测：先看剧本生成
    print(generate_script_node({"theme": "校园密室"}))

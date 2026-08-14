"""
节点函数 —— LangGraph 里的"演员"

Phase 3.5 节点清单（用户参与）：
1. generate_script_node ：生成剧本 + 指定用户角色（LLM）
2. dm_intro_node       ：DM 开场介绍（LLM）
3. ai_player_turn_node ：AI 玩家发言，think/speak 双通道（LLM）
4. human_turn_node     ：轮到用户发言（interrupt 暂停等输入，新增）
5. ai_vote_node        ：AI 玩家投票（LLM）
6. human_vote_node     ：用户投票（interrupt 暂停等输入，新增）
7. tally_node          ：统计票数（确定性节点，不调 LLM）
8. dm_reveal_node      ：DM 揭晓真相 + 对比投票（LLM）
9. route_speaker       ：条件边路由（判断下一个发言者是谁）
"""
import os
import json
import re
from collections import Counter
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek
from langgraph.types import interrupt
import httpx

load_dotenv()

# 显式创建"不走代理"的 HTTP 客户端。
# streamlit 进程可能读到了代理环境变量（与普通终端不同），导致 SDK 走坏代理报 Connection error。
# trust_env=False 强制直连，不读 HTTP_PROXY / HTTPS_PROXY 等环境变量。
http_client = httpx.Client(trust_env=False, timeout=120)

llm = ChatDeepSeek(
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    temperature=0.8,
    max_tokens=4096,
    reasoning_effort="none",   # 关键：关闭 v4 默认的 thinking 模式，让答案直接进 content
    timeout=120,               # 请求超时 120 秒（网络慢时别急着放弃）
    max_retries=5,             # 失败自动重试 5 次（SSL 握手偶发失败，多试几次提高成功率）
    http_client=http_client,   # 用不走代理的客户端，规避 streamlit 环境的代理问题
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
    """节点 1：生成结构化剧本，并把第一个嫌疑人指定为用户角色"""
    theme = state.get("theme", "民国豪门恩怨")
    background = state.get("background_style", "自由发挥")

    prompt = f"""你是一名资深剧本杀编剧。

创作主题：{theme}
背景风格：{background}

请围绕这个主题、在这个背景风格下，创作一个完整的剧本杀剧本。

严格输出 JSON 格式，不要输出任何 JSON 以外的文字。字段如下：
{{
  "background": "案件背景故事，约100字",
  "suspects": [
    {{"name": "嫌疑人名字", "secret": "这个人隐藏的秘密", "forbidden": ["这个人绝对不能公开说出的关键词，2~4个"]}}
  ],
  "clues": ["线索1", "线索2", "线索3", "线索4", "线索5"],
  "truth": "案件真相：凶手是谁、动机、作案手法"
}}"""

    resp = llm.invoke(prompt)
    script = _parse_json(resp.content)
    # 用户扮演第一个嫌疑人
    suspects = script.get("suspects", [])
    user_role = suspects[0].get("name", "玩家") if suspects else "玩家"
    return {"script": script, "current_phase": "intro", "user_role": user_role}


def dm_intro_node(state: dict) -> dict:
    """节点 2：DM 开场介绍，然后把阶段推进到讨论"""
    script = state.get("script", {})
    background = script.get("background", script.get("raw", ""))
    suspects = script.get("suspects", [])
    clues = script.get("clues", [])
    user_role = state.get("user_role", "")

    prompt = f"""你是一位剧本杀主持人（DM）。现在进入【开场】阶段。

案件背景：{background}
登场嫌疑人：{suspects}
可公开线索：{clues}

请用主持人的口吻：
1. 宣布案件发生，介绍背景和嫌疑人
2. 公布可公开的线索
3. 宣布进入自由讨论，规则是嫌疑人轮流发言

注意：{user_role} 是真人玩家扮演的，介绍时正常介绍即可。

只输出主持台词本身，不要额外解释。"""

    resp = llm.invoke(prompt)
    return {
        "messages": [{"speaker": "主持人", "content": resp.content}],
        "current_phase": "discuss",
        "phase_round": 0,   # 讨论从第 0 轮开始计数
    }


def ai_player_turn_node(state: dict) -> dict:
    """节点 3：AI 玩家发言（think/speak 双通道 + 防泄露重试）"""
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return {"phase_round": state.get("phase_round", 0) + 1}

    round_num = state.get("phase_round", 0)
    speaker = suspects[round_num % len(suspects)]
    name = speaker.get("name", "嫌疑人")
    secret = speaker.get("secret", "无")
    forbidden = speaker.get("forbidden", [])   # 禁忌词：绝对不能公开说

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

    # 防跑飞（确定性校验 + 重试）：
    # LLM 负责"生成发言"，确定性逻辑负责"检查发言有没有泄露秘密"。
    # 如果发言里出现了禁忌词（泄露），就在 prompt 里加强约束、让模型重说。
    think, speak = "", "……"
    for attempt in range(3):   # 最多重试 3 次，避免死循环
        resp = llm.invoke(prompt)
        out = _parse_json(resp.content)
        think = out.get("think", "")
        speak = out.get("speak", out.get("raw", "……"))

        # 确定性校验：发言里是否出现了禁忌词（纯 Python 的字符串匹配，不调 LLM）
        leaked = [w for w in forbidden if w and w in speak]
        if not leaked:
            break   # 没泄露，接受这次发言
        # 泄露了：把"你刚才说漏嘴了"这件事告诉模型，让它重说
        prompt += f"\n\n⚠️ 你刚才的发言泄露了秘密（提到了：{'、'.join(leaked)}），请重新组织语言，绝不能再提这些词。"

    return {
        "messages": [{"speaker": name, "content": speak}],
        "thoughts": [f"{name}（内心）: {think}"],
        "phase_round": round_num + 1,
    }


def human_turn_node(state: dict) -> dict:
    """节点 4：轮到用户发言（interrupt 暂停，等用户在终端输入）

    interrupt() 会在这里"冻结"图，把控制权交还给 main.py，
    等 main.py 用 Command(resume=用户输入) 恢复时，interrupt() 返回用户输入的内容。
    """
    user_role = state.get("user_role", "你")
    round_num = state.get("phase_round", 0)
    # 暂停，把提示信息传给 main.py
    user_input = interrupt({"type": "human_turn", "speaker": user_role})
    return {
        "messages": [{"speaker": user_role, "content": user_input}],
        "phase_round": round_num + 1,
    }


def ai_vote_node(state: dict) -> dict:
    """节点 5：AI 玩家投票（排除用户角色，用户单独在 human_vote 里投）"""
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    user_role = state.get("user_role", "")
    suspect_names = [s.get("name", "?") for s in suspects]
    # AI 玩家 = 排除用户扮演的角色
    ai_names = [n for n in suspect_names if n != user_role]

    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-10:]
    )

    prompt = f"""讨论已经结束，现在进入【投票】环节。

嫌疑人名单：{suspect_names}
需要投票的 AI 玩家：{ai_names}

讨论记录：
{history}

请每位 AI 玩家根据讨论内容，投票指认自己认为的凶手。严格输出 JSON：
{{
  "votes": {{"周野": "王教授", "苏晴": "林晚"}}
}}

要求：
1. votes 的键是 AI 玩家名单里的人，值是他/她投的人
2. 不能投自己，被投者必须是嫌疑人名单里的人"""

    resp = llm.invoke(prompt)
    out = _parse_json(resp.content)
    votes = out.get("votes", {})
    if not isinstance(votes, dict):
        votes = {}

    # 兜底：过滤掉"投自己"或"投名单外的人"的无效票
    valid = {k: v for k, v in votes.items() if k in ai_names and v in suspect_names and k != v}
    return {"votes": valid, "current_phase": "vote"}


def human_vote_node(state: dict) -> dict:
    """节点 6：用户投票（interrupt 暂停，等用户输入）

    注意：votes 是普通 dict（没有 reducer），所以这里要把用户的票
    "合并"进已有的 AI 投票里，而不是直接覆盖。
    """
    user_role = state.get("user_role", "你")
    script = state.get("script", {})
    suspect_names = [s.get("name", "?") for s in script.get("suspects", [])]

    user_vote = interrupt({"type": "human_vote", "suspects": suspect_names})

    # 拷贝现有投票（AI 玩家投的），再加上用户这一票
    votes = dict(state.get("votes", {}))
    votes[user_role] = user_vote
    return {"votes": votes}


def tally_node(state: dict) -> dict:
    """节点 7：统计票数（确定性节点，纯 Python 逻辑，不调 LLM）"""
    votes = state.get("votes", {})
    counter = Counter(votes.values())
    vote_counts = dict(counter)
    vote_winner = counter.most_common(1)[0][0] if counter else "无人投票"
    return {"vote_counts": vote_counts, "vote_winner": vote_winner}


def dm_reveal_node(state: dict) -> dict:
    """节点 8：DM 揭晓真相 + 对比投票结果"""
    script = state.get("script", {})
    truth = script.get("truth", "")
    votes = state.get("votes", {})
    vote_counts = state.get("vote_counts", {})
    vote_winner = state.get("vote_winner", "无人")

    # 完整投票明细（谁投了谁），让 DM 照实公布，而不是自己编
    votes_text = "、".join(f"{k}投{v}" for k, v in votes.items()) or "无人投票"

    prompt = f"""你是一位剧本杀主持人（DM）。讨论和投票都结束了，现在进入【揭晓真相】阶段。

案件真相：{truth}
投票明细（谁投了谁，务必照实公布，禁止编造）：{votes_text}
票数统计：{vote_counts}（得票最多的是：{vote_winner}）

请用主持人的口吻，依次：
1. 照实公布投票明细（谁投了谁、谁得票最多）
2. 揭晓真相（凶手、动机、手法）
3. 对比投票和真相：多数人投对了吗？点出投对和投错的玩家
4. 为整场游戏收尾"""

    resp = llm.invoke(prompt)
    return {
        "messages": [{"speaker": "主持人", "content": resp.content}],
        "current_phase": "reveal",
    }


def route_speaker(state: dict) -> str:
    """条件边的路由函数：根据当前轮次决定"下一个谁发言"。

    返回 "human"（轮到用户）/ "ai"（轮到 AI）/ "vote"（进入投票）。
    """
    round_num = state.get("phase_round", 0)
    if round_num >= MAX_ROUNDS:
        return "vote"

    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return "vote"

    speaker = suspects[round_num % len(suspects)]
    if speaker.get("name") == state.get("user_role", ""):
        return "human"   # 轮到用户发言
    return "ai"          # 轮到 AI 发言


if __name__ == "__main__":
    print(generate_script_node({"theme": "校园密室"}))

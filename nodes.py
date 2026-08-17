"""
节点函数 —— LangGraph 里的"演员"

Phase 5 节点清单（用户选择角色）：
1. generate_script_node ：生成剧本 + 线索（LLM）
1.5 distribute_clues_node：线索分发，信息差（确定性节点，不调 LLM）
1.6 choose_role_node    ：用户选择扮演的角色（interrupt 暂停等选择）
2. dm_intro_node       ：DM 开场介绍（LLM）
3. ai_player_turn_node ：AI 玩家发言，think/speak 双通道（LLM）
4. human_turn_node     ：轮到用户发言（interrupt 暂停等输入）
5. ai_vote_node        ：AI 玩家投票（LLM）
6. human_vote_node     ：用户投票（interrupt 暂停等输入）
7. tally_node          ：统计票数（确定性节点，不调 LLM）
8. dm_reveal_node      ：DM 揭晓真相 + 对比投票（LLM）
9. route_speaker       ：条件边路由（判断下一个发言者是谁）

模块拆分（曾是 700+ 行的 God module）：
- names.py      名字池 + 抽样（_parse_names / _pick_suspect_names）
- prompts.py    prompt 构建（_build_script_prompt）
- validators.py 解析 / 规范化 / 兜底（_parse_json / _enforce_names / _normalize_secrets / _fallback_clues / _extract_speak）
- 本文件        纯节点编排 + llm 配置

这些被移走的函数通过下面的 import 重新暴露在 nodes 命名空间里，
所以 app.py / main.py 里的 `from nodes import _parse_names` 等写法无需改动。
"""
import os
import logging
from collections import Counter
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek
from langgraph.types import interrupt
import httpx

from game_state import GameState
from names import _parse_names, _pick_suspect_names
from validators import _parse_json, _enforce_names, _normalize_secrets, _fallback_clues, _extract_speak
from prompts import _build_script_prompt

load_dotenv()

logger = logging.getLogger(__name__)

# 显式创建"不走代理"的 HTTP 客户端。
# streamlit 进程可能读到了代理环境变量（与普通终端不同），导致 SDK 走坏代理报 Connection error。
# trust_env=False 强制直连，不读 HTTP_PROXY / HTTPS_PROXY 等环境变量。
http_client = httpx.Client(trust_env=False, timeout=120)

# LLM 客户端懒加载：不再在 import 时初始化。
# 之前 import nodes 就创建 ChatDeepSeek，若 .env 缺失 / key 为空，
# 导入阶段就可能抛异常，导致整个模块（乃至纯函数测试）无法 import。
# 改成首次调用 get_llm() 时才初始化，纯函数测试不碰 LLM 就不会失败。
_llm = None


def get_llm():
    global _llm
    if _llm is None:
        _llm = ChatDeepSeek(
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
    return _llm

# 每个玩家至少发言几轮（讨论总轮数 = 嫌疑人数量 × 这个值）。
# 之前 MAX_ROUNDS 写死 6，嫌疑人随机 4~6 人时，玩家可能只轮到 1 次就投票。
# 现在每人至少发言 3 次，剧情流转更充分、更有可玩性（想更快就改小）。
ROUNDS_PER_PLAYER = 3


def _stream_full_text(prompt: str, *, allow_partial: bool = True) -> str:
    """流式调用 LLM 并累积成完整文本。

    节点内部改用 llm.stream()（而不是 llm.invoke()），这样 LangGraph 的
    stream_mode="messages" 才能捕获每个 token 并实时推给前端（真流式）。
    这里把 token 累积成完整文本返回，节点照常拿完整文本做后续解析。

    用 list.append + "".join() 累积而非 `full += token`：Python 字符串不可变，
    每次 += 都要复制已有全部内容，是 O(n²)；append 是摊还 O(1)，整体 O(n)。

    异常处理：llm.stream() 是网络 IO，超时/限流/断连是常态而非意外。
    捕获异常后，若 allow_partial 且已累积了内容，返回部分文本让上层降级处理；
    否则重新抛出，让上层真正感知失败。
    """
    parts = []
    try:
        for chunk in get_llm().stream(prompt):
            if chunk.content:
                parts.append(chunk.content)
    except Exception as e:
        logger.warning("LLM stream 中断（%s），已累积 %d 字符", type(e).__name__, sum(len(p) for p in parts))
        if not allow_partial or not parts:
            raise
    return "".join(parts)


def generate_script_node(state: GameState) -> dict:
    """节点 1：生成结构化剧本（不再指定用户角色，改由 choose_role_node 让用户选）

    两个入口：
    - 如果 state 里已经有 script（前端用流式预生成好了），直接跳过，不重复生成。
    - 否则用 llm.invoke 兜底生成（终端版 main.py 走这条路）。
    """
    if state.get("script"):
        return {"current_phase": "intro"}   # 剧本已预生成，跳过

    theme = state.get("theme", "民国豪门恩怨")
    background = state.get("background_style", "自由发挥")
    background_story = state.get("background_story", "")
    story_time = state.get("story_time", "")
    story_location = state.get("story_location", "")
    custom_names = state.get("custom_names")

    # 先由确定性逻辑选好嫌疑人名字（自定义优先，否则随机），再让 LLM 编故事
    names = _pick_suspect_names(background, custom_names)
    prompt = _build_script_prompt(theme, background, names, background_story, story_time, story_location)

    resp = get_llm().invoke(prompt)
    script = _parse_json(resp.content)
    script = _fallback_clues(script)        # 兜底：LLM 没按新格式给线索时，从旧格式抢救
    script = _enforce_names(script, names)  # 兜底：强制剧本名字 = 我们抽的名字
    script = _normalize_secrets(script)     # 兜底：secret 强制第一人称开头
    return {"script": script, "current_phase": "intro"}


def generate_script_stream(theme: str, background: str, background_story: str = "", custom_names: list[str] | None = None, story_time: str = "", story_location: str = ""):
    """真流式生成剧本（生成器函数）。

    - yield：每个 token 片段（前端用它实时显示"剧本正在生成"）
    - return：解析 + 兜底后的完整 script dict

    和 generate_script_node 的区别：这里用 llm.stream()（逐段吐 token），
    节点里用 llm.invoke()（一次性返回完整结果）。这是"真流式"——
    LLM 边生成边把 token 推出来，而不是等生成完再返回。

    注意生成器的 return 值：for 循环拿不到它，要手动 next() 迭代，
    从 StopIteration.value 里取。这是"生成器既流式产出中间结果、
    又能携带最终结果"的惯用法。
    """
    names = _pick_suspect_names(background, custom_names)
    prompt = _build_script_prompt(theme, background, names, background_story, story_time, story_location)

    # 用 list 累积 token，最后 join（O(n)），避免 `full += token` 的 O(n²) 复制
    parts = []
    for chunk in get_llm().stream(prompt):
        token = chunk.content or ""
        if token:
            parts.append(token)
            yield token

    # 流式结束后，用累积的完整文本做解析 + 兜底（和节点里的后处理完全一致）
    script = _parse_json("".join(parts))
    script = _fallback_clues(script)
    script = _enforce_names(script, names)
    script = _normalize_secrets(script)   # 兜底：secret 强制第一人称开头
    return script


def distribute_clues_node(state: GameState) -> dict:
    """节点 1.5：线索分发（信息差的核心，确定性节点，不调 LLM）

    把剧本里的私密线索按 holder 归位到每个角色手里，形成信息差：
    - 每个角色只"看得见"自己持有的线索
    - 凶手手握护身符，侦探手握指向真凶的拼图
    - 真相散落在不同人手里，必须靠讨论互相盘问才能拼出全貌

    为什么这里用纯 Python 而不是 LLM？
    因为"分发"本质是机械的归位动作（把 holder 字符串匹配到角色名），
    LLM 反而可能分错、丢线索。信息差由"编剧在设计线索时定好归属"决定，
    "执行分发"用确定性逻辑最可靠——这和 tally_node 数票是同一个道理。
    """
    script = state.get("script", {})
    suspect_names = [s.get("name", "") for s in (script.get("suspects") or []) if s.get("name")]

    private = script.get("private_clues") or []

    # 初始：每个嫌疑人一条线索都没有
    distributed = {name: [] for name in suspect_names}

    # 第一遍：holder 合法的私密线索，归位到对应角色
    unassigned = []
    for clue in private:
        if not isinstance(clue, dict):
            continue
        content = clue.get("content", "")
        holder = clue.get("holder", "")
        if content and holder in distributed:
            distributed[holder].append(content)
        elif content:
            unassigned.append(content)   # holder 写错 / 缺失，先收集起来

    # 兜底：holder 对不上嫌疑人名单的线索，负载均衡分配——
    # 每条都分给"当前持有线索最少"的角色，保证不丢线索、也不偏爱任何人。
    if unassigned and suspect_names:
        for content in unassigned:
            target = min(suspect_names, key=lambda n: len(distributed[n]))
            distributed[target].append(content)

    return {
        "distributed_clues": distributed,
    }


def choose_role_node(state: GameState) -> dict:
    """节点 1.6：用户选择扮演的角色（interrupt 暂停，等用户选）

    之前 user_role 固定在剧本生成时指定为第一个嫌疑人，
    现在改成开局让用户从嫌疑人名单里自由挑选，更接近真实剧本杀"选本"体验。

    复用 interrupt 机制：在这里暂停，把嫌疑人名单传给前端，
    前端让用户挑一个，再用 Command(resume=角色名) 恢复，这里收到角色名。
    """
    script = state.get("script", {})
    suspects = script.get("suspects") or []
    names = [s.get("name", "?") for s in suspects]

    if not names:
        return {"user_role": "玩家"}   # 剧本没生成出嫌疑人（生成失败），兜底

    chosen = interrupt({"type": "choose_role", "suspects": names})

    # 确定性校验：用户选的名字必须在名单里，否则兜底到第一个。
    # 不让非法输入污染后续的 route_speaker / human_turn 逻辑。
    if chosen not in names:
        chosen = names[0]
    return {"user_role": chosen}


def dm_intro_node(state: GameState) -> dict:
    """节点 2：DM 开场介绍（只公布公共线索，私密线索各角色私下掌握）"""
    script = state.get("script", {})
    background = script.get("background", script.get("raw", ""))
    suspects = script.get("suspects", [])
    # 只把嫌疑人"名字"传给 DM，绝不给 secret/forbidden——
    # 否则 prompt 里"你也不知道私密线索"就和传入内容自相矛盾，DM 会在开场白剧透。
    names = [s.get("name", "?") for s in suspects]
    public_clues = script.get("public_clues", [])
    user_role = state.get("user_role", "")

    prompt = f"""你是一位剧本杀主持人（DM）。现在进入【开场】阶段。

案件背景：{background}
登场嫌疑人：{names}
可公开线索（这些可以当众公布）：{public_clues}

【重要规则】这是一局"信息不对称"的剧本杀：
- 每个嫌疑人私下都握有只属于自己的私密线索（已悄悄发到各自手里，不在这里列出，你也不知道具体内容）。
- 你在开场时只能公布上面的"可公开线索"，绝不能编造或公布私密线索。
- 你要引导玩家：真相散落在不同人手里，需要大家讨论、互相盘问才能拼出全貌。

请用主持人的口吻，把案件背景像讲故事一样娓娓道来（自然融入，不要照念、不要机械罗列），依次：
1. 用一段有画面感的开场，把案件背景和嫌疑人自然引出来
2. 公布可公开线索
3. 说明"每人手中握有私密线索"，鼓励玩家互相套话
4. 自然过渡到自由讨论，规则是嫌疑人轮流发言

注意：{user_role} 是真人玩家扮演的，介绍时正常介绍即可。

只输出主持台词本身，不要额外解释。"""

    resp = _stream_full_text(prompt)
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "discuss",
        "phase_round": 0,   # 讨论从第 0 轮开始计数
    }


def ai_player_turn_node(state: GameState) -> dict:
    """节点 3：AI 玩家发言（think/speak 双通道 + 防泄露重试 + 确定性脱敏兜底）"""
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return {"phase_round": state.get("phase_round", 0) + 1}

    round_num = state.get("phase_round", 0)
    speaker = _current_speaker(state)
    name = speaker.get("name", "嫌疑人")
    secret = speaker.get("secret", "无")
    forbidden = speaker.get("forbidden", [])   # 禁忌词：绝对不能公开说

    # 信息差：只把"这个角色自己持有的私密线索"告诉它，别的角色有什么它不知道
    own_clues = state.get("distributed_clues", {}).get(name, [])
    clues_text = "\n".join(f"- {c}" for c in own_clues) if own_clues else "（你没有额外的私密线索）"

    # 历史窗口动态化：至少保留最近 6 条；嫌疑人多时保留两轮完整讨论，
    # 避免 AI 忘掉两轮前别人说过的话（之前写死 [-6:]，12~18 条消息只看到 6 条）
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-max(6, len(suspects) * 2):]
    )

    prompt = f"""你正在扮演剧本杀角色「{name}」。

你的秘密（只能你自己知道，绝不能在发言中直接承认）：{secret}

你手里握有的私密线索（只有你知道；是否公开、公开多少、如何曲解，都由你决定）：
{clues_text}

最近对话：
{history if history else "（还没有人发言）"}

请以「{name}」的口吻，输出 JSON：
{{
  "think": "你的内心推理（不公开）：你在隐瞒什么、怀疑谁、想引导什么、手里的线索指向谁",
  "speak": "你公开说的话（1~3 句，符合人设。可选择性抛出部分线索引导他人，也可隐瞒）
}}"""

    # 防跑飞（确定性校验 + 重试）：
    # LLM 负责"生成发言"，确定性逻辑负责"检查发言有没有泄露秘密"。
    # 如果发言里出现了禁忌词（泄露），就在 prompt 里加强约束、让模型重说。
    # feedback 单独维护、不追加到原 prompt，避免 prompt 随重试次数不断增长。
    think, speak = "", "……"
    feedback = ""
    for attempt in range(3):   # 最多重试 3 次，避免死循环
        out = _parse_json(_stream_full_text(prompt + feedback))
        think = out.get("think", "")
        speak = _extract_speak(out)   # 关键：解析失败时用正则抢救，绝不用 raw（会泄露 think）

        # 确定性校验：发言里是否出现了禁忌词（纯 Python 的字符串匹配，不调 LLM）
        leaked = [w for w in forbidden if w and w in speak]
        if not leaked:
            break   # 没泄露，接受这次发言
        # 泄露了：把"你刚才说漏嘴了"这件事告诉模型，让它重说（只保留最新一条反馈）
        feedback = f"\n\n⚠️ 你刚才的发言泄露了秘密（提到了：{'、'.join(leaked)}），请重新组织语言，绝不能再提这些词。"

    # 兜底：重试耗尽后仍泄露，做确定性脱敏（把禁忌词替换成 □），保证绝不泄露
    for w in forbidden:
        if w and w in speak:
            speak = speak.replace(w, "□")

    return {
        "messages": [{"speaker": name, "content": speak}],
        "thoughts": [f"{name}（内心）: {think}"],
        "phase_round": round_num + 1,
    }


def human_turn_node(state: GameState) -> dict:
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


def _vote_one_player(name: str, secret: str, own_clues: list, history: str, suspect_names: list[str]) -> tuple[str, str | None]:
    """单个 AI 玩家投票：基于自己的秘密 + 私密线索 + 公开历史。

    返回 (名字, 投票目标) 或 (名字, None) 表示弃权。单个人调用失败不影响其他人。
    """
    clues_text = "\n".join(f"- {c}" for c in own_clues) if own_clues else "（无）"
    prompt = f"""你是剧本杀角色「{name}」，现在进入投票环节。

你的秘密（你自己知道）：{secret}

你手里握有的私密线索（只有你知道）：
{clues_text}

讨论记录：
{history}

嫌疑人名单：{suspect_names}

请根据讨论和你手里的线索，投票指认你认为的凶手。严格输出 JSON：
{{"vote": "嫌疑人名字"}}

要求：不能投自己，被投者必须在嫌疑人名单里。"""
    try:
        out = _parse_json(_stream_full_text(prompt))
        vote = out.get("vote", "")
        if vote in suspect_names and vote != name:
            return name, vote
    except Exception as e:
        logger.warning("AI 玩家 %s 投票失败（%s），算弃权", name, type(e).__name__)
    return name, None


def ai_vote_node(state: GameState) -> dict:
    """节点 5：AI 玩家投票（逐人投票，信息差闭环）

    之前所有 AI 在"一次 LLM 调用"里集体投票，prompt 不含各人私密线索，
    信息差在投票环节崩塌；且一个格式错误会丢弃全部票。现在改为逐人投票：
    - 每个 AI 单独构建含"自己秘密 + 私密线索 + 公开历史"的 prompt
    - 逐个调用，单个人失败不影响其他人（失败算弃权）
    - 最后合并有效票

    串行调用 n 次（n≈4~5）；如需加速可改用 asyncio 并发（见第二轮报告优化点 3），
    但当前 llm 用的是同步 http_client，先保持串行稳妥。
    """
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    user_role = state.get("user_role", "")
    suspect_names = [s.get("name", "?") for s in suspects]
    # AI 玩家 = 排除用户扮演的角色
    ai_names = [n for n in suspect_names if n != user_role]

    distributed = state.get("distributed_clues", {})
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-10:]
    )

    votes = {}
    for name in ai_names:
        secret = next((s.get("secret", "") for s in suspects if s.get("name") == name), "")
        own_clues = distributed.get(name, [])
        voter, target = _vote_one_player(name, secret, own_clues, history, suspect_names)
        if target:
            votes[voter] = target

    return {"votes": votes, "current_phase": "vote"}


def human_vote_node(state: GameState) -> dict:
    """节点 6：用户投票（interrupt 暂停，等用户输入）

    兜底校验：resume 值必须是合法嫌疑人名字，否则丢弃（算弃权）。
    防止前端 chat_input 值残留把"发言文本"当成投票传进来，污染 votes——
    这正是"输入的消息变成投票结果"这个 bug 的根源。

    votes 用合并 reducer（见 game_state._merge_dict），这里直接返回自己这一票，
    框架会自动和 ai_vote_node 的票合并，无需手动拷贝现有投票。
    """
    user_role = state.get("user_role", "你")
    script = state.get("script", {})
    suspect_names = [s.get("name", "?") for s in script.get("suspects", [])]

    user_vote = interrupt({"type": "human_vote", "suspects": suspect_names})

    # 只有合法嫌疑人名字才记录，非法值（比如残留的发言文本）直接丢弃 = 弃权
    if user_vote in suspect_names:
        return {"votes": {user_role: user_vote}}
    return {}


def tally_node(state: GameState) -> dict:
    """节点 7：统计票数（确定性节点，纯 Python 逻辑，不调 LLM）

    平票处理：之前 most_common(1) 在平票时按插入顺序任取一个当"得票最多"，
    DM 会给出误导性结论。现在检测平票，vote_winner 设为"平票（A、B）"，
    dm_reveal 的 prompt 据此说明。
    """
    votes = state.get("votes", {})
    counter = Counter(votes.values())
    vote_counts = dict(counter)
    if not counter:
        return {"vote_counts": {}, "vote_winner": "无人投票"}

    top = counter.most_common()
    max_n = top[0][1]
    winners = [k for k, v in top if v == max_n]
    vote_winner = "平票（" + "、".join(winners) + "）" if len(winners) > 1 else winners[0]
    return {"vote_counts": vote_counts, "vote_winner": vote_winner}


def _format_private_clues(distributed_clues: dict) -> str:
    """把 {角色名: [私密线索...]} 格式化成"谁持有哪条线索"的文本清单。

    这是确定性格式化（纯字符串拼接），供 DM 揭晓时做"线索复盘"用。
    判断"哪些线索被埋没"的语义工作交给 LLM，但整理线索总账用代码最可靠——
    这和 tally 数票、线索分发是同一个分层思路。
    """
    lines = []
    for holder, clues in distributed_clues.items():
        for c in clues:
            lines.append(f"- {holder}：{c}")
    return "\n".join(lines) if lines else "（没有私密线索）"


def dm_reveal_node(state: GameState) -> dict:
    """节点 8：DM 揭晓真相 + 对比投票结果 + 线索复盘（信息差闭环）"""
    script = state.get("script", {})
    truth = script.get("truth", "")
    votes = state.get("votes", {})
    vote_counts = state.get("vote_counts", {})
    vote_winner = state.get("vote_winner", "无人")

    # 平票说明：vote_winner 是"平票（A、B）"时，明确告诉 DM 如实说明并列，别假装单一赢家
    winner_note = "（注意：这是平票，务必如实说明「多票并列」，不要假装有单一赢家）" if str(vote_winner).startswith("平票") else ""

    # 完整投票明细（谁投了谁），让 DM 照实公布，而不是自己编
    votes_text = "、".join(f"{k}投{v}" for k, v in votes.items()) or "无人投票"

    # 信息差闭环：把"私密线索总账"+"公开讨论记录"交给 LLM，让它复盘哪些线索被埋没
    clues_text = _format_private_clues(state.get("distributed_clues", {}))
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])
    )

    prompt = f"""你是一位剧本杀主持人（DM）。讨论和投票都结束了，现在进入【揭晓真相】阶段。

案件真相：{truth}
投票明细（谁投了谁，务必照实公布，禁止编造）：{votes_text}
票数统计：{vote_counts}（得票最多的是：{vote_winner}）{winner_note}

【私密线索总账】开局时每个玩家私下只握有这些线索（别人不知道）：
{clues_text}

【完整公开讨论记录】：
{history if history else "（无）"}

请用主持人的口吻，依次：
1. 照实公布投票明细（谁投了谁、谁得票最多）
2. 揭晓真相（凶手、动机、手法）
3. 对比投票和真相：多数人投对了吗？点出投对和投错的玩家
4. 【线索复盘】对照私密线索总账和公开讨论记录，指出哪些私密线索从头到尾没被任何人在讨论中提及（被埋没了），并简要说明这些线索若被挖出，对破案有什么帮助
5. 为整场游戏收尾"""

    resp = _stream_full_text(prompt)
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "reveal",
    }


def _current_speaker(state: GameState) -> dict:
    """根据当前轮次确定"这一轮轮到谁发言"（单一事实源）。

    之前 `suspects[round_num % len(suspects)]` 在 ai_player_turn_node 和
    route_speaker 里各写一遍，两处若改一处忘另一处，"路由判断"和"实际发言者"
    就会错位。现在统一从这里取，保证二者永远用同一个 speaker。

    调用前提：suspects 非空（调用方先判过空）。
    """
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    round_num = state.get("phase_round", 0)
    return suspects[round_num % len(suspects)]


def route_speaker(state: GameState) -> str:
    """条件边的路由函数：根据当前轮次决定"下一个谁发言"。

    返回 "human"（轮到用户）/ "ai"（轮到 AI）/ "vote"（进入投票）。
    """
    round_num = state.get("phase_round", 0)
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return "vote"

    # 讨论轮数动态：嫌疑人数量 × ROUNDS_PER_PLAYER，保证每个角色至少发言 2 次
    max_rounds = len(suspects) * ROUNDS_PER_PLAYER
    if round_num >= max_rounds:
        return "vote"

    speaker = _current_speaker(state)
    if speaker.get("name") == state.get("user_role", ""):
        return "human"   # 轮到用户发言
    return "ai"          # 轮到 AI 发言

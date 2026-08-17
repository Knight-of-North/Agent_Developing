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
"""
import os
import json
import re
import random
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

# 每个玩家至少发言几轮（讨论总轮数 = 嫌疑人数量 × 这个值）。
# 之前 MAX_ROUNDS 写死 6，嫌疑人随机 4~6 人时，玩家可能只轮到 1 次就投票，
# 体验像"刚发言就进投票"。改成动态后保证每个角色至少发言 2 次。
ROUNDS_PER_PLAYER = 2


# ---- 嫌疑人名字池（按背景风格分组）----
# 每组 16 个名字（男女混合），random.sample 从里面无放回抽样，
# 保证几乎每局的名字都不一样。
_NAME_POOLS = {
    "民国豪门": [
        "陆明轩", "顾则安", "沈长卿", "裴静山", "霍世昌", "宋怀远", "傅敬亭", "周既明",
        "沈碧如", "陆曼宁", "白素秋", "苏晚棠", "秦婉清", "顾念慈", "温若梅", "林淑仪",
    ],
    "校园怪谈": [
        "顾一舟", "林小北", "江叙", "许晏", "程野", "宋知夏", "周既白", "裴然",
        "林小满", "苏晚晴", "阮清", "叶听澜", "池念初", "温南乔", "纪云舒", "白露晞",
    ],
    "古风仙侠": [
        "萧暮云", "洛青崖", "沈星野", "慕寒", "顾长风", "谢流云", "楚怀瑾", "陆离",
        "洛清欢", "云知意", "苏挽月", "姜晚吟", "阮清歌", "白若溪", "温如故", "秦望舒",
    ],
    "现代都市": [
        "程亦辰", "陆则言", "沈默", "顾景行", "江叙白", "周叙", "许奕", "裴照",
        "苏念", "林晚意", "叶知秋", "池雨", "温以宁", "纪南乔", "白筱", "秦悦",
    ],
    "科幻末世": [
        "陆沉舟", "沈烬", "顾寒", "江澜", "宋曜", "周烬", "裴夜", "韩泽",
        "苏曜", "林烬", "叶澜", "池寒", "温澜", "纪星", "白月", "秦霜",
    ],
}

# "自由发挥"（以及任何没匹配上的背景）用所有池合并去重，名字风格最杂、随机性最大
_MIXED_POOL = []
for _pool in _NAME_POOLS.values():
    for _name in _pool:
        if _name not in _MIXED_POOL:
            _MIXED_POOL.append(_name)


def _parse_names(text: str) -> list:
    """解析用户输入的自定义名字列表（支持逗号/顿号/空格/换行分隔，去重保序）。"""
    if not text:
        return []
    text = re.sub(r"[，,、;\s]+", " ", text.strip())
    seen = set()
    result = []
    for name in text.split():
        name = name.strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def _pick_suspect_names(background: str, custom_names=None) -> list:
    """选嫌疑人名字：用户自定义优先，否则按背景风格随机抽（4~6 个，不重复）。

    为什么随机抽不用 LLM？LLM 的"随机"趋同（翻来覆去那几个高频名），
    random.sample 无放回抽样组合数巨大。但用户可能想用自己的朋友/同学名
    代入角色——这时自定义优先，随机兜底。
    """
    # 用户自定义名字：至少 3 个才采用，否则退回随机（名字太少撑不起剧本杀）
    if custom_names:
        cleaned = [n.strip() for n in custom_names if n and n.strip()]
        if len(cleaned) >= 3:
            return cleaned[:6]   # 最多 6 个嫌疑人

    pool = _NAME_POOLS.get(background, _MIXED_POOL)
    n = random.randint(4, 6)          # 嫌疑人数量也随机，增强可玩性
    n = min(n, len(pool))             # 名字池不够抽时退而求其次
    return random.sample(pool, n)


def _enforce_names(script: dict, names: list) -> dict:
    """兜底：强制剧本里的嫌疑人名字 = 我们抽的名字。

    名字是后续 choose_role / route_speaker / distribute_clues 的"主键"，
    LLM 可能不听话改了名字，必须改回来，否则选角色、轮流发言全乱。
    策略：尽量保留 LLM 给每个位置编的 secret / forbidden，只替换 name。
    """
    suspects = script.get("suspects") or []
    fixed = []
    for i, name in enumerate(names):
        if i < len(suspects) and isinstance(suspects[i], dict):
            s = dict(suspects[i])
            s["name"] = name           # 只改名字，保留它编的 secret / forbidden
        else:
            # LLM 少给了嫌疑人，补一个空壳
            s = {"name": name, "secret": "待补充", "forbidden": []}
        fixed.append(s)
    script["suspects"] = fixed
    return script


def _normalize_secret_first_person(secret: str) -> str:
    """兜底：把 secret 开头的第三人称代词替换为第一人称。

    LLM 偶尔偷懒用「他/她」写自己的秘密（应该是第一人称「我」），破坏代入感。
    只替换 secret 开头的代词（其余地方的代词可能指别人，不动）。
    """
    if not secret:
        return secret
    # 按长度从长到短匹配，避免「她的」被先匹配成「她」+「的」
    for old, new in [("她的", "我的"), ("他的", "我的"), ("她", "我"), ("他", "我")]:
        if secret.startswith(old):
            return new + secret[len(old):]
    return secret


def _normalize_secrets(script: dict) -> dict:
    """把所有 suspect 的 secret 规范化为第一人称开头。"""
    for s in script.get("suspects") or []:
        if isinstance(s, dict) and "secret" in s:
            s["secret"] = _normalize_secret_first_person(s["secret"])
    return script


def _parse_json(text: str) -> dict:
    """从模型输出里安全提取 JSON（容错去掉 ```json 标记）"""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _stream_full_text(prompt: str) -> str:
    """流式调用 LLM 并累积成完整文本。

    节点内部改用 llm.stream()（而不是 llm.invoke()），这样 LangGraph 的
    stream_mode="messages" 才能捕获每个 token 并实时推给前端（真流式）。
    这里把 token 累积成完整文本返回，节点照常拿完整文本做后续解析。
    """
    full = ""
    for chunk in llm.stream(prompt):
        token = chunk.content or ""
        if token:
            full += token
    return full


def _build_script_prompt(theme: str, background: str, names: list, background_story: str = "") -> str:
    """构建"生成剧本"的 prompt（节点生成 和 流式生成 共用，避免重复）。

    名字由 _pick_suspect_names 随机抽好、作为参数传进来，
    prompt 只负责"把给定的名字硬塞给 LLM，让它围绕这些名字编故事"。

    如果用户提供了自定义背景剧情（background_story），它就是创作的核心依据，
    优先级高于 theme + background；否则退回"主题 + 风格"自由发挥。
    """
    names_text = "、".join(names)

    if background_story and background_story.strip():
        # 用户自定义背景剧情：当作"素材种子"，让 LLM 扩写演化，而不是照抄
        context = f"""【创作灵感】玩家给了一段背景点子，请把它当作素材种子，用你自己的编剧语言扩写、演化成完整案件：
{background_story.strip()}

要求：不要照抄上面这段原文，而是把它自然融入、补上具体的时间地点、人物关系、
动机冲突、可疑之处，发展成一个浑然天成的案件背景。

背景风格参考：{background}"""
    else:
        context = f"""创作主题：{theme}
背景风格：{background}"""

    return f"""你是一名资深剧本杀编剧。

{context}

请据此创作一个完整的剧本杀剧本。

【嫌疑人名字已定，务必原样使用，不得改动、不得增减、不得替换】
嫌疑人共 {len(names)} 位，名字依次为：{names_text}

严格输出 JSON 格式，不要输出任何 JSON 以外的文字。字段如下：
{{
  "background": "扩写后的完整案件背景（约100-150字，自然流畅、有画面感；基于玩家的背景点子重新组织扩写，不要照抄原文）",
  "suspects": [
    {{"name": "必须依次使用上面给定的名字", "secret": "这个人的秘密（**必须用第一人称「我」开头**，例如「我暗恋宋知夏」「我曾偷看过考卷」，**禁止**用「他/她」开头——因为玩家会扮演这个角色，第三人称会破坏代入感）", "forbidden": ["这个人绝对不能公开说出的关键词，2~4个"]}}
  ],
  "public_clues": ["所有人都知道的公共线索，2~3条"],
  "private_clues": [
    {{"holder": "持有这条线索的嫌疑人名字（必须用上面给定的名字之一）", "content": "这条线索的具体内容，只有 holder 一个人知道"}}
  ],
  "truth": "案件真相：凶手是谁、动机、作案手法"
}}

【私密线索设计铁律（信息差是剧本杀的灵魂，务必遵守）】：
1. 每个嫌疑人都必须至少持有 1 条私密线索，凶手可以持有 2 条。
2. 凶手的私密线索必须对他/她有利（不在场证明、伪造证词、转移视线的伪证），帮他洗清嫌疑。
3. 其余角色（尤其接近真相的人）的私密线索要能指向真凶，但单看任何一条都不足以锁定，必须互相拼凑。
4. 不同角色的私密线索要能组合出完整真相：每个人手里只有一块拼图。
5. private_clues 里每个元素的 holder 必须精确等于 suspects 里的某个 name，不能写"某人""凶手"等模糊指代。"""


def generate_script_node(state: dict) -> dict:
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
    custom_names = state.get("custom_names")

    # 先由确定性逻辑选好嫌疑人名字（自定义优先，否则随机），再让 LLM 编故事
    names = _pick_suspect_names(background, custom_names)
    prompt = _build_script_prompt(theme, background, names, background_story)

    resp = llm.invoke(prompt)
    script = _parse_json(resp.content)
    script = _fallback_clues(script)        # 兜底：LLM 没按新格式给线索时，从旧格式抢救
    script = _enforce_names(script, names)  # 兜底：强制剧本名字 = 我们抽的名字
    script = _normalize_secrets(script)     # 兜底：secret 强制第一人称开头
    return {"script": script, "current_phase": "intro"}


def generate_script_stream(theme: str, background: str, background_story: str = "", custom_names=None):
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
    prompt = _build_script_prompt(theme, background, names, background_story)

    full = ""
    for chunk in llm.stream(prompt):
        token = chunk.content or ""
        if token:
            full += token
            yield token

    # 流式结束后，用累积的完整文本做解析 + 兜底（和节点里的后处理完全一致）
    script = _parse_json(full)
    script = _fallback_clues(script)
    script = _enforce_names(script, names)
    script = _normalize_secrets(script)   # 兜底：secret 强制第一人称开头
    return script


def _fallback_clues(script: dict) -> dict:
    """兜底：LLM 没按新格式（public_clues/private_clues）输出时，从旧 clues 字段抢救。

    这是"确定性兜底"思想：不指望 LLM 100% 听话，而是准备好它不听话时的退路。
    旧格式的 clues 是没标注持有者的纯字符串列表，这里把它切分：
    前 2 条当公共线索，剩下的轮转分配给各嫌疑人，保证信息差仍然成立。
    """
    if script.get("public_clues") or script.get("private_clues"):
        return script   # 已经是新格式，不用动

    suspects = script.get("suspects") or []
    names = [s.get("name") for s in suspects if s.get("name")]
    old_clues = script.get("clues") or []
    if not old_clues:
        return script   # 连旧线索都没有，放弃兜底

    # 前 2 条公开，剩余轮转分配（第 i 条分给第 i % len(names) 个嫌疑人）
    public = old_clues[:2]
    private = []
    if names:
        for i, c in enumerate(old_clues[2:]):
            private.append({"holder": names[i % len(names)], "content": c})
    else:
        public = old_clues   # 没有嫌疑人名单，全部当公共线索

    script["public_clues"] = public
    script["private_clues"] = private
    return script


def distribute_clues_node(state: dict) -> dict:
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

    public = list(script.get("public_clues") or [])
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

    # clues_pool：所有线索的完整清单（公开 + 私密），供 DM 揭晓时复盘"哪些线索从未被公开"
    clues_pool = public + [
        (c.get("content", "") if isinstance(c, dict) else c) for c in private
    ]

    return {
        "clues_pool": clues_pool,
        "distributed_clues": distributed,
    }


def choose_role_node(state: dict) -> dict:
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


def dm_intro_node(state: dict) -> dict:
    """节点 2：DM 开场介绍（只公布公共线索，私密线索各角色私下掌握）"""
    script = state.get("script", {})
    background = script.get("background", script.get("raw", ""))
    suspects = script.get("suspects", [])
    public_clues = script.get("public_clues", [])
    user_role = state.get("user_role", "")

    prompt = f"""你是一位剧本杀主持人（DM）。现在进入【开场】阶段。

案件背景：{background}
登场嫌疑人：{suspects}
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

    # 信息差：只把"这个角色自己持有的私密线索"告诉它，别的角色有什么它不知道
    own_clues = state.get("distributed_clues", {}).get(name, [])
    clues_text = "\n".join(f"- {c}" for c in own_clues) if own_clues else "（你没有额外的私密线索）"

    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-6:]
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
    think, speak = "", "……"
    for attempt in range(3):   # 最多重试 3 次，避免死循环
        out = _parse_json(_stream_full_text(prompt))
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

    兜底校验：resume 值必须是合法嫌疑人名字，否则丢弃（算弃权）。
    防止前端 chat_input 值残留把"发言文本"当成投票传进来，污染 votes——
    这正是"输入的消息变成投票结果"这个 bug 的根源。
    """
    user_role = state.get("user_role", "你")
    script = state.get("script", {})
    suspect_names = [s.get("name", "?") for s in script.get("suspects", [])]

    user_vote = interrupt({"type": "human_vote", "suspects": suspect_names})

    # 拷贝现有投票（AI 玩家投的）
    votes = dict(state.get("votes", {}))
    # 只有合法嫌疑人名字才记录，非法值（比如残留的发言文本）直接丢弃 = 弃权
    if user_vote in suspect_names:
        votes[user_role] = user_vote
    return {"votes": votes}


def tally_node(state: dict) -> dict:
    """节点 7：统计票数（确定性节点，纯 Python 逻辑，不调 LLM）"""
    votes = state.get("votes", {})
    counter = Counter(votes.values())
    vote_counts = dict(counter)
    vote_winner = counter.most_common(1)[0][0] if counter else "无人投票"
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


def dm_reveal_node(state: dict) -> dict:
    """节点 8：DM 揭晓真相 + 对比投票结果 + 线索复盘（信息差闭环）"""
    script = state.get("script", {})
    truth = script.get("truth", "")
    votes = state.get("votes", {})
    vote_counts = state.get("vote_counts", {})
    vote_winner = state.get("vote_winner", "无人")

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
票数统计：{vote_counts}（得票最多的是：{vote_winner}）

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


def route_speaker(state: dict) -> str:
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

    speaker = suspects[round_num % len(suspects)]
    if speaker.get("name") == state.get("user_role", ""):
        return "human"   # 轮到用户发言
    return "ai"          # 轮到 AI 发言


if __name__ == "__main__":
    print(generate_script_node({"theme": "校园密室"}))

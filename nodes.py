"""
节点函数 —— LangGraph 里的"演员"

节点清单（13 个，与 graph.py 的 add_node 一致）：
1. generate_script_node ：生成剧本 + 线索（LLM）
1.5 distribute_clues_node：线索分发，信息差（确定性节点，不调 LLM）
1.6 choose_role_node    ：用户选择扮演的角色（interrupt 暂停等选择）
2. dm_intro_node       ：DM 开场介绍（LLM）
2.5 self_intro_node    ：自我介绍（每个 AI 依次介绍，玩家跳过）（LLM）
3. ai_player_turn_node ：AI 玩家发言，think/speak 双通道（LLM）
3.5 dm_midpoint_node   ：DM 中场引导（讨论过半触发一次）（LLM）
4. human_turn_node     ：轮到用户发言（interrupt 暂停等输入）
5. ai_vote_node        ：AI 玩家投票（LLM）
6. human_vote_node     ：用户投票（interrupt 暂停等输入）
7. tally_node          ：统计票数 + 全局胜负（确定性节点，不调 LLM）
7.5 final_statement_node：被投最高者的最终陈词（LLM）
8. dm_reveal_node      ：DM 揭晓真相 + 对比投票 + 线索复盘（LLM）
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
import re
from collections import Counter
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek
from langgraph.types import interrupt
import httpx

from game_state import GameState
from names import _parse_names, _pick_suspect_names
from validators import _parse_json, _enforce_names, _normalize_secrets, _fallback_clues, _filter_relations, _ensure_clues, _extract_speak
from prompts import _build_script_prompt, murderer_defense_pool_text

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


def _check_script_consistency(script: dict) -> list[str]:
    """剧本自洽性确定性检查，返回问题列表（空列表 = 通过）。

    对应"赢不了的局"风险：线索与真相不自洽时，玩家认真推理却必然失败，
    且事后看不出是系统问题。这里做五条低成本检查：
    1. truth 里必须提到某个嫌疑人名字（凶手在名单内）
    2. 每个嫌疑人的 forbidden 词至少有 1 个出现在自己的 secret 里（防泄露机制才有效）
    3. 至少要有私密线索（信息差的基础，全空则玩不下去）
    4. 8-20 新增：每个嫌疑人的关键结构化字段（secret/personal_script/profession/relation_to_victim/alibi）
       必须独立填写（非空且不是"待补充"占位符）。LLM 偷懒只把信息塞进 DM 开场叙事、
       不填 JSON 字段会导致左栏全空、玩家拿不到角色卡——这是飞哥用完整《桃花坪埋尸案》
       背景测试时实际遇到的坑，必须靠代码硬检测 + 重试机制兜底。
    5. 8-20 新增：relations 必须有至少 1 条公开边（>=3 人局时），让玩家能盘问人物关系。
    """
    problems = []
    suspects = script.get("suspects") or []
    names = [s.get("name", "") for s in suspects if isinstance(s, dict) and s.get("name")]
    truth = script.get("truth", "")

    if truth and names and not any(n and n in truth for n in names):
        problems.append("truth 未提到任何嫌疑人名字")

    for s in suspects:
        if not isinstance(s, dict):
            continue
        secret = s.get("secret", "")
        forbidden = s.get("forbidden", []) or []
        if secret and forbidden and not any(w and w in secret for w in forbidden):
            problems.append(f"{s.get('name', '?')} 的 forbidden 词未出现在 secret 里")

    if not (script.get("private_clues") or script.get("public_clues")):
        problems.append("没有任何线索")

    # 8-20：检测结构化字段是否仍为空/占位符——LLM 偷懒只讲故事不填 JSON 字段的兜底
    empty_field_suspects = []
    for s in suspects:
        if not isinstance(s, dict):
            continue
        name = s.get("name", "?")
        missing = []
        # secret / personal_script 是空就报（"待补充" 视为空）
        for fld in ["secret", "personal_script", "profession", "relation_to_victim", "alibi"]:
            val = s.get(fld, "")
            if not (isinstance(val, str) and val.strip()) or val == "待补充":
                missing.append(fld)
        if missing:
            empty_field_suspects.append(f"{name} 缺 {', '.join(missing)}")
    if empty_field_suspects:
        problems.append(
            "以下嫌疑人的结构化字段仍为空/占位符，必须独立写齐（不能用『待补充』占位）："
            + "; ".join(empty_field_suspects)
        )

    # 8-20：relations 必须有公开边（2 人以上局），否则玩家没东西盘问。
    # 单人/空名单跳过（单人局不存在人物关系）。
    relations = script.get("relations") or []
    public_relations = [r for r in relations if isinstance(r, dict) and r.get("public")]
    if not public_relations and len(names) >= 2:
        problems.append("relations 数组没有公开边（人物关系图会显示『剧本未生成公开关系』，必须至少 1 条公开关系）")

    return problems


def _get_murderer(script: dict, suspect_names: list[str]) -> str:
    """提取凶手名字：优先读 murderer 字段（结构化），否则从 truth 里子串匹配嫌疑人名字兜底。

    murderer 是结构化字段（"玩家是凶手""AI 目标注入"都靠它精确判断谁是凶手）；
    旧格式剧本没有这个字段时，从 truth 文本里找第一个出现的嫌疑人名字。
    """
    murderer = script.get("murderer", "")
    if murderer and murderer in suspect_names:
        return murderer
    # 兜底：从 truth 里找第一个出现的嫌疑人名字
    truth = script.get("truth", "")
    for n in suspect_names:
        if n and n in truth:
            return n
    return ""


def generate_script_node(state: GameState) -> dict:
    """节点 1：生成结构化剧本（不再指定用户角色，改由 choose_role_node 让用户选）

    两个入口：
    - 如果 state 里已经有 script（前端用流式预生成好了），直接跳过，不重复生成。
    - 否则用 llm.invoke 兜底生成（终端版 main.py 走这条路）。

    生成后做自洽校验，不过则重试（最多 2 次），仍不过放行但记日志。
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

    script = None
    problems = []
    for attempt in range(2):   # 最多生成 2 次（自洽校验不过则重试）
        # 8-19：第一次用原始 prompt，重试时把上次失败原因拼进 prompt 让 LLM 知道漏在哪
        current_prompt = prompt if attempt == 0 else _build_script_prompt(
            theme, background, names, background_story, story_time, story_location,
            previous_problems=problems)
        resp = get_llm().invoke(current_prompt)
        script = _parse_json(resp.content)
        script = _fallback_clues(script)        # 兜底：LLM 没按新格式给线索时，从旧格式抢救
        script = _enforce_names(script, names)  # 兜底：强制剧本名字 = 我们抽的名字
        script = _normalize_secrets(script)     # 兜底：secret 强制第一人称开头
        script = _filter_relations(script)      # 兜底：剔除名单外角色的关系边（否则关系图崩溃）
        script = _ensure_clues(script)          # 最后防线：线索全空时从 secret 生成，保证能玩
        problems = _check_script_consistency(script)
        if not problems:
            break
        logger.warning("剧本自洽校验未通过（第 %d 次）：%s", attempt + 1, problems)
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

    # 生成 + 兜底 + 自洽校验（不过则重试，最多 2 次）
    script = None
    problems = []
    for attempt in range(2):
        # 8-19：第一次用原始 prompt，重试时把上次失败原因拼进 prompt 让 LLM 知道漏在哪
        current_prompt = prompt if attempt == 0 else _build_script_prompt(
            theme, background, names, background_story, story_time, story_location,
            previous_problems=problems)
        # 用 list 累积 token，最后 join（O(n)），避免 `full += token` 的 O(n²) 复制
        parts = []
        for chunk in get_llm().stream(current_prompt):
            token = chunk.content or ""
            if token:
                parts.append(token)
                yield token

        # 流式结束后，用累积的完整文本做解析 + 兜底（和节点里的后处理完全一致）
        script = _parse_json("".join(parts))
        script = _fallback_clues(script)
        script = _enforce_names(script, names)
        script = _normalize_secrets(script)   # 兜底：secret 强制第一人称开头
        script = _filter_relations(script)    # 兜底：剔除名单外角色的关系边（否则关系图崩溃）
        script = _ensure_clues(script)        # 最后防线：线索全空时从 secret 生成，保证能玩
        problems = _check_script_consistency(script)
        if not problems:
            break
        logger.warning("剧本自洽校验未通过（第 %d 次）：%s", attempt + 1, problems)
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

    # 均衡兜底：尽量消除"白板角色"（整局无存在感、开局即劝退）。
    # 只从"线索数 > 1"的角色匀一条给零线索角色，且保证 donor 匀完还剩至少 1 条——
    # 避免拆东墙补西墙（把一个人匀光、制造新的白板）。
    for n in [x for x in suspect_names if not distributed[x]]:
        donors = [x for x in suspect_names if len(distributed[x]) > 1]
        if not donors:
            break   # 没有多线索角色可匀了，剩余白板只能留白（线索总量不够，数学上无解）
        donor = max(donors, key=lambda x: len(distributed[x]))
        distributed[n].append(distributed[donor].pop())

    return {
        "distributed_clues": distributed,
    }


def _extract_clue_keywords(clue: str) -> list[str]:
    """从线索内容里抽关键词（用于判断线索是否在发言中被公开提及）。

    简单启发式：按标点/空白切分，取长度 >= 2 的片段当关键词。
    足够用来做"这条线索有没有被人说出来"的粗略判断，不必精确。
    """
    words = re.split(r"[，。、；：？！\s]+", clue)
    return [w for w in words if len(w) >= 2]


def _update_revealed_clues(speak: str, distributed_clues: dict, revealed_clues: dict) -> dict:
    """发言后更新线索公开状态：发言里出现某条线索的关键词，就标记为公开。

    返回本次新公开的线索映射 {线索内容: 首次提及者}（只返回新增，已公开的跳过）。
    纯确定性逻辑，不调 LLM。
    """
    newly = {}
    for holder, clues in distributed_clues.items():
        for clue in clues:
            if clue in revealed_clues:
                continue   # 已公开，跳过
            keywords = _extract_clue_keywords(clue)
            if any(kw and kw in speak for kw in keywords):
                newly[clue] = holder
    return newly


def _detect_addressed(speak: str, names: list[str]) -> str:
    """检测发言里点名了哪个嫌疑人（简单子串匹配，返回第一个被点到的名字，无则空串）。

    用于"点名优先发言"：玩家问"陆明轩案发当晚你在哪"时，检测到"陆明轩"，
    让陆明轩下一个优先回应。子串匹配可能误伤（名字是另一个名字的子串），
    但作为启发式足够——真正的回应质量由 prompt 的"必须回应"规则兜底。
    """
    for n in names:
        if n and n in speak:
            return n
    return ""


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
    # 判断玩家是否选到了凶手（用于差异化提示 + 完美犯罪结局）
    murderer = _get_murderer(script, names)
    return {"user_role": chosen, "user_is_murderer": chosen == murderer}


def dm_intro_node(state: GameState) -> dict:
    """节点 2：DM 开场介绍（只公布公共线索和公开关系，私密线索/私密关系各角色私下掌握）"""
    script = state.get("script", {})
    background = script.get("background", script.get("raw", ""))
    suspects = script.get("suspects", [])
    # 只把嫌疑人"名字"传给 DM，绝不给 secret/forbidden——
    # 否则 prompt 里"你也不知道私密线索"就和传入内容自相矛盾，DM 会在开场白剧透。
    names = [s.get("name", "?") for s in suspects]
    public_clues = script.get("public_clues", [])
    # 公开的人物关系（public=true 的才能当众介绍；私密关系仍属信息差，玩家要自己盘）
    relations = script.get("relations", [])
    public_relations = [
        f"{r.get('from', '?')} 与 {r.get('to', '?')}：{r.get('rel', '')}"
        for r in relations if isinstance(r, dict) and r.get("public", False)
    ]
    relations_text = "\n".join(f"- {t}" for t in public_relations) if public_relations else "（人物之间的恩怨情仇，等玩家自己盘问）"
    user_role = state.get("user_role", "")

    prompt = f"""你是一位剧本杀主持人（DM）。现在进入【开场】阶段。

案件背景：{background}
登场嫌疑人：{names}
公开人物关系（这些可以当众介绍，帮助玩家建立人物印象）：
{relations_text}
可公开线索（这些可以当众公布）：{public_clues}

【重要规则】这是一局"信息不对称"的剧本杀：
- 每个嫌疑人私下都握有只属于自己的私密线索（已悄悄发到各自手里，不在这里列出，你也不知道具体内容）。
- 你在开场时只能公布上面的"可公开线索"和"公开人物关系"，绝不能编造或公布私密线索、私密关系。
- 你要引导玩家：真相散落在不同人手里，需要大家讨论、互相盘问才能拼出全貌。

请用主持人的口吻，把案件背景像讲故事一样娓娓道来（自然融入，不要照念、不要机械罗列），依次：
1. 用一段有画面感的开场，把案件背景和嫌疑人自然引出来
2. 借公开人物关系，简要勾勒"谁和谁有什么纠葛"（点到为止，勾出嫌疑即可）
3. 公布可公开线索
4. 说明"每人手中握有私密线索"，鼓励玩家互相套话
5. 自然过渡到自由讨论，规则是嫌疑人轮流发言

【硬性人称约束 · 8-20 飞哥反馈"主持人旁白用『我』破坏沉浸感"】
- 严禁使用第一人称"我"——你是全知旁观者，不是事件参与者
- 描述自己的动作/位置/神态时，用"主持人""他/她"或无主语客观描写，绝不能用"我"
- ✅ "主持人站在书房门口，目光扫过在座各位。壁炉的火光映着死者脸上凝固的惊愕。"
- ❌ "我站在书房门口，目光扫过你们每个人。"

注意：{user_role} 是真人玩家扮演的，介绍时正常介绍即可。

只输出主持台词本身，不要额外解释。"""

    resp = _stream_full_text(prompt, allow_partial=False)   # 关键台词：断连宁可报错重试，不能拿半截文本
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "discuss",
        "phase_round": 0,   # 讨论从第 0 轮开始计数
    }


def self_intro_node(state: GameState) -> dict:
    """节点 2.5：自我介绍（dm_intro 之后、讨论循环之前）。

    调研报告五阶段标准：每位玩家以角色身份依次介绍「姓名/职业/与死者关系/案发时在哪」，
    这是玩家建立"谁是谁、谁和死者什么关系"的信息基础——之前跳过这步直接自由讨论，
    玩家会陷入"我是谁、该说什么、大家在说什么"的茫然（报告风险 1）。

    实现：每个 AI 角色单独一次 LLM 调用做自我介绍，只给公开信息（职业/关系/不在场证明），
    绝不给 secret（信息差安全）；真人玩家跳过（玩家已有个人剧本，可在第一轮自由发言时介绍）。
    串行调用 n-1 次，和 ai_vote 逐人投票是同一模式。
    """
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    user_role = state.get("user_role", "")

    intro_messages = []
    for s in suspects:
        name = s.get("name", "")
        if name == user_role:
            continue   # 真人玩家跳过，不用 LLM 替玩家自我介绍
        profession = s.get("profession", "")
        relation = s.get("relation_to_victim", "")
        alibi = s.get("alibi", "")
        personality = s.get("personality", "")
        speech_style = s.get("speech_style", "")

        prompt = f"""你正在扮演剧本杀角色「{name}」，现在进入【自我介绍】阶段。

你的性格：{personality or "未指定"}
你的说话风格：{speech_style or "未指定"}
你的职业：{profession or "未指定"}
你与死者的关系：{relation or "未指定"}
你的不在场证明（案发时你声称自己在哪）：{alibi or "未提供"}

请用 1~2 句做自我介绍，依次说明：你的姓名、职业、与死者的关系、案发时你在哪里。
要求：
- 只介绍上面给出的公开信息，绝不能提到你的秘密或任何不该公开的线索。
- 符合你的性格和说话风格，让玩家感受到你是个活人，而不是照稿念。

只输出自我介绍本身，不要 JSON、不要「自我介绍」这类前缀。"""

        resp = _stream_full_text(prompt, allow_partial=False)   # 关键台词：断连宁可报错重试，不能拿半截文本
        intro_messages.append({"speaker": name, "content": f"（自我介绍）{resp}"})

    return {"messages": intro_messages}


def dm_midpoint_node(state: GameState) -> dict:
    """节点 3.5：DM 中场引导（讨论过半时触发一次，基于线索公开情况给方向提示）。

    真实 DM 的核心职能是中场控场——讨论后半程容易跑题冷场，这里在过半时插入
    一次 2~3 句的中场引导：指出被忽略的线索方向、提醒盘问人物关系，但不剧透。
    依赖 revealed_clues（线索公开追踪）提供"哪些线索还没浮出水面"的事实。
    """
    revealed = state.get("revealed_clues", {})
    distributed = state.get("distributed_clues", {})

    # 尚未被任何人公开提及的线索（还在某人手里藏着）
    all_clues = [c for clues in distributed.values() for c in clues]
    hidden = [c for c in all_clues if c not in revealed]

    prompt = f"""你是剧本杀主持人（DM）。讨论已经过半，现在做一次中场引导。

已经公开讨论的线索：{list(revealed.keys()) if revealed else "（还没有线索被公开讨论）"}
尚未被提及的线索方向：{hidden if hidden else "（线索基本都浮出水面了）"}

请用 2~3 句给出中场引导：
- 指出大家似乎忽略了哪个方向（从"尚未被提及的线索"里挑，但**不要直接说出线索的具体内容**，只给模糊方向，比如"大家似乎都忽略了案发时间这个疑点"）
- 提醒玩家关注还没被盘问清楚的人物关系
- 绝不剧透真相、绝不编造线索

只输出主持台词本身，不要额外解释。"""

    resp = _stream_full_text(prompt, allow_partial=False)   # 关键台词：断连宁可报错重试，不能拿半截文本
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "midpoint_done": True,   # 标记中场引导已做，避免重复触发
    }


def ai_player_turn_node(state: GameState) -> dict:
    """节点 3：AI 玩家发言（think/speak 双通道 + 防泄露重试 + 确定性脱敏兜底）"""
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return {"phase_round": state.get("phase_round", 0) + 1}

    round_num = state.get("phase_round", 0)
    # 定向通信：玩家点名了某个 AI 时，优先让他回应（而非按轮换顺序），回应后清空 pending
    pending = state.get("pending_reply_to", "")
    suspect_names = [s.get("name", "") for s in suspects]
    if pending and pending in suspect_names:
        speaker = next(s for s in suspects if s.get("name") == pending)
        clear_pending = {"pending_reply_to": ""}
    else:
        speaker = _current_speaker(state)
        clear_pending = {}
    name = speaker.get("name", "嫌疑人")
    secret = speaker.get("secret", "无")
    forbidden = speaker.get("forbidden", [])   # 禁忌词：绝对不能公开说
    personality = speaker.get("personality", "")
    speech_style = speaker.get("speech_style", "")
    # 结构化角色信息：职业/与死者关系/不在场证明/任务。
    # 与 personal_script 互补——personal_script 是叙事故事，这四个是精确注入的"行为锚点"，
    # 尤其 task 决定了 AI 讨论时的行为方向（隐瞒/查案/保护某人/转移嫌疑），避免千人一面。
    profession = speaker.get("profession", "")
    relation_to_victim = speaker.get("relation_to_victim", "")
    alibi = speaker.get("alibi", "")
    task = speaker.get("task", "")
    # 个人剧本：这个角色完整的背景故事（身份/动机/人际关系/目标），
    # 发言必须基于它，而不是只靠一句 secret 干巴巴地编。
    personal_script = speaker.get("personal_script", "")

    # 判断当前发言者是否为凶手，注入不同目标（凶手脱罪 vs 无辜者找凶）——
    # 没有目标的 agent 只是聊天机器人，有目标才有策略、欺骗、博弈
    murderer = _get_murderer(script, suspect_names)
    if name == murderer:
        # 8-20：数 messages 历史里玩家"指控"消息数，决定凶手用哪套狡辩策略。
        # 飞哥反馈："凶手被指控只会说同样的话"——给一个 5 套策略池循环使用，
        # 严禁 LLM 重复同一种话术。accusation_count = 0 时不拼策略池（未被指控过）。
        user_role_in_state = state.get("user_role", "")
        messages = state.get("messages", [])
        accusation_count = sum(
            1 for m in messages
            if m.get("speaker") == user_role_in_state and "指控" in (m.get("content") or "")
        )
        goal_text = """【你的目标】你是真凶，首要目标是不被投出去：
- 不要主动提及任何能指向你的线索，尽量把嫌疑引向有动机的其他人
- 被直接指控时，用你手里的有利线索为自己辩护
- 可以撒谎、编造不在场证明，但要圆得回来，不能自相矛盾""" + murderer_defense_pool_text(accusation_count)
    else:
        goal_text = """【你的目标】你是无辜者，首要目标是找出真凶：
- 分享你手里的线索（但可保留最关键的一条作底牌）
- 关注其他人发言中的矛盾，指出不合理之处
- 被怀疑时为自己辩护，但不要慌乱"""

    # 信息差：只把"这个角色自己持有的私密线索"告诉它，别的角色有什么它不知道
    own_clues = state.get("distributed_clues", {}).get(name, [])
    clues_text = "\n".join(f"- {c}" for c in own_clues) if own_clues else "（你没有额外的私密线索）"

    # 8-20：注入玩家性别（从剧本里 user_role 查 suspects 拿 gender 字段）——
    # 飞哥反馈"沈长卿在两轮发言里被 NPC 分别称为'沈小姐'和'沈先生'"，中性名字
    # LLM 自己猜性别前后会矛盾。这里把剧本里显式确定的 gender 直接告诉 AI，
    # 让 AI 严格按此称呼玩家（先生/小姐），严禁混用。
    user_role = state.get("user_role", "")
    user_suspect = next(
        (s for s in script.get("suspects", []) if s.get("name") == user_role), {}
    )
    user_gender = user_suspect.get("gender", "")
    gender_hint = ""
    if user_gender:
        gender_hint = f"\n真人玩家（{user_role}）性别：{user_gender}——**必须严格按此性别称呼**（男→「先生/他」/女→「小姐/她」），整局游戏前后必须一致，严禁混用「先生/小姐」。\n"

    # 历史窗口动态化：至少保留最近 6 条；嫌疑人多时保留两轮完整讨论，
    # 避免 AI 忘掉两轮前别人说过的话（之前写死 [-6:]，12~18 条消息只看到 6 条）
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-max(6, len(suspects) * 2):]
    )

    # 自我记忆：该角色之前说过的关键陈述（防自相矛盾）
    memory = state.get("agent_memory", {}).get(name, [])
    memory_text = "\n".join(f"- 你之前说过：{m}" for m in memory[-3:]) if memory else "（这是你第一次发言）"

    prompt = f"""你正在扮演剧本杀角色「{name}」。

你的性格：{personality or "未指定"}
你的说话风格：{speech_style or "未指定"}
你的职业：{profession or "未指定"}
你与死者的关系：{relation_to_victim or "未指定"}
你的不在场证明（案发时你声称自己在哪）：{alibi or "未提供"}
你的秘密（只能你自己知道，绝不能在发言中直接承认）：{secret}

你的个人剧本（你完整的背景故事，发言必须基于它、贴合你的人设和动机，不能凭空编造出与它矛盾的内容）：
{personal_script or "（未提供）"}
{gender_hint}
你之前说过的话（必须与之保持一致，不能自相矛盾；若之前说了谎，要圆回来而不是推翻）：
{memory_text}

你手里握有的私密线索（只有你知道；是否公开、公开多少、如何曲解，都由你决定）：
{clues_text}

最近对话：
{history if history else "（还没有人发言）"}

{goal_text}

你的专属任务（比上面的通用目标更具体，你每一次发言和行动都要围绕它，而不是只做不痛不痒的推理）：
{task or "（未指定，按上面的通用目标行事）"}

【发言要求】
- 说话必须体现你的性格和说话风格，让玩家感受到你是一个"活人"，而不是复读机。
- 若最近对话中有人直接向你提问、点名质疑你，你必须先正面回应（可以撒谎、可以回避、可以反将一军，但绝不能无视），再展开你自己的内容。
- **若玩家最近发言明显是胡言乱语 / 装疯卖傻 / 发梗 / 开玩笑**（如"666""哈哈哈""666杀手""导导导"这种无意义或捣乱的内容），你必须**自然地回应这种行为本身**——可以调侃、装傻、反问、或按字面意思接梗展开（8-20 飞哥反馈：玩家装疯卖傻时 NPC 不理或强行曲解都不自然，要把这种行为融入游戏氛围）。严禁完全无视玩家发言（显得没听到），也严禁强行曲解成别的语义（显得自说自话）。

请以「{name}」的口吻，输出 JSON：
{{
  "think": "你的内心推理（不公开）：你在隐瞒什么、怀疑谁、想引导什么、手里的线索指向谁",
  "speak": "你公开说的话（1~3 句，符合人设。可选择性抛出部分线索引导他人，也可隐瞒）"
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

    # 更新线索公开状态（发言里出现的线索关键词标记为公开）
    revealed = _update_revealed_clues(speak, state.get("distributed_clues", {}), state.get("revealed_clues", {}))

    # 把本次发言存入自我记忆（保留最近 3 条），供下一轮"保持一致"使用
    new_memory = (state.get("agent_memory", {}).get(name, []) + [speak])[-3:]

    return {
        "messages": [{"speaker": name, "content": speak}],
        "thoughts": [f"{name}（内心）: {think}"],
        "phase_round": round_num + 1,
        "revealed_clues": revealed,
        "agent_memory": {name: new_memory},
        **clear_pending,
    }


def human_turn_node(state: GameState) -> dict:
    """节点 4：轮到用户行动（interrupt 暂停，等用户发言或选动作）。

    现在支持四种动作，靠 resume 值的类型区分：
    - str：普通发言（向后兼容旧前端，也兼容 main.py 直接 input）
    - {"action": "reveal_clue", "clue": "..."}：公开自己一条私密线索
    - {"action": "accuse", "target": "..."}：正式指控某人（触发对方强制回应）
    - {"action": "investigate"}：调查一条隐藏线索（从 hidden_clues 揭示）

    这是豆包报告"玩家无法行动只能说话"的修复——玩家从旁观者变成主动者。
    """
    user_role = state.get("user_role", "你")
    round_num = state.get("phase_round", 0)
    script = state.get("script", {})
    suspect_names = [s.get("name", "") for s in script.get("suspects", [])]
    # 排除自己的可指控对象
    targets = [n for n in suspect_names if n != user_role]
    # 玩家自己持有的私密线索（可公开）
    own_clues = state.get("distributed_clues", {}).get(user_role, [])
    # 还能调查的隐藏线索（用于前端禁用/隐藏"调查"按钮）
    hidden = script.get("hidden_clues", [])
    investigated = state.get("investigated_clues", [])
    available_hidden = [c for c in hidden if c not in investigated]

    # 暂停，把玩家可用的行动信息传给前端（前端据此渲染"公开线索/指控/调查"按钮）
    action = interrupt({
        "type": "human_turn",
        "speaker": user_role,
        "own_clues": own_clues,
        "targets": targets,
        "can_investigate": bool(available_hidden),
    })

    # ---- 兼容两种 resume：str = 发言，dict = 动作 ----
    investigated_new: list[str] = []
    if isinstance(action, dict):
        kind = action.get("action", "speak")
        if kind == "reveal_clue":
            clue = action.get("clue", "")
            text = f"我公开一条线索：{clue}"
            addressed = ""
            follow_up = 0
            # 精确标记该线索公开（确定性，不靠关键词匹配）
            revealed = {clue: user_role} if clue else {}
        elif kind == "accuse":
            target = action.get("target", "")
            text = f"我正式指控 {target} 是凶手！"
            addressed = target if target in targets else ""
            follow_up = 1 if addressed else 0
            revealed = _update_revealed_clues(text, state.get("distributed_clues", {}), state.get("revealed_clues", {}))
        elif kind == "investigate":
            if available_hidden:
                clue = available_hidden[0]   # 按序取第一条未调查的隐藏线索
                text = f"🔍 我调查后发现了一条新线索：{clue}"
                investigated_new = [clue]
                revealed = {clue: user_role}   # 调查到的线索也标记公开
            else:
                text = "我调查了一番，但没有新发现。"
                revealed = {}
            addressed = ""
            follow_up = 0
        else:
            # dict 但 action 未知，兜底当发言（取 text 字段）
            text = str(action.get("text", ""))
            revealed = _update_revealed_clues(text, state.get("distributed_clues", {}), state.get("revealed_clues", {}))
            addressed = _detect_addressed(text, targets)
            follow_up = 1 if addressed else 0
    else:
        # str = 普通发言（向后兼容）
        text = action
        revealed = _update_revealed_clues(text, state.get("distributed_clues", {}), state.get("revealed_clues", {}))
        addressed = _detect_addressed(text, targets)
        follow_up = 1 if addressed else 0

    result = {
        "messages": [{"speaker": user_role, "content": text}],
        "phase_round": round_num + 1,
        "revealed_clues": revealed,
        "pending_reply_to": addressed,
        "follow_up": follow_up,
    }
    if investigated_new:
        result["investigated_clues"] = investigated_new
    return result


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
    # L4 修复（终审）：投票窗口从写死 [-10:] 改为动态——
    # 深入节奏（每人 4 轮）下讨论可达 24+ 条消息，只给 10 条会让 AI 忘记前期线索。
    # 与 ai_player_turn 一致：至少 10 条，嫌疑人多时保留两轮完整讨论。
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-max(10, len(suspect_names) * 2):]
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
        return {"vote_counts": {}, "vote_winner": "无人投票", "game_result": "平局"}

    top = counter.most_common()
    max_n = top[0][1]
    winners = [k for k, v in top if v == max_n]
    vote_winner = "平票（" + "、".join(winners) + "）" if len(winners) > 1 else winners[0]

    # 全局胜负判定：对比得票最多者与真凶（剧本杀"平民 vs 凶手"的对抗性核心，报告风险 2）。
    # 之前无论投对投错都揭晓真相，投票没有 stakes；现在投出真凶=平民胜利，真凶逃脱=凶手胜利。
    script = state.get("script", {})
    murderer = _get_murderer(script, [s.get("name", "") for s in script.get("suspects", [])])
    if len(winners) > 1:
        game_result = "平局"        # 平票，胜负未分
    elif vote_winner == murderer:
        game_result = "平民胜利"    # 投出了真凶
    else:
        game_result = "凶手胜利"    # 真凶逃脱

    return {"vote_counts": vote_counts, "vote_winner": vote_winner, "game_result": game_result}


def final_statement_node(state: GameState) -> dict:
    """节点 7.5：被投最高者的最终陈词（tally 与 reveal 之间，补上剧本杀的情绪最高点）。

    真实剧本杀里"被投者自辩/遗言"是情绪高点，这里让得票最高的 AI 角色做一次
    最后挣扎（喊冤或认罪由 LLM 根据其真实身份自由发挥）。
    平票 / 无人投票 / 得票最高者恰好是真人玩家时跳过（玩家已在投票时表达）。
    """
    vote_winner = state.get("vote_winner", "")
    user_role = state.get("user_role", "")
    script = state.get("script", {})

    if not vote_winner or str(vote_winner).startswith("平票") or vote_winner == user_role:
        return {}   # 无单一 AI 被投最多，跳过陈词

    suspects = script.get("suspects", [])
    winner = next((s for s in suspects if s.get("name") == vote_winner), None)
    secret = winner.get("secret", "无") if winner else "无"

    # L5 修复（终审）：完整 truth 只给真凶本人（它本来就知道，用于"认罪/狡辩"抉择）；
    # 无辜者只告诉它"你不是真凶"——之前把含真凶姓名的 truth 交给无辜者，
    # 它喊冤时可能无意中说出"真凶其实是 XX"，提前剧透、削弱 dm_reveal 的悬念。
    truth = script.get("truth", "")
    suspects_names = [s.get("name", "") for s in suspects]
    murderer = _get_murderer(script, suspects_names)
    if vote_winner == murderer:
        truth_hint = f"案件真相（你此刻扮演这个角色，请根据自己是否真凶来决定喊冤还是认罪）：{truth}"
    else:
        truth_hint = "事实：你不是真凶，你被冤枉了——喊冤时语气要符合被冤枉的处境，但绝不要说破真凶是谁（悬念留给揭晓环节）"

    prompt = f"""你是剧本杀角色「{vote_winner}」，你在投票中被最多人指认为凶手。

你的秘密：{secret}
{truth_hint}

请输出一段 2 句的最终陈词（这是你最后的自辩机会）：
- 若你是凶手：可以继续狡辩、嫁祸他人，也可以突然认罪，由你自由发挥
- 若你不是凶手：真诚喊冤，语气符合被冤枉的处境

只输出陈词本身，不要 JSON、不要解释、不要"最终陈词"这类前缀。"""

    resp = _stream_full_text(prompt, allow_partial=False)   # 关键台词：断连宁可报错重试，不能拿半截文本
    return {
        "messages": [{"speaker": vote_winner, "content": f"（最终陈词）{resp}"}],
    }


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


def _judge_ending(state: GameState) -> tuple[str, str]:
    """根据玩家行为判定结局类型，返回 (结局标签, 演绎提示)。

    五种结局（豆包报告玩法5.1「结局唯一」+ 深度5.2「玩家选凶手体验断裂」的修复）：
    - 完美犯罪：玩家是真凶且未被投出
    - 凶手伏法：玩家是真凶但被识破投出
    - 侦探：玩家不是凶手、投对、且主导了推理（发言多或调查过/公开过线索）
    - 幸运旁观者：玩家不是凶手、投对、但全程低调
    - 被蒙蔽：玩家不是凶手、投错（被真凶误导）

    纯确定性判定（统计发言次数、投给谁、是否凶手），不调 LLM——
    和 tally 数票、线索分发是同一个分层思路。
    """
    user_role = state.get("user_role", "你")
    user_is_murderer = state.get("user_is_murderer", False)
    votes = state.get("votes", {})
    user_vote = votes.get(user_role, "未投")
    vote_winner = str(state.get("vote_winner", ""))
    script = state.get("script", {})
    murderer = _get_murderer(script, [s.get("name", "") for s in script.get("suspects", [])])

    if user_is_murderer:
        if vote_winner != user_role:
            return "完美犯罪", f"（真人玩家 {user_role} 是真凶却成功脱罪！请点出他/她是如何瞒天过海、误导全场的）"
        return "凶手伏法", f"（真人玩家 {user_role} 是真凶但被识破投出，请演绎他/她的伏法与不甘）"

    # 玩家不是凶手
    if murderer and user_vote == murderer:
        # 投对：按活跃度区分"侦探" vs "幸运旁观者"
        speak_count = sum(1 for m in state.get("messages", []) if m.get("speaker") == user_role)
        investigated = bool(state.get("investigated_clues"))
        if speak_count >= 3 or investigated:
            return "侦探", f"（真人玩家 {user_role} 投对了真凶且积极主导了推理，请夸赞他/她的洞察力）"
        return "幸运旁观者", f"（真人玩家 {user_role} 投对了但全程低调，像是个运气不错的旁观者）"
    return "被蒙蔽", f"（真人玩家 {user_role} 投错了、被真凶误导，请演绎'真凶逍遥法外'的遗憾尾声，让玩家恍然大悟又懊恼）"


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

    # 真人玩家投给了谁，让 DM 据此判断玩家投对/投错，做投错结局演绎
    user_role = state.get("user_role", "你")
    user_vote = votes.get(user_role, "未投")

    # 全局胜负（tally_node 判定）：平民胜利/凶手胜利/平局，给 DM 一个明确的"谁赢了"前提
    game_result = state.get("game_result", "")
    if game_result == "平民胜利":
        result_note = "（全局胜负：平民胜利！真凶已被投出，请庆祝正义得到伸张）"
    elif game_result == "凶手胜利":
        result_note = "（全局胜负：凶手胜利！真凶逃脱了，请先点明「凶手逃脱」，再复盘真凶是如何瞒天过海、误导全场的）"
    elif game_result == "平局":
        result_note = "（全局胜负：平局，票数并列未能决出真凶）"
    else:
        result_note = ""

    # 结局判定：根据玩家行为给差异化结局（侦探/旁观者/被蒙蔽/完美犯罪/凶手伏法）
    ending_label, ending_note = _judge_ending(state)

    # 信息差闭环：把"私密线索总账"+"公开讨论记录"交给 LLM，让它复盘哪些线索被埋没
    clues_text = _format_private_clues(state.get("distributed_clues", {}))
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])
    )

    prompt = f"""你是一位剧本杀主持人（DM）。讨论和投票都结束了，现在进入【揭晓真相】阶段。

案件真相：{truth}
投票明细（谁投了谁，务必照实公布，禁止编造）：{votes_text}
票数统计：{vote_counts}（得票最多的是：{vote_winner}）{winner_note}
真人玩家（{user_role}）投给了：{user_vote}
{result_note}

【私密线索总账】开局时每个玩家私下只握有这些线索（别人不知道）：
{clues_text}

【完整公开讨论记录】：
{history if history else "（无）"}

【硬性人称约束 · 8-20 飞哥反馈】严禁使用第一人称"我"——你是全知旁观者。描述自己的动作/位置时用"主持人"或无主语客观描写，绝不能用"我"。

请用主持人的口吻，依次：
1. 照实公布投票明细（谁投了谁、谁得票最多）
2. 揭晓真相（凶手、动机、手法）
3. 对比投票和真相：多数人投对了吗？点出投对和投错的玩家
4. 【线索复盘】对照私密线索总账和公开讨论记录，指出哪些私密线索从头到尾没被任何人在讨论中提及（被埋没了），并简要说明这些线索若被挖出，对破案有什么帮助
5. 【结局演绎】真人玩家（{user_role}）本局的结局是「{ending_label}」{ending_note}
6. 为整场游戏收尾"""

    resp = _stream_full_text(prompt, allow_partial=False)   # 关键台词：断连宁可报错重试，不能拿半截文本
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "reveal",
    }


def _current_speaker(state: GameState) -> dict:
    """根据"上一位实际发言者"确定"这一轮轮到谁"（单一事实源）。

    8-20 终审 M2 修复：之前用 `suspects[round_num % len(suspects)]` 决定轮换，
    但"点名回应/追问"会让被点名者插队发言、phase_round 照常 +1，
    按取模计算的轮次就会错位——有人被整轮跳过、有人连续发言。
    现在改成：读 messages 最后一条的 speaker（上一位真正发言的人），
    从名单里取它的下一位。插队发言后，下一位仍是正常顺序里的下一位，
    轮换顺序不再被点名/追问机制打乱。

    调用前提：suspects 非空（调用方先判过空）。
    """
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return {}
    # 讨论开始前 messages 可能为空（或最后发言是"主持人"等名单外角色），从头开始
    last = state.get("messages", [])[-1].get("speaker", "") if state.get("messages") else ""
    if not last:
        return suspects[0]
    for i, s in enumerate(suspects):
        if s.get("name") == last:
            return suspects[(i + 1) % len(suspects)]
    return suspects[0]


def route_speaker(state: GameState) -> str:
    """条件边的路由函数：根据当前轮次决定"下一个谁发言"。

    返回 "human"（轮到用户）/ "ai"（轮到 AI）/ "midpoint"（中场引导）/ "vote"（进入投票）。
    """
    round_num = state.get("phase_round", 0)
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return "vote"

    # 讨论轮数动态：嫌疑人数量 × 每人发言轮数（rounds_per_player 可配置，默认 ROUNDS_PER_PLAYER）
    rounds_per_player = state.get("rounds_per_player", ROUNDS_PER_PLAYER)
    max_rounds = len(suspects) * rounds_per_player
    if round_num >= max_rounds:
        return "vote"

    # 中场引导：讨论过半时触发一次（midpoint_done 标记避免重复）
    if not state.get("midpoint_done") and round_num >= max_rounds // 2:
        return "midpoint"

    # 定向通信：玩家点名了某个 AI，优先让他回应（human_turn_node 已把点名者存入 pending_reply_to）
    if state.get("pending_reply_to", ""):
        return "ai"

    # 追问：玩家点名 AI 后有一次追问机会（follow_up>0），AI 回应后再次轮到玩家
    if state.get("follow_up", 0) > 0:
        return "human"

    speaker = _current_speaker(state)
    if speaker.get("name") == state.get("user_role", ""):
        return "human"   # 轮到用户发言
    return "ai"          # 轮到 AI 发言

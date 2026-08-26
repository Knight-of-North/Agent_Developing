"""
节点函数 —— LangGraph 里的"演员"

节点清单（14 个，与 graph.py 的 add_node 一致）：
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
7.4 tie_break_node     ：平票加时辩护（LLM，H15）
7.5 final_statement_node：被投最高者的最终陈词（LLM）
8. dm_reveal_node      ：DM 揭晓真相 + 对比投票 + 线索复盘（LLM）
9. route_speaker       ：条件边路由（判断下一个发言者是谁）

模块拆分（曾是 700+ 行的 God module）：
- names.py      名字池 + 抽样（_parse_names / _pick_suspect_names）
- prompts.py    prompt 构建（_build_script_prompt + 各阶段 prompt）
- validators.py 解析 / 规范化 / 兜底（_parse_json / _enforce_names / ...）
- config.py     集中配置（温度 / 轮数 / 限制）
- 本文件        纯节点编排 + llm 配置
"""
import os
import re
import time
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek
from langgraph.types import interrupt
import httpx

from game_state import GameState
from config import (
    LLM_CONFIG, LLM_TIMEOUT, LLM_MAX_RETRIES, TRUST_ENV,
    ROUNDS_PER_PLAYER, INVESTIGATE_LIMIT, MAX_CONSECUTIVE_FOLLOWUPS,
    MAX_CHAT_LEN, VOTE_CONCURRENT, VOTE_MAX_WORKERS, SCRIPT_MAX_ATTEMPTS,
)
from names import _parse_names, _pick_suspect_names
from validators import _parse_json, _enforce_names, _normalize_secrets, _fallback_clues, _filter_relations, _ensure_clues, _extract_speak
from prompts import (
    _build_script_prompt, murderer_defense_pool_text,
    build_dm_intro_prompt, build_self_intro_prompt, build_midpoint_prompt,
    build_ai_turn_prompt, build_vote_prompt, build_final_statement_prompt,
    build_reveal_prompt,
)

load_dotenv()

logger = logging.getLogger(__name__)

# 显式创建 HTTP 客户端。trust_env 可配（之前硬编码 False，企业代理环境无法连接）。
http_client = httpx.Client(trust_env=TRUST_ENV, timeout=LLM_TIMEOUT)

# LLM 客户端懒加载 + 按用途缓存（不同温度/最大 token）。
_llm_cache: dict[str, ChatDeepSeek] = {}


def get_llm(purpose: str = "player"):
    """按用途返回 LLM 客户端（单例缓存）。

    purpose: script / dm / player / vote，温度和 max_tokens 不同——
    剧本要创造性、投票要稳定，之前所有节点共用 0.8 不合理。
    """
    if purpose not in _llm_cache:
        cfg = LLM_CONFIG.get(purpose, LLM_CONFIG["player"])
        _llm_cache[purpose] = ChatDeepSeek(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY"),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens"],
            reasoning_effort="none",   # 关闭 v4 默认的 thinking 模式
            timeout=LLM_TIMEOUT,
            max_retries=LLM_MAX_RETRIES,
            http_client=http_client,
        )
    return _llm_cache[purpose]


def _stream_full_text(prompt: str, *, purpose: str = "player", allow_partial: bool = True) -> str:
    """流式调用 LLM 并累积成完整文本。

    用 list.append + join 累积（O(n)），避免 += 的 O(n²)。
    异常处理：断连时若 allow_partial 且已累积内容，返回部分文本；否则重抛。
    """
    parts = []
    try:
        for chunk in get_llm(purpose).stream(prompt):
            if chunk.content:
                parts.append(chunk.content)
    except Exception as e:
        logger.warning("LLM stream 中断（%s），已累积 %d 字符", type(e).__name__, sum(len(p) for p in parts))
        if not allow_partial or not parts:
            raise
    return "".join(parts)


def _safe_llm_text(prompt: str, *, purpose: str = "player", allow_partial: bool = True,
                   fallback: str = "……") -> str:
    """节点级安全封装：任何异常都返回 fallback，不抛到图执行栈（F1）。

    关键台词节点用 fallback 给一句过场话，保证流程不断；
    非关键的 AI 发言失败返回"……"由上层重试/兜底。
    """
    try:
        text = _stream_full_text(prompt, purpose=purpose, allow_partial=allow_partial)
        return text if text.strip() else fallback
    except Exception as e:
        logger.warning("LLM 调用失败（%s），使用兜底文案", type(e).__name__)
        return fallback


class ScriptGenerationError(RuntimeError):
    """剧本生成致命问题（凶手无效等），重试耗尽后抛出，让前端提示重开。"""


def _postprocess_script(raw_text: str, names: list[str]) -> tuple[dict, list[str]]:
    """剧本后处理流水线（解析 + 兜底 + 自洽校验），node 和 stream 共用（L5）。

    返回 (script, problems)。
    """
    script = _parse_json(raw_text)
    script = _fallback_clues(script)
    script = _enforce_names(script, names)
    script = _normalize_secrets(script)
    script = _filter_relations(script)
    script = _ensure_clues(script)
    problems = _check_script_consistency(script)
    return script, problems


def _check_script_consistency(script: dict) -> list[str]:
    """剧本自洽性确定性检查，返回问题列表（空列表 = 通过）。"""
    problems = []
    suspects = script.get("suspects") or []
    names = [s.get("name", "") for s in suspects if isinstance(s, dict) and s.get("name")]
    truth = script.get("truth", "")

    if truth and names and not any(n and n in truth for n in names):
        problems.append("truth 未提到任何嫌疑人名字")

    # F4：murderer 字段必填且必须在名单内（之前漏检，导致 truth 子串兜底误判凶手）
    murderer = script.get("murderer", "")
    if not murderer or murderer not in names:
        problems.append(f"murderer 字段缺失或不在嫌疑人名单内（当前：{murderer!r}），必须精确等于某个嫌疑人 name")

    for s in suspects:
        if not isinstance(s, dict):
            continue
        secret = s.get("secret", "")
        forbidden = s.get("forbidden", []) or []
        if secret and forbidden and not any(w and w in secret for w in forbidden):
            problems.append(f"{s.get('name', '?')} 的 forbidden 词未出现在 secret 里")

    if not (script.get("private_clues") or script.get("public_clues")):
        problems.append("没有任何线索")

    # 结构化字段非空检查
    empty_field_suspects = []
    for s in suspects:
        if not isinstance(s, dict):
            continue
        name = s.get("name", "?")
        missing = []
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

    relations = script.get("relations") or []
    public_relations = [r for r in relations if isinstance(r, dict) and r.get("public")]
    if not public_relations and len(names) >= 2:
        problems.append("relations 数组没有公开边（人物关系图会显示『剧本未生成公开关系』，必须至少 1 条公开关系）")

    # 公开边里至少 2 条涉及死者
    victim_edges = [
        r for r in public_relations
        if isinstance(r, dict) and (r.get("from") == "死者" or r.get("to") == "死者")
    ]
    if public_relations and len(victim_edges) < 2:
        problems.append(
            "relations 的公开边里直接涉及「死者」的不足 2 条（关系图里死者会被边缘化，"
            "必须至少 2 条公开边的一端是「死者」）"
        )

    # 嫌疑人之间也要有公开边
    if public_relations and len(names) >= 4:
        suspect_names_set = set(names)
        suspect_pair_edges = [
            r for r in public_relations
            if isinstance(r, dict)
            and r.get("from") in suspect_names_set
            and r.get("to") in suspect_names_set
            and r.get("from") != r.get("to")
        ]
        threshold = max(2, (len(names) + 1) // 2)
        if len(suspect_pair_edges) < threshold:
            problems.append(
                f"嫌疑人之间的公开边不足 {threshold} 条（嫌疑人之间也要互相连线，"
                f"不能全靠嫌疑人→死者。当前 {len(suspect_pair_edges)} 条，阈值 {threshold} 条）"
            )

    return problems


def _get_murderer(script: dict, suspect_names: list[str]) -> str:
    """提取凶手名字：优先 murderer 字段，否则从 truth 里找最后出现的嫌疑人名字兜底。

    兜底取"最后出现"而非"第一个出现"——truth 叙事通常先描述涉案人物、
    在末尾才揭露真凶，第一个出现的往往是无辜者（F4）。
    """
    murderer = script.get("murderer", "")
    if murderer and murderer in suspect_names:
        return murderer
    truth = script.get("truth", "")
    found = ""
    for n in suspect_names:
        if n and n in truth:
            found = n   # 继续遍历，保留最后一个出现的
    return found


def _summarize_personal_script(text: str, limit: int = 120) -> str:
    """personal_script 确定性摘要：取前 limit 字，在句号/逗号处截断（H10）。

    不调 LLM，避免额外开销；注入发言 prompt 时用摘要替代 150-250 字全文，
    降低 token 占用和"复述原文泄露秘密"的风险。
    """
    if not text:
        return "（未提供）"
    if len(text) <= limit:
        return text
    snippet = text[:limit]
    for sep in ("。", "！", "？", "；", "\n"):
        idx = snippet.rfind(sep)
        if idx >= limit // 2:
            return snippet[:idx + 1]
    return snippet + "……"


def generate_script_node(state: GameState) -> dict:
    """节点 1：生成结构化剧本。

    两个入口：已有 script（前端预生成）则跳过；否则 LLM 生成。
    生成后自洽校验，不过则重试；致命问题（murderer 无效）重试耗尽抛 ScriptGenerationError（H1）。
    """
    if state.get("script"):
        return {"current_phase": "intro"}

    theme = state.get("theme", "民国豪门恩怨")
    background = state.get("background_style", "自由发挥")
    background_story = state.get("background_story", "")
    story_time = state.get("story_time", "")
    story_location = state.get("story_location", "")
    custom_names = state.get("custom_names")

    names = _pick_suspect_names(background, custom_names)
    prompt = _build_script_prompt(theme, background, names, background_story, story_time, story_location)

    script = None
    problems = []
    for attempt in range(SCRIPT_MAX_ATTEMPTS):
        current_prompt = prompt if attempt == 0 else _build_script_prompt(
            theme, background, names, background_story, story_time, story_location,
            previous_problems=problems)
        # 剧本生成用 script 用途（高温度）
        resp = get_llm("script").invoke(current_prompt)
        script, problems = _postprocess_script(resp.content, names)
        if not problems:
            break
        logger.warning("剧本自洽校验未通过（第 %d 次）：%s", attempt + 1, problems)

    # 致命问题不放行：murderer 无效 / 无线索，游戏逻辑会全错
    fatal = [p for p in problems if ("murderer" in p or "没有任何线索" in p)]
    if fatal:
        raise ScriptGenerationError("; ".join(fatal))
    if problems:
        logger.warning("剧本存在非致命问题，放行：%s", problems)
    return {"script": script, "current_phase": "intro"}


def generate_script_stream(theme: str, background: str, background_story: str = "", custom_names: list[str] | None = None, story_time: str = "", story_location: str = ""):
    """真流式生成剧本（生成器函数）。

    yield：("token", 文本片段) 用于前端实时显示；("retry", 提示) 表示重试清空显示。
    return：解析 + 兜底后的完整 script dict。
    """
    names = _pick_suspect_names(background, custom_names)
    prompt = _build_script_prompt(theme, background, names, background_story, story_time, story_location)

    script = None
    problems = []
    for attempt in range(SCRIPT_MAX_ATTEMPTS):
        current_prompt = prompt if attempt == 0 else _build_script_prompt(
            theme, background, names, background_story, story_time, story_location,
            previous_problems=problems)
        parts = []
        for chunk in get_llm("script").stream(current_prompt):
            token = chunk.content or ""
            if token:
                parts.append(token)
                yield ("token", token)

        script, problems = _postprocess_script("".join(parts), names)
        if not problems:
            break
        logger.warning("剧本自洽校验未通过（第 %d 次）：%s", attempt + 1, problems)
        yield ("retry", "剧本校验未通过，正在重新生成……")

    fatal = [p for p in problems if ("murderer" in p or "没有任何线索" in p)]
    if fatal:
        raise ScriptGenerationError("; ".join(fatal))
    return script


def distribute_clues_node(state: GameState) -> dict:
    """节点 1.5：线索分发（信息差的核心，确定性节点，不调 LLM）。"""
    script = state.get("script", {})
    suspect_names = [s.get("name", "") for s in (script.get("suspects") or []) if s.get("name")]

    private = script.get("private_clues") or []

    distributed = {name: [] for name in suspect_names}

    unassigned = []
    for clue in private:
        if not isinstance(clue, dict):
            continue
        content = clue.get("content", "")
        holder = clue.get("holder", "")
        if content and holder in distributed:
            distributed[holder].append(content)
        elif content:
            unassigned.append(content)

    if unassigned and suspect_names:
        for content in unassigned:
            target = min(suspect_names, key=lambda n: len(distributed[n]))
            distributed[target].append(content)

    # 均衡兜底：消除白板角色
    for n in [x for x in suspect_names if not distributed[x]]:
        donors = [x for x in suspect_names if len(distributed[x]) > 1]
        if not donors:
            break
        donor = max(donors, key=lambda x: len(distributed[x]))
        distributed[n].append(distributed[donor].pop())

    return {"distributed_clues": distributed}


def _extract_clue_keywords(clue: str) -> list[str]:
    """从线索内容里抽关键词（按标点/空白切分，取长度 >= 2 的片段）。"""
    words = re.split(r"[，。、；：？！\s]+", clue)
    return [w for w in words if len(w) >= 2]


def _clue_mentioned(clue: str, speak: str, threshold: float = 0.6) -> bool:
    """判断线索是否在发言中被提及（H3：降低误报，同时容忍转述/省略）。

    - 精确子串优先（线索整句出现 → 命中）
    - 否则算字符重叠率：线索中的不同字符有多少比例出现在发言中。
      单个常见词（如"书房"）重叠率低，不会误标整句线索；
      转述（"书房里有带血的刀" vs 线索"书房里有一把带血的刀"）重叠率高，能命中。
    """
    if not clue:
        return False
    if clue in speak:
        return True
    clue_chars = set(clue)
    if len(clue_chars) < 3:
        # 极短线索（2字）必须精确出现，避免单字误匹配
        return False
    overlap = sum(1 for c in clue_chars if c in speak)
    return overlap / len(clue_chars) >= threshold


def _update_revealed_clues(speak: str, distributed_clues: dict, revealed_clues: dict) -> dict:
    """发言后更新线索公开状态。用 _clue_mentioned 容忍转述、防止单词误报。"""
    newly = {}
    for holder, clues in distributed_clues.items():
        for clue in clues:
            if clue in revealed_clues:
                continue
            if _clue_mentioned(clue, speak):
                newly[clue] = holder
    return newly


def _detect_addressed(speak: str, names: list[str]) -> str:
    """检测发言里点名了哪个嫌疑人（H18：长名优先，避免前缀重叠误匹配）。

    按名字长度降序匹配：名单同时含"江叙"和"江叙白"时，"江叙白你怎么看"
    优先命中"江叙白"而非短名"江叙"。不用中文边界正则——"江叙白你"里
    "你"是正常称呼字，边界正则会误阻断。
    """
    for n in sorted(names, key=len, reverse=True):
        if n and n in speak:
            return n
    return ""


def choose_role_node(state: GameState) -> dict:
    """节点 1.6：用户选择扮演的角色（interrupt 暂停，等用户选）。"""
    script = state.get("script", {})
    suspects = script.get("suspects") or []
    names = [s.get("name", "?") for s in suspects]

    if not names:
        return {"user_role": "玩家"}

    chosen = interrupt({"type": "choose_role", "suspects": names})

    if chosen not in names:
        chosen = names[0]
    murderer = _get_murderer(script, names)
    return {"user_role": chosen, "user_is_murderer": chosen == murderer}


def dm_intro_node(state: GameState) -> dict:
    """节点 2：DM 开场介绍（只公布公共线索和公开关系）。"""
    script = state.get("script", {})
    background = script.get("background", script.get("raw", ""))
    suspects = script.get("suspects", [])
    names = [s.get("name", "?") for s in suspects]
    public_clues = script.get("public_clues", [])
    relations = script.get("relations", [])
    public_relations = [
        f"{r.get('from', '?')} 与 {r.get('to', '?')}：{r.get('rel', '')}"
        for r in relations if isinstance(r, dict) and r.get("public", False)
    ]
    relations_text = "\n".join(f"- {t}" for t in public_relations) if public_relations else "（人物之间的恩怨情仇，等玩家自己盘问）"
    user_role = state.get("user_role", "")

    prompt = build_dm_intro_prompt(background, names, relations_text, public_clues, user_role)
    resp = _safe_llm_text(prompt, purpose="dm", allow_partial=False,
                          fallback="主持人翻开卷宗，将案件背景一一道来，众人各怀心事。")
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "discuss",
        "phase_round": 0,
    }


def self_intro_node(state: GameState) -> dict:
    """节点 2.5：自我介绍（每个 AI 单独 LLM 调用，玩家跳过）。

    H5：每个角色独立 try/except，一人失败不影响其他人，用确定性兜底文案。
    """
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    user_role = state.get("user_role", "")

    intro_messages = []
    for s in suspects:
        name = s.get("name", "")
        if name == user_role:
            continue
        prompt = build_self_intro_prompt(
            name, s.get("personality", ""), s.get("speech_style", ""),
            s.get("profession", ""), s.get("relation_to_victim", ""), s.get("alibi", ""),
        )
        resp = _safe_llm_text(prompt, purpose="player", allow_partial=False,
                              fallback=f"在下{name}。")
        intro_messages.append({"speaker": name, "content": f"（自我介绍）{resp}"})

    return {"messages": intro_messages}


def _clue_topic_map(script: dict) -> dict:
    """建 {线索内容: 方向标签} 映射，供 DM 中场引导用（H13）。"""
    topic_map = {}
    for c in script.get("private_clues", []) or []:
        if isinstance(c, dict) and c.get("content"):
            topic = (c.get("topic") or "").strip()
            topic_map[c["content"]] = topic
    return topic_map


def dm_midpoint_node(state: GameState) -> dict:
    """节点 3.5：DM 中场引导（H13：只给线索方向标签，不给原文）。"""
    revealed = state.get("revealed_clues", {})
    distributed = state.get("distributed_clues", {})
    script = state.get("script", {})

    all_clues = [c for clues in distributed.values() for c in clues]
    hidden = [c for c in all_clues if c not in revealed]
    topic_map = _clue_topic_map(script)
    # 优先用 LLM 生成的 topic 标签；缺失时用线索前 6 字做模糊方向
    hidden_topics = []
    for c in hidden:
        t = topic_map.get(c, "")
        if t:
            hidden_topics.append(t)
        else:
            hidden_topics.append(c[:6] + "…" if len(c) > 6 else c)

    prompt = build_midpoint_prompt(revealed, hidden_topics)
    resp = _safe_llm_text(prompt, purpose="dm", allow_partial=False,
                          fallback="主持人目光扫过众人：有些线索似乎还被藏着，不妨再往深处问问。")
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "midpoint_done": True,
    }


def ai_player_turn_node(state: GameState) -> dict:
    """节点 3：AI 玩家发言（think/speak 双通道 + 防泄露重试 + 确定性脱敏兜底）。"""
    t0 = time.time()
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return {"phase_round": state.get("phase_round", 0) + 1}

    round_num = state.get("phase_round", 0)
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
    forbidden = speaker.get("forbidden", [])
    personality = speaker.get("personality", "")
    speech_style = speaker.get("speech_style", "")
    profession = speaker.get("profession", "")
    relation_to_victim = speaker.get("relation_to_victim", "")
    alibi = speaker.get("alibi", "")
    task = speaker.get("task", "")
    personal_script = speaker.get("personal_script", "")
    gender = speaker.get("gender", "")

    murderer = _get_murderer(script, suspect_names)
    if name == murderer:
        # L1：统计所有发言者（不限玩家）对本角色的指控次数
        messages_all = state.get("messages", [])
        accusation_count = sum(
            1 for m in messages_all
            if m.get("speaker") != name and "指控" in (m.get("content") or "")
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

    own_clues = state.get("distributed_clues", {}).get(name, [])
    clues_text = "\n".join(f"- {c}" for c in own_clues) if own_clues else "（你没有额外的私密线索）"

    # H16：注入所有角色的性别花名册，AI 间称呼也不混用
    gender_roster = "、".join(
        f"{s.get('name', '?')}（{s.get('gender', '未指定')}）"
        for s in suspects if isinstance(s, dict)
    )

    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-max(6, len(suspects) * 2):]
    )

    memory = state.get("agent_memory", {}).get(name, [])
    memory_text = "\n".join(f"- 你之前说过：{m}" for m in memory[-3:]) if memory else "（这是你第一次发言）"

    prompt = build_ai_turn_prompt(
        name=name, personality=personality, speech_style=speech_style,
        profession=profession, relation_to_victim=relation_to_victim, alibi=alibi,
        secret=secret, gender=gender, goal_text=goal_text, clues_text=clues_text,
        gender_roster=gender_roster, memory_text=memory_text, history=history,
        task=task, script_summary=_summarize_personal_script(personal_script),
    )

    # 防泄露重试 + 解析失败重试（H2：解析失败不能直接 break 接受"……"）
    think, speak = "", "……"
    feedback = ""
    attempts = 3
    for attempt in range(attempts):
        out = _parse_json(_safe_llm_text(prompt + feedback, fallback=""))
        think = out.get("think", "")
        speak = _extract_speak(out)
        leaked = [w for w in forbidden if w and w in speak]
        parse_failed = (speak == "……（这个角色欲言又止）")
        if not leaked and not parse_failed:
            break
        if leaked:
            feedback = f"\n\n⚠️ 你刚才的发言泄露了秘密（提到了：{'、'.join(leaked)}），请重新组织语言，绝不能再提这些词。"
        else:
            feedback = "\n\n⚠️ 输出格式有误，请严格输出包含 think 和 speak 两个字段的 JSON。"
    else:
        # 重试耗尽：确定性脱敏（F8 改进：整句替换为回避话术，而非 □ 欲盖弥彰）
        if any(w and w in speak for w in forbidden):
            speak = "此事我不想多谈，你们与其盯着我，不如去问问别人。"

    revealed = _update_revealed_clues(speak, state.get("distributed_clues", {}), state.get("revealed_clues", {}))
    new_memory = (state.get("agent_memory", {}).get(name, []) + [speak])[-3:]

    logger.info("node=ai_player_turn speaker=%s round=%d cost=%.2fs", name, round_num, time.time() - t0)
    return {
        "messages": [{"speaker": name, "content": speak}],
        "thoughts": [f"{name}（内心）: {think}"],
        "phase_round": round_num + 1,
        "revealed_clues": revealed,
        "agent_memory": {name: new_memory},
        **clear_pending,
    }


def human_turn_node(state: GameState) -> dict:
    """节点 4：轮到用户行动（interrupt 暂停，等用户发言或选动作）。"""
    user_role = state.get("user_role", "你")
    round_num = state.get("phase_round", 0)
    script = state.get("script", {})
    suspect_names = [s.get("name", "") for s in script.get("suspects", [])]
    targets = [n for n in suspect_names if n != user_role]
    own_clues = state.get("distributed_clues", {}).get(user_role, [])
    hidden = script.get("hidden_clues", [])
    investigated = state.get("investigated_clues", [])
    available_hidden = [c for c in hidden if c not in investigated]
    investigations_used = state.get("investigations_used", 0)
    can_investigate = bool(available_hidden) and investigations_used < INVESTIGATE_LIMIT

    action = interrupt({
        "type": "human_turn",
        "speaker": user_role,
        "own_clues": own_clues,
        "targets": targets,
        "can_investigate": can_investigate,
        "investigations_remaining": max(0, INVESTIGATE_LIMIT - investigations_used),
    })

    investigated_new: list[str] = []
    if isinstance(action, dict):
        kind = action.get("action", "speak")
        if kind == "reveal_clue":
            clue = action.get("clue", "")
            text = f"我公开一条线索：{clue}"
            addressed = ""
            follow_up = 0
            consecutive = 0
            revealed = {clue: user_role} if clue else {}
        elif kind == "accuse":
            target = action.get("target", "")
            text = f"我正式指控 {target} 是凶手！"
            addressed = target if target in targets else ""
            follow_up = 1 if addressed else 0
            consecutive = state.get("consecutive_followups", 0) + 1 if addressed else 0
            revealed = _update_revealed_clues(text, state.get("distributed_clues", {}), state.get("revealed_clues", {}))
        elif kind == "investigate":
            if can_investigate and available_hidden:
                clue = available_hidden[0]
                text = f"🔍 我调查后发现了一条新线索：{clue}"
                investigated_new = [clue]
                revealed = {clue: user_role}
            else:
                text = "我调查了一番，但没有新发现。"
                revealed = {}
            addressed = ""
            follow_up = 0
            consecutive = 0
        else:
            text = str(action.get("text", ""))[:MAX_CHAT_LEN]
            revealed = _update_revealed_clues(text, state.get("distributed_clues", {}), state.get("revealed_clues", {}))
            addressed = _detect_addressed(text, targets)
            follow_up = 1 if addressed else 0
            consecutive = state.get("consecutive_followups", 0) + 1 if addressed else 0
    else:
        # str = 普通发言（F2：截断超长输入）
        text = str(action)[:MAX_CHAT_LEN]
        revealed = _update_revealed_clues(text, state.get("distributed_clues", {}), state.get("revealed_clues", {}))
        addressed = _detect_addressed(text, targets)
        # F5：只有上一轮 follow_up==0 时新点名才给追问，防止无限连点霸麦
        prev_follow = state.get("follow_up", 0)
        if addressed and prev_follow == 0:
            follow_up = 1
            consecutive = state.get("consecutive_followups", 0) + 1
        elif addressed and prev_follow > 0:
            # 连续追问已用过，不再续期
            follow_up = 0
            consecutive = 0
        else:
            follow_up = 0
            consecutive = 0

    result = {
        "messages": [{"speaker": user_role, "content": text}],
        "phase_round": round_num + 1,
        "revealed_clues": revealed,
        "pending_reply_to": addressed,
        "follow_up": follow_up,
        "consecutive_followups": consecutive,
    }
    if investigated_new:
        result["investigated_clues"] = investigated_new
        result["investigations_used"] = investigations_used + 1
    return result


def _vote_one_player(name: str, secret: str, own_clues: list, history: str,
                     suspect_names: list[str], is_murderer: bool, task: str) -> tuple[str, str | None, str]:
    """单个 AI 玩家投票（H11：注入身份/任务，think+vote 双通道）。

    返回 (名字, 投票目标或 None, think 推理)。单个人调用失败不影响其他人。
    """
    identity_hint = (
        "你是真凶。投票目标：投给一个有嫌疑的无辜者，绝不能投自己，尽量引导他人跟着你投。"
        if is_murderer else
        "你是无辜者。你的目标是根据线索和讨论投出你认为的真凶。"
    )
    prompt = build_vote_prompt(name, secret, own_clues, history, suspect_names, identity_hint, task)
    try:
        out = _parse_json(_safe_llm_text(prompt, purpose="vote", fallback=""))
        vote = out.get("vote", "")
        think = out.get("think", "")
        if vote in suspect_names and vote != name:
            return name, vote, think
    except Exception as e:
        logger.warning("AI 玩家 %s 投票失败（%s），算弃权", name, type(e).__name__)
    return name, None, ""


def ai_vote_node(state: GameState) -> dict:
    """节点 5：AI 玩家投票（逐人投票，信息差闭环）。

    H6：用线程池并发调用（httpx.Client 线程安全），可通过 VOTE_CONCURRENT=0 回退串行。
    """
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    user_role = state.get("user_role", "")
    suspect_names = [s.get("name", "?") for s in suspects]
    ai_names = [n for n in suspect_names if n != user_role]
    murderer = _get_murderer(script, suspect_names)

    distributed = state.get("distributed_clues", {})
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])[-max(10, len(suspects) * 2):]
    )

    def _vote(name: str):
        secret = next((s.get("secret", "") for s in suspects if s.get("name") == name), "")
        task = next((s.get("task", "") for s in suspects if s.get("name") == name), "")
        return _vote_one_player(name, secret, distributed.get(name, []), history,
                                suspect_names, name == murderer, task)

    votes = {}
    thoughts = []
    if VOTE_CONCURRENT and len(ai_names) > 1:
        try:
            with ThreadPoolExecutor(max_workers=min(VOTE_MAX_WORKERS, len(ai_names))) as ex:
                results = list(ex.map(_vote, ai_names))
        except Exception as e:
            logger.warning("并发投票失败（%s），回退串行", type(e).__name__)
            results = [_vote(n) for n in ai_names]
    else:
        results = [_vote(n) for n in ai_names]

    for voter, target, think in results:
        if target:
            votes[voter] = target
        if think:
            thoughts.append(f"{voter}（投票内心）: {think}")

    out = {"votes": votes, "current_phase": "vote"}
    if thoughts:
        out["thoughts"] = thoughts
    return out


def human_vote_node(state: GameState) -> dict:
    """节点 6：用户投票（interrupt 暂停，等用户输入）。非法值弃权。"""
    user_role = state.get("user_role", "你")
    script = state.get("script", {})
    suspect_names = [s.get("name", "?") for s in script.get("suspects", [])]

    user_vote = interrupt({"type": "human_vote", "suspects": suspect_names})

    if user_vote in suspect_names:
        return {"votes": {user_role: user_vote}}
    return {}


def tally_node(state: GameState) -> dict:
    """节点 7：统计票数（确定性节点，纯 Python 逻辑）。"""
    votes = state.get("votes", {})
    counter = Counter(votes.values())
    vote_counts = dict(counter)
    if not counter:
        return {"vote_counts": {}, "vote_winner": "无人投票", "game_result": "平局"}

    top = counter.most_common()
    max_n = top[0][1]
    winners = [k for k, v in top if v == max_n]
    vote_winner = "平票（" + "、".join(winners) + "）" if len(winners) > 1 else winners[0]

    script = state.get("script", {})
    murderer = _get_murderer(script, [s.get("name", "") for s in script.get("suspects", [])])
    if len(winners) > 1:
        game_result = "平局"
    elif vote_winner == murderer:
        game_result = "平民胜利"
    else:
        game_result = "凶手胜利"

    return {"vote_counts": vote_counts, "vote_winner": vote_winner, "game_result": game_result}


def tie_break_node(state: GameState) -> dict:
    """节点 7.4：平票加时辩护（H15）。平票的 AI 候选人各做一句最后辩护，然后重投一轮。

    真人候选人不在这里辩护（玩家无法在 interrupt 外发言），但第二轮 human_vote
    会给玩家改票机会。
    """
    vote_winner = str(state.get("vote_winner", ""))
    user_role = state.get("user_role", "")
    script = state.get("script", {})
    suspects = script.get("suspects", [])

    # 从"平票（A、B）"解析候选人
    inner = vote_winner.strip("平票（）")
    tied_names = [n.strip() for n in inner.split("、") if n.strip()]

    messages = []
    for name in tied_names:
        if name == user_role:
            continue   # 真人玩家自己辩护，不替它生成
        suspect = next((s for s in suspects if s.get("name") == name), None)
        if not suspect:
            continue
        secret = suspect.get("secret", "无")
        murderer = _get_murderer(script, [s.get("name", "") for s in suspects])
        if name == murderer:
            truth_hint = f"案件真相（你是真凶，继续狡辩嫁祸）：{script.get('truth', '')}"
        else:
            truth_hint = "你不是真凶，你被冤枉了——用一句话做最后辩护，但别说破真凶是谁。"
        prompt = f"""你是剧本杀角色「{name}」，你目前平票，这是最后辩护机会。
你的秘密：{secret}
{truth_hint}
只输出一句为自己辩护的话，不要 JSON、不要前缀。"""
        resp = _safe_llm_text(prompt, purpose="player", allow_partial=False,
                              fallback=f"{name}沉默片刻，似乎有难言之隐。")
        messages.append({"speaker": name, "content": f"（平票辩护）{resp}"})

    out: dict = {"tie_break_done": True}
    if messages:
        out["messages"] = messages
    return out


def final_statement_node(state: GameState) -> dict:
    """节点 7.5：被投最高者的最终陈词。"""
    vote_winner = state.get("vote_winner", "")
    user_role = state.get("user_role", "")
    script = state.get("script", {})

    if not vote_winner or str(vote_winner).startswith("平票") or vote_winner == user_role:
        return {}

    suspects = script.get("suspects", [])
    winner = next((s for s in suspects if s.get("name") == vote_winner), None)
    secret = winner.get("secret", "无") if winner else "无"

    truth = script.get("truth", "")
    suspects_names = [s.get("name", "") for s in suspects]
    murderer = _get_murderer(script, suspects_names)
    if vote_winner == murderer:
        truth_hint = f"案件真相（你此刻扮演这个角色，请根据自己是否真凶来决定喊冤还是认罪）：{truth}"
    else:
        truth_hint = "事实：你不是真凶，你被冤枉了——喊冤时语气要符合被冤枉的处境，但绝不要说破真凶是谁（悬念留给揭晓环节）"

    prompt = build_final_statement_prompt(vote_winner, secret, truth_hint)
    resp = _safe_llm_text(prompt, purpose="player", allow_partial=False,
                          fallback=f"{vote_winner}张了张嘴，最终什么也没说。")
    return {
        "messages": [{"speaker": vote_winner, "content": f"（最终陈词）{resp}"}],
    }


def _format_private_clues(distributed_clues: dict) -> str:
    """把 {角色名: [私密线索...]} 格式化成文本清单。"""
    lines = []
    for holder, clues in distributed_clues.items():
        for c in clues:
            lines.append(f"- {holder}：{c}")
    return "\n".join(lines) if lines else "（没有私密线索）"


def _judge_ending(state: GameState) -> tuple[str, str]:
    """根据玩家行为判定结局类型，返回 (结局标签, 演绎提示)。纯确定性判定。"""
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

    if murderer and user_vote == murderer:
        speak_count = sum(1 for m in state.get("messages", []) if m.get("speaker") == user_role)
        investigated = bool(state.get("investigated_clues"))
        if speak_count >= 3 or investigated:
            return "侦探", f"（真人玩家 {user_role} 投对了真凶且积极主导了推理，请夸赞他/她的洞察力）"
        return "幸运旁观者", f"（真人玩家 {user_role} 投对了但全程低调，像是个运气不错的旁观者）"
    return "被蒙蔽", f"（真人玩家 {user_role} 投错了、被真凶误导，请演绎'真凶逍遥法外'的遗憾尾声，让玩家恍然大悟又懊恼）"


def dm_reveal_node(state: GameState) -> dict:
    """节点 8：DM 揭晓真相 + 对比投票结果 + 线索复盘（信息差闭环）。"""
    script = state.get("script", {})
    truth = script.get("truth", "")
    votes = state.get("votes", {})
    vote_counts = state.get("vote_counts", {})
    vote_winner = state.get("vote_winner", "无人")

    winner_note = "（注意：这是平票，务必如实说明「多票并列」，不要假装有单一赢家）" if str(vote_winner).startswith("平票") else ""
    votes_text = "、".join(f"{k}投{v}" for k, v in votes.items()) or "无人投票"

    user_role = state.get("user_role", "你")
    user_vote = votes.get(user_role, "未投")

    game_result = state.get("game_result", "")
    if game_result == "平民胜利":
        result_note = "（全局胜负：平民胜利！真凶已被投出，请庆祝正义得到伸张）"
    elif game_result == "凶手胜利":
        result_note = "（全局胜负：凶手胜利！真凶逃脱了，请先点明「凶手逃脱」，再复盘真凶是如何瞒天过海、误导全场的）"
    elif game_result == "平局":
        result_note = "（全局胜负：平局，票数并列未能决出真凶）"
    else:
        result_note = ""

    ending_label, ending_note = _judge_ending(state)
    clues_text = _format_private_clues(state.get("distributed_clues", {}))
    history = "\n".join(
        f"{m['speaker']}: {m['content']}" for m in state.get("messages", [])
    )

    prompt = build_reveal_prompt(
        truth=truth, votes_text=votes_text, vote_counts=vote_counts,
        vote_winner=vote_winner, winner_note=winner_note, user_role=user_role,
        user_vote=user_vote, result_note=result_note, clues_text=clues_text,
        history=history, ending_label=ending_label, ending_note=ending_note,
    )
    resp = _safe_llm_text(prompt, purpose="dm", allow_partial=False,
                          fallback="主持人公布了投票结果，揭开了案件真相。")
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "reveal",
    }


def _current_speaker(state: GameState) -> dict:
    """根据"上一位实际发言者"确定"这一轮轮到谁"（F3 修复：跳过主持人等名单外 speaker）。

    之前读 messages 末位 speaker，若末位是"主持人"（中场引导后）则回退到 suspects[0]，
    导致轮换重置、有人被跳过。现在从后向前找最近一位嫌疑人发言者。
    """
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return {}
    names = {s.get("name") for s in suspects if isinstance(s, dict)}
    # 从后向前找最近一位嫌疑人发言者，忽略主持人/DM/系统消息
    for m in reversed(state.get("messages", [])):
        last = m.get("speaker", "")
        if last in names:
            for i, s in enumerate(suspects):
                if s.get("name") == last:
                    return suspects[(i + 1) % len(suspects)]
            break
    return suspects[0]


def route_speaker(state: GameState) -> str:
    """条件边路由：返回 human / ai / midpoint / vote。"""
    round_num = state.get("phase_round", 0)
    script = state.get("script", {})
    suspects = script.get("suspects", [])
    if not suspects:
        return "vote"

    rounds_per_player = state.get("rounds_per_player", ROUNDS_PER_PLAYER)
    max_rounds = len(suspects) * rounds_per_player
    if round_num >= max_rounds:
        return "vote"

    if not state.get("midpoint_done") and round_num >= max_rounds // 2:
        return "midpoint"

    # F5：连续追问超过上限后强制回到正常轮换，防止玩家霸麦
    consecutive = state.get("consecutive_followups", 0)
    follow_up_limited = consecutive >= MAX_CONSECUTIVE_FOLLOWUPS

    if state.get("pending_reply_to", ""):
        return "ai"

    if state.get("follow_up", 0) > 0 and not follow_up_limited:
        return "human"

    speaker = _current_speaker(state)
    if speaker.get("name") == state.get("user_role", ""):
        return "human"
    return "ai"


def route_after_tally(state: GameState) -> str:
    """tally 后的条件边（H15）：平票且未加时过 → tie_break，否则 → final_statement。"""
    vote_winner = str(state.get("vote_winner", ""))
    if vote_winner.startswith("平票") and not state.get("tie_break_done"):
        return "tie_break"
    return "final_statement"

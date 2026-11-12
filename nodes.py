"""
节点函数 —— LangGraph 里的"演员"

节点清单（15 个，与 graph.py 的 add_node 一致）：
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
7.5 human_tiebreak_node：玩家在平票名单时的最后陈述（interrupt）
7.6 final_statement_node：被投最高者的最终陈词（LLM）
8. dm_reveal_node      ：DM 揭晓真相 + 对比投票 + 线索复盘（LLM）
9. route_speaker       ：条件边路由（判断下一个发言者是谁）

模块拆分（曾是 700+ 行的 God module）：
- names.py      名字池 + 抽样（_parse_names / _pick_suspect_names）
- prompts.py    prompt 构建（_build_script_prompt + 各阶段 prompt）
- validators.py 解析 / 规范化 / 兜底（_parse_json / _enforce_names / ...）
- config.py     集中配置（温度 / 轮数 / 限制）
- 本文件        纯节点编排 + llm 配置
"""
import contextvars
import logging
import os
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
from langchain_deepseek import ChatDeepSeek
from langgraph.types import interrupt

from config import (
    INVESTIGATE_LIMIT,
    LLM_CONFIG,
    LLM_MAX_RETRIES,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT,
    MAX_CHAT_LEN,
    MAX_CONSECUTIVE_FOLLOWUPS,
    ROUNDS_PER_PLAYER,
    SCRIPT_MAX_ATTEMPTS,
    TRUST_ENV,
    TURN_BUDGET_SECONDS,
    VOTE_CONCURRENT,
    VOTE_MAX_WORKERS,
)
from game_state import GameState
from interrupt_handler import ABSTAIN_WORDS, SILENCE_WORDS
from locations import clues_at_location, location_status
from names import _pick_suspect_names
from prompts import (
    _build_script_prompt,
    build_ai_turn_prompt,
    build_dm_intro_prompt,
    build_final_statement_prompt,
    build_midpoint_prompt,
    build_reveal_prompt,
    build_self_intro_prompt,
    build_tiebreak_prompt,
    build_vote_prompt,
    murderer_defense_pool_text,
)

# 兼容转发（拆分 rules.py 时留下的公开导入路径）：
# 本文件内部已不直接调用这两个函数，但 tests / 外部脚本仍按 `nodes._clue_mentioned`
# 的老路径导入。保留转发并显式 noqa，避免"删未用导入"把兼容层一起删掉。
from rules import (
    apply_followup_guard as _apply_followup_guard,
    clue_mentioned as _clue_mentioned,  # noqa: F401
    clue_topic_map as _clue_topic_map,
    count_accusations_against as _count_accusations_against,
    detect_addressed as _detect_addressed,  # noqa: F401
    get_murderer as _get_murderer,
    judge_ending as _judge_ending,
    update_revealed_clues as _update_revealed_clues,
)
from validators import (
    _coerce_text,
    _enforce_names,
    _ensure_clues,
    _extract_speak,
    _fallback_clues,
    _filter_relations,
    _normalize_secrets,
    _parse_json,
    _sanitize_outsider_names,
    _sanitize_text_for_script,
)

# `.env` 由 config.py 在 import 时加载（必须先于任何 os.getenv）；
# 这里不再调用 load_dotenv，避免"两处加载、时序仍错"的假象。

logger = logging.getLogger(__name__)

# 显式创建 HTTP 客户端。trust_env 可配（之前硬编码 False，企业代理环境无法连接）。
http_client = httpx.Client(trust_env=TRUST_ENV, timeout=LLM_TIMEOUT)

# LLM 客户端懒加载 + 按用途缓存（不同温度/最大 token）。
_llm_cache: dict[str, ChatDeepSeek] = {}
_llm_lock = threading.Lock()   # L2：并发投票下懒加载的双重检查锁


def get_llm(purpose: str = "player", json_mode: bool = False):
    """按用途返回 LLM 客户端（单例缓存）。

    purpose: script / dm / player / vote，温度和 max_tokens 不同——
    剧本要创造性、投票要稳定，之前所有节点共用 0.8 不合理。
    json_mode: True 时启用 DeepSeek 的 JSON 输出模式（response_format），
    模型侧保证输出合法 JSON（M7）；缓存 key 单独分档。
    """
    key = f"{purpose}:json" if json_mode else purpose
    if key not in _llm_cache:
        with _llm_lock:
            if key not in _llm_cache:   # 双重检查，避免并发重复构造
                cfg = LLM_CONFIG.get(purpose, LLM_CONFIG["player"])
                _llm_cache[key] = ChatDeepSeek(
                    model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                    api_key=os.getenv("DEEPSEEK_API_KEY"),
                    base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                    temperature=cfg["temperature"],
                    max_tokens=cfg["max_tokens"],
                    reasoning_effort=LLM_REASONING_EFFORT,   # L6：可配（默认关闭 v4 thinking 模式）
                    timeout=LLM_TIMEOUT,
                    max_retries=LLM_MAX_RETRIES,
                    http_client=http_client,
                    **({"response_format": {"type": "json_object"}} if json_mode else {}),
                )
    return _llm_cache[key]


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
                   fallback: str = "……", json_mode: bool = False) -> str:
    """节点级安全封装：任何异常都返回 fallback，不抛到图执行栈（F1）。

    关键台词节点用 fallback 给一句过场话，保证流程不断；
    非关键的 AI 发言失败返回"……"由上层重试/兜底。
    json_mode（M7）：先走 DeepSeek JSON 输出模式（invoke，模型侧保证合法 JSON），
    失败或返回空时自动回退普通流式路径——json_mode 是增强而非依赖，零行为退化风险。
    M12：失败日志带 purpose 与 prompt 头部，排障时可定位是哪个环节的调用。
    """
    if json_mode:
        try:
            # R2：必须走流式（stream）而非 invoke——非流式调用在 stream_mode="messages"
            # 下只产生 1 个"整段就绪"事件（时机=响应完成后），app.py 的"💭 思考中"
            # 占位会与结果同帧出现，AI 发言/投票期间页面完全静止（M3/M8 修复被
            # M7 的 invoke 化意外抵消）。DeepSeek 的 response_format 与流式兼容。
            parts = []
            for chunk in get_llm(purpose, json_mode=True).stream(prompt):
                if chunk.content:
                    parts.append(chunk.content)
            text = "".join(parts).strip()
            if text:
                return text
            logger.warning("json_mode 返回空文本（purpose=%s），回退普通模式", purpose)
        except Exception as e:
            logger.warning("json_mode 调用失败（%s，purpose=%s prompt_head=%r），回退普通模式",
                           type(e).__name__, purpose, prompt[:60])
    try:
        text = _stream_full_text(prompt, purpose=purpose, allow_partial=allow_partial)
        return text if text.strip() else fallback
    except Exception as e:
        logger.warning("LLM 调用失败（%s，purpose=%s prompt_head=%r），使用兜底文案",
                       type(e).__name__, purpose, prompt[:60])
        return fallback


class ScriptGenerationError(RuntimeError):
    """剧本生成致命问题（凶手无效等），重试耗尽后抛出，让前端提示重开。"""


def _run_concurrently(fn, items: list, *, label: str) -> list:
    """并发执行 fn(item)（保序），单条失败只重跑该条，不重跑全量。

    三个节点（自我介绍 / AI 投票 / 平票辩护）此前各抄一份"线程池 + 异常回退串行"：
    回退时把**已成功的条目**也全部重跑，等于重复烧 token。这里统一实现，
    并把回退粒度收窄到真正失败的那几条。

    contextvars：ThreadPoolExecutor 的 worker 不继承提交线程的上下文，
    直接 submit 会让日志里的对局 ID（gid）变成 "-"。每个任务用一份
    copy_context()（互不复用，避免同一 Context 并发 enter 抛 RuntimeError），
    worker 内的日志就能正确带上对局标识。
    """
    if not items:
        return []
    if not (VOTE_CONCURRENT and len(items) > 1):
        return [fn(item) for item in items]

    results: list = [None] * len(items)
    failed: list[int] = []
    with ThreadPoolExecutor(max_workers=min(VOTE_MAX_WORKERS, len(items))) as ex:
        futures = {
            ex.submit(contextvars.copy_context().run, fn, item): i
            for i, item in enumerate(items)
        }
        for future, idx in futures.items():
            try:
                results[idx] = future.result()
            except Exception as e:   # noqa: BLE001 - 单条失败降级为串行重试
                logger.warning("%s 第 %d 条并发失败（%s），单独重试", label, idx, type(e).__name__)
                failed.append(idx)
    for idx in sorted(failed):
        results[idx] = fn(items[idx])
    return results


class ScriptProblem(str):
    """一条剧本自洽问题：**既是字符串，又携带 fatal 标记**。

    为什么继承 str 而不是用 dataclass：
    1. 问题文本此前是纯字符串，被 `"; ".join(problems)`、`"xxx" in problems`、
       拼进重试 prompt 等 6 处直接消费；换成 dataclass 会连锁改一大片。
    2. 旧实现用 `"murderer" in p or "没有任何线索" in p` 反推"是否致命"——
       改一句文案就静默失效，且 generate_script_node / generate_script_stream
       各抄了一份判定。现在 fatal 是数据，不是从文案里猜出来的。
    3. str 子类让所有既有字符串用法零改动，测试也无须重写。
    """

    fatal: bool

    def __new__(cls, message: str, fatal: bool = False):
        obj = super().__new__(cls, message)
        obj.fatal = fatal
        return obj


def _is_fatal(problem) -> bool:
    """兼容裸字符串（外部/测试构造）——没有 fatal 标记一律按非致命处理。"""
    return bool(getattr(problem, "fatal", False))


def _drain(generator) -> dict:
    """把生成器跑到 StopIteration，返回它的 return 值。

    generate_script_node（图节点，不展示 token）与 generate_script_stream
    （Web 端，逐 token 展示）共用同一份重试实现：节点把 token 丢掉即可，
    不必再维护第二套"invoke 版"生成循环（两份 fatal 判定曾各写一遍）。
    """
    while True:
        try:
            next(generator)
        except StopIteration as stop:
            return stop.value


def _postprocess_script(raw_text: str, names: list[str]) -> tuple[dict, list[ScriptProblem]]:
    """剧本后处理流水线（解析 + 兜底 + 自洽校验），node 和 stream 共用（L5）。

    返回 (script, problems)。

    防御：整条流水线包在 try 里——LLM 返回的 JSON 结构可能荒唐（suspects 是 dict、
    secret 是数字……），任何一处漏掉的属性访问都会让整局生成直接失败。
    兜底策略是"记一条致命问题让上层重试"，而不是带病放行。
    """
    try:
        script = _parse_json(raw_text)
        script = _fallback_clues(script)
        script = _enforce_names(script, names)
        script = _sanitize_outsider_names(script)
        script = _normalize_secrets(script)
        script = _filter_relations(script)
        script = _ensure_clues(script)
        problems = _check_script_consistency(script)
    except Exception as e:   # noqa: BLE001 - 任何解析/结构异常都要转成可重试的问题
        logger.warning("剧本后处理异常（%s）：%s", type(e).__name__, e, exc_info=True)
        return {}, [ScriptProblem(
            f"剧本结构异常（{type(e).__name__}），无法解析为标准格式，请严格按 JSON schema 重出",
            fatal=True,
        )]
    return script, problems


def _check_script_consistency(script: dict) -> list[ScriptProblem]:
    """剧本自洽性确定性检查，返回问题列表（空列表 = 通过）。

    致命项（fatal=True）交给上层决定"重试耗尽就报错"：
    murderer 无效 / 没有任何线索 / truth 缺失——这三项放行会让游戏逻辑全错。
    """
    problems: list[ScriptProblem] = []
    suspects = script.get("suspects") or []
    if not isinstance(suspects, list):
        suspects = []
    names = [s.get("name", "") for s in suspects if isinstance(s, dict) and s.get("name")]
    truth = script.get("truth", "")

    # R4：truth 缺失/空白的剧本此前能带病通过校验（条件短路跳过），
    # 揭晓与最终陈词环节将无真相可揭——与 murderer 同级，硬性必填。
    if not (isinstance(truth, str) and truth.strip()):
        problems.append(ScriptProblem(
            "truth（案件真相）缺失或为空，必须写明作案动机与完整手法", fatal=True))
        truth = ""

    if truth and names and not any(n and n in truth for n in names):
        problems.append(ScriptProblem("truth 未提到任何嫌疑人名字"))

    # F4：murderer 字段必填且必须在名单内（之前漏检，导致 truth 子串兜底误判凶手）
    murderer = script.get("murderer", "")
    if not murderer or murderer not in names:
        problems.append(ScriptProblem(
            f"murderer 字段缺失或不在嫌疑人名单内（当前：{murderer!r}），必须精确等于某个嫌疑人 name",
            fatal=True,
        ))

    for s in suspects:
        if not isinstance(s, dict):
            continue
        secret = _coerce_text(s.get("secret", ""))
        forbidden = s.get("forbidden", []) or []
        if secret and isinstance(forbidden, list) and forbidden \
                and not any(isinstance(w, str) and w and w in secret for w in forbidden):
            problems.append(ScriptProblem(f"{s.get('name', '?')} 的 forbidden 词未出现在 secret 里"))

    if not (script.get("private_clues") or script.get("public_clues")):
        problems.append(ScriptProblem("没有任何线索", fatal=True))

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
        problems.append(ScriptProblem(
            "以下嫌疑人的结构化字段仍为空/占位符，必须独立写齐（不能用『待补充』占位）："
            + "; ".join(empty_field_suspects)
        ))

    relations = script.get("relations") or []
    public_relations = [r for r in relations if isinstance(r, dict) and r.get("public")]
    if not public_relations and len(names) >= 2:
        problems.append(ScriptProblem(
            "relations 数组没有公开边（至少需要 1 条公开关系，供玩家和 AI 了解人物关系）"))

    # 公开边里至少 2 条涉及死者
    victim_edges = [
        r for r in public_relations
        if isinstance(r, dict) and (r.get("from") == "死者" or r.get("to") == "死者")
    ]
    if public_relations and len(victim_edges) < 2:
        problems.append(ScriptProblem(
            "relations 的公开边里直接涉及「死者」的不足 2 条（死者需要至少 2 条公开关系，"
            "否则人物关系会显得边缘化）"
        ))

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
            problems.append(ScriptProblem(
                f"嫌疑人之间的公开边不足 {threshold} 条（嫌疑人之间也要互相连线，"
                f"不能全靠嫌疑人→死者。当前 {len(suspect_pair_edges)} 条，阈值 {threshold} 条）"
            ))

    return problems


def _summarize_personal_script(text: str, limit: int = 120) -> str:
    """personal_script 确定性摘要：取前 limit 字，在句号/逗号处截断（H10）。

    不调 LLM，避免额外开销；注入发言 prompt 时用摘要替代 150-250 字全文，
    降低 token 占用和"复述原文泄露秘密"的风险。
    """
    text = _coerce_text(text)   # LLM 可能把 personal_script 写成数字/对象，先归一
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

    实现复用 generate_script_stream：图节点不需要逐 token 展示，把生成器跑干
    并丢掉 token 即可——重试循环与致命判定只保留一份实现（此前 invoke/stream
    两个版本各抄一遍 fatal 判定，改一处文案另一处就静默失效）。
    """
    if state.get("script"):
        return {"current_phase": "intro"}

    theme = state.get("theme", "民国豪门恩怨")
    background = state.get("background_style", "自由发挥")
    background_story = state.get("background_story", "")
    story_time = state.get("story_time", "")
    story_location = state.get("story_location", "")
    custom_names = state.get("custom_names")

    # L7：名字回避用"局内"集合（GameState.used_names），不再读写模块级全局——
    # 多会话共享进程时 A 局不再消耗 B 局的名字池。返回值经 operator.add 累积进状态。
    prior_used = set(state.get("used_names", []))
    names = _pick_suspect_names(background, custom_names, used=prior_used)

    script = _drain(_iter_script_attempts(
        theme, background, names, background_story, story_time, story_location,
    ))
    # L7：本局抽用的名字写回状态（operator.add 累积），下局在同一 thread 内自动回避
    return {"script": script, "current_phase": "intro", "used_names": names}


def _iter_script_attempts(theme: str, background: str, names: list[str], background_story: str,
                          story_time: str, story_location: str):
    """剧本生成的重试循环（唯一实现）。yield token/retry 事件，return 最终 script。

    调用方：generate_script_stream 直接转发事件；generate_script_node 用 _drain 丢弃事件。
    """
    prompt = _build_script_prompt(theme, background, names, background_story, story_time, story_location)

    script: dict = {}
    problems: list[ScriptProblem] = []
    for attempt in range(SCRIPT_MAX_ATTEMPTS):
        current_prompt = prompt if attempt == 0 else _build_script_prompt(
            theme, background, names, background_story, story_time, story_location,
            previous_problems=[str(p) for p in problems])
        parts = []
        for chunk in get_llm("script").stream(current_prompt):
            token = chunk.content or ""
            if token:
                parts.append(token)
                yield ("token", token)

        script, problems = _postprocess_script("".join(parts), names)
        if not problems:
            break
        logger.warning("剧本自洽校验未通过（第 %d 次）：%s", attempt + 1, list(problems))
        yield ("retry", "剧本校验未通过，正在重新生成……")

    # 致命问题不放行：murderer 无效 / 无线索 / truth 缺失，游戏逻辑会全错
    # （R4：只拦"truth 缺失"，"truth 未提到名字"仍是非致命问题）
    fatal = [p for p in problems if _is_fatal(p)]
    if fatal:
        raise ScriptGenerationError("; ".join(str(p) for p in fatal))
    if problems:
        logger.warning("剧本存在非致命问题，放行：%s", list(problems))
    return script


def generate_script_stream(theme: str, background: str, background_story: str = "", custom_names: list[str] | None = None, story_time: str = "", story_location: str = "", used: set[str] | None = None):
    """真流式生成剧本（生成器函数）。

    yield：("token", 文本片段) 用于前端实时显示；("retry", 提示) 表示重试清空显示。
    return：解析 + 兜底后的完整 script dict。

    R8：used 为跨局已用名字集合（app.py 传 st.session_state.used_names）——
    此前流式主路径不接收 used，名字回避退化为 names.py 模块级全局，
    GameState.used_names 在主路径下成为死字段、跨进程重启后撞名回归。
    传入的集合会被 _pick_suspect_names 原地 update（本次抽用的名字写回集合）。
    """
    names = _pick_suspect_names(background, custom_names, used=used)
    inner = _iter_script_attempts(theme, background, names, background_story, story_time, story_location)
    while True:
        try:
            event = next(inner)
        except StopIteration as stop:
            return stop.value
        yield event


def distribute_clues_node(state: GameState) -> dict:
    """节点 1.5：线索分发（信息差的核心，确定性节点，不调 LLM）。"""
    script = (state.get("script") or {})
    suspect_names = [
        s.get("name", "") for s in (script.get("suspects") or [])
        if isinstance(s, dict) and s.get("name")
    ]

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


def choose_role_node(state: GameState) -> dict:
    """节点 1.6：用户选择扮演的角色（interrupt 暂停，等用户选）。"""
    script = (state.get("script") or {})
    suspects = script.get("suspects") or []
    names = [s.get("name", "?") for s in suspects]
    # 选角页只展示公开身份信息（职业/与死者关系），不泄露 secret/personal_script。
    profiles = [
        {
            "name": s.get("name", "?"),
            "profession": s.get("profession", ""),
            "relation_to_victim": s.get("relation_to_victim", ""),
        }
        for s in suspects if isinstance(s, dict)
    ]

    if not names:
        return {"user_role": "玩家"}

    payload = {"type": "choose_role", "suspects": names, "profiles": profiles}
    chosen = interrupt(payload)

    # R9：非法 resume 值不再静默回退 names[0]（终端手滑/协议误用会让玩家
    # 扮演完全陌生的角色）——重新 interrupt 让调用方重试一次；
    # 重试仍非法才保底 names[0]，协议层不可能无限 interrupt 循环。
    if chosen not in names:
        chosen = interrupt({**payload, "invalid": str(chosen)})
        if chosen not in names:
            chosen = names[0]
    murderer = _get_murderer(script, names)
    return {"user_role": chosen, "user_is_murderer": chosen == murderer}


def dm_intro_node(state: GameState) -> dict:
    """节点 2：DM 开场介绍（只公布公共线索和公开关系）。"""
    script = (state.get("script") or {})
    background = script.get("background", "")   # L1：删死 fallback script.get("raw","")——流水线恒产出 background
    suspects = script.get("suspects") or []
    names = [s.get("name", "?") for s in suspects]
    public_clues = script.get("public_clues") or []
    relations = script.get("relations") or []
    public_relations = [
        f"{r.get('from', '?')} 与 {r.get('to', '?')}：{r.get('rel', '')}"
        for r in relations if isinstance(r, dict) and r.get("public", False)
    ]
    relations_text = "\n".join(f"- {t}" for t in public_relations) if public_relations else "（人物之间的恩怨情仇，等玩家自己盘问）"
    user_role = state.get("user_role", "")

    prompt = build_dm_intro_prompt(background, names, relations_text, public_clues, user_role)
    resp = _safe_llm_text(prompt, purpose="dm", allow_partial=False,
                          fallback="主持人翻开卷宗，将案件背景一一道来，众人各怀心事。")
    resp = _sanitize_text_for_script(resp, script)
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "discuss",
        "phase_round": 0,
    }


def self_intro_node(state: GameState) -> dict:
    """节点 2.5：自我介绍（每个 AI 单独 LLM 调用，玩家跳过）。

    H5：每个角色独立 try/except，一人失败不影响其他人，用确定性兜底文案。
    H1：多角色并发调用（复用 ai_vote_node 的线程池模式，map 保序），
        开局 N-1 次串行干等 12~32 秒 → 并发后≈单次调用耗时。
    """
    script = (state.get("script") or {})
    suspects = script.get("suspects") or []
    user_role = state.get("user_role", "")
    bg_style = state.get("background_style", "") or ""

    # P5：兜底文案按背景风格适配——科幻局里冒出"在下"会瞬间出戏
    if "科幻" in bg_style or "末世" in bg_style:
        def _fallback(name: str) -> str:
            return f"{name}，身份保密，先听别人说吧。"
    elif any(k in bg_style for k in ("古风", "仙侠", "民国")):
        def _fallback(name: str) -> str:
            return f"在下{name}，幸会。"
    else:
        def _fallback(name: str) -> str:
            return f"我是{name}，别急着下结论。"

    def _intro(s: dict) -> dict:
        name = s.get("name", "")
        prompt = build_self_intro_prompt(
            name, s.get("personality", ""), s.get("speech_style", ""),
            s.get("profession", ""), s.get("relation_to_victim", ""), s.get("alibi", ""),
        )
        resp = _safe_llm_text(prompt, purpose="player", allow_partial=False,
                              fallback=_fallback(name))
        resp = _sanitize_text_for_script(resp, script)
        return {"speaker": name, "content": f"（自我介绍）{resp}"}

    targets = [s for s in suspects if s.get("name", "") != user_role]
    intro_messages = _run_concurrently(_intro, targets, label="自我介绍")

    return {"messages": intro_messages}


def dm_midpoint_node(state: GameState) -> dict:
    """节点 3.5：DM 中场引导（H13：只给线索方向标签，不给原文）。"""
    revealed = (state.get("revealed_clues") or {})
    distributed = (state.get("distributed_clues") or {})
    script = (state.get("script") or {})

    all_clues = [c for clues in distributed.values() for c in (clues or [])]
    hidden = [c for c in all_clues if c not in revealed]
    topic_map = _clue_topic_map(script)
    # M5：有 topic 标签的用标签；缺失 topic 的不能截取原文前 6 字（会直接泄露线索内容），
    # 合并成一条模糊提示，让 DM 引导玩家深挖而非替玩家公开。
    hidden_topics = []
    untagged = 0
    for c in hidden:
        t = (topic_map.get(c, "") or "").strip()
        if t:
            hidden_topics.append(t)
        else:
            untagged += 1
    if untagged:
        hidden_topics.append(f"另有 {untagged} 条线索尚未被讨论，请引导大家继续深挖")

    prompt = build_midpoint_prompt(revealed, hidden_topics)
    resp = _safe_llm_text(prompt, purpose="dm", allow_partial=False,
                          fallback="主持人目光扫过众人：有些线索似乎还被藏着，不妨再往深处问问。")
    resp = _sanitize_text_for_script(resp, script)
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "midpoint_done": True,
    }


def ai_player_turn_node(state: GameState) -> dict:
    """节点 3：AI 玩家发言（think/speak 双通道 + 防泄露重试 + 确定性脱敏兜底）。"""
    t0 = time.time()
    script = (state.get("script") or {})
    suspects = script.get("suspects") or []
    if not suspects:
        return {"phase_round": state.get("phase_round", 0) + 1}

    round_num = state.get("phase_round", 0)
    pending = state.get("pending_reply_to", "")
    suspect_names = [s.get("name", "") for s in suspects]
    if pending and pending in suspect_names:
        speaker = next(s for s in suspects if s.get("name") == pending)
        clear_pending = {"pending_reply_to": ""}
        is_followup_reply = True   # M11：插队回应（被点名后回应）
    else:
        speaker = _current_speaker(state)
        clear_pending = {}
        is_followup_reply = False
    # M11："插队免费 + 总量保险丝"——插队回应（pending 触发）不消耗 phase_round
    # 预算（点名/追问是加戏，不该挤掉其他 AI 的正常轮换、饿死其私密线索）；
    # 保险丝：全局插队累计超过 总预算/2 后恢复计费，防止被点名的 AI 反复插队
    # 把讨论拖成"两人对话"、总轮数远超预算。
    rounds_per_player = state.get("rounds_per_player", ROUNDS_PER_PLAYER)
    followup_count = state.get("followup_count", 0)
    fuse_limit = max(1, len(suspects) * rounds_per_player // 2)
    free_followup = is_followup_reply and followup_count < fuse_limit
    name = speaker.get("name", "嫌疑人")
    secret = speaker.get("secret", "无")
    # R3 加强：forbidden 若被 LLM 写成字符串，`for w in forbidden` 会退化成
    # **逐字检测**（"秘密"→ 检查"秘""密"两个字），正常台词极易误判泄密。
    # 这里统一收窄成 str 列表，下游只需按词判断。
    _raw_forbidden = speaker.get("forbidden", [])
    forbidden = ([w for w in _raw_forbidden if isinstance(w, str) and w]
                 if isinstance(_raw_forbidden, list) else [])
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
        # M4：用启发式统计所有发言者对本角色的指控次数（不只认"指控"二字）
        messages_all = (state.get("messages") or [])
        accusation_count = _count_accusations_against(messages_all, name)
        goal_text = """【你的目标】你是真凶，首要目标是不被投出去：
- 不要主动提及任何能指向你的线索，尽量把嫌疑引向有动机的其他人
- 被直接指控时，用你手里的有利线索为自己辩护
- 可以撒谎、编造不在场证明，但要圆得回来，不能自相矛盾""" + murderer_defense_pool_text(accusation_count)
    else:
        goal_text = """【你的目标】你是无辜者，首要目标是找出真凶：
- 分享你手里的线索（但可保留最关键的一条作底牌）
- 关注其他人发言中的矛盾，指出不合理之处
- 被怀疑时为自己辩护，但不要慌乱"""

    own_clues = (state.get("distributed_clues") or {}).get(name, [])
    clues_text = "\n".join(f"- {c}" for c in own_clues) if own_clues else "（你没有额外的私密线索）"

    # H3/P2：全局已公开线索账本——AI 不知道别人摊过牌会重复公开同一线索；
    # 注入账本后 AI 还能围绕已公开线索做增量推理。滑动窗口外的摊牌也不丢。
    revealed_all = (state.get("revealed_clues") or {})
    revealed_text = ("\n".join(f"- {holder}公开过：{clue}" for clue, holder in revealed_all.items())
                     if revealed_all else "（目前还没有人公开过私密线索）")

    # H16：注入所有角色的性别花名册，AI 间称呼也不混用
    gender_roster = "、".join(
        f"{s.get('name', '?')}（{s.get('gender', '未指定')}）"
        for s in suspects if isinstance(s, dict)
    )

    history = "\n".join(
        f"{m.get('speaker', '?')}: {m.get('content', '')}" for m in (state.get("messages") or [])[-max(6, len(suspects) * 2):] if isinstance(m, dict)
    )

    memory = (state.get("agent_memory") or {}).get(name, [])
    memory_text = "\n".join(f"- 你之前说过：{m}" for m in memory[-3:]) if memory else "（这是你第一次发言）"

    prompt = build_ai_turn_prompt(
        name=name, personality=personality, speech_style=speech_style,
        profession=profession, relation_to_victim=relation_to_victim, alibi=alibi,
        secret=secret, gender=gender, goal_text=goal_text, clues_text=clues_text,
        gender_roster=gender_roster, memory_text=memory_text, history=history,
        task=task, script_summary=_summarize_personal_script(personal_script),
        revealed_text=revealed_text,
    )

    # 防泄露重试 + 解析失败重试（H2：解析失败不能直接 break 接受"……"）
    think, speak = "", "……"
    feedback = ""
    attempts = 3
    # 时间预算：重试层数会相乘（3 次尝试 × json_mode 的"JSON 失败→普通模式"回退
    # × SDK max_retries=3），最坏一个回合能发出 24 次请求、把页面挂住十几分钟。
    # 超预算就停止重试、走确定性兜底——宁可一句过场，也不冻结 UI。
    deadline = time.time() + TURN_BUDGET_SECONDS
    for attempt in range(attempts):
        if time.time() > deadline:
            logger.warning("AI 发言超出时间预算（%ds），停止重试 speaker=%s", TURN_BUDGET_SECONDS, name)
            break
        # M7：json_mode 只在第 1 次尝试启用（它自带一次普通模式回退）；
        # 后续重试直接走普通流式，把最坏请求数从 24 压到 8 以内。
        out = _parse_json(_safe_llm_text(prompt + feedback, fallback="",
                                        json_mode=(attempt == 0)))
        think = out.get("think", "")
        speak = _extract_speak(out)
        # R3：forbidden 元素同样可能是 LLM 输出的任意 JSON 值，非字符串直接
        # 参与成员判断会在 dict/int 上抛 TypeError，炸穿 F1 兜底
        leaked = [w for w in forbidden if isinstance(w, str) and w and w in speak]
        parse_failed = (not speak.strip() or speak.strip() == "……")
        if not leaked and not parse_failed:
            break
        if leaked:
            # P4：给"改写策略"而非纯否定式禁令——只堵字面，模型会换个说法继续泄露语义
            feedback = (f"\n\n⚠️ 你刚才的发言泄露了秘密（提到了：{'、'.join(leaked)}）。"
                        "重写时请执行以下策略，而不只是回避字眼：\n"
                        "1. 彻底删除该信息的任何表述——包括暗示、比喻、部分提及；\n"
                        "2. 用一个对你有利的替代话题填充（如反问对方行踪、提起另一条已公开线索）；\n"
                        "3. 语气保持自然：刻意回避本身会露馅，要让「不提」看起来像「不在意」。")
        else:
            feedback = "\n\n⚠️ 输出格式有误，请严格输出包含 think 和 speak 两个字段的 JSON。"

    # 确定性脱敏（F8 改进：整句替换为回避话术，而非 □ 欲盖弥彰）。
    # 放在循环外而不是 for-else：超预算 break 时也要兜底，不能漏掉最后一句。
    if any(isinstance(w, str) and w and w in speak for w in forbidden):
        speak = "此事我不想多谈，你们与其盯着我，不如去问问别人。"
    # 发言里若出现名单外人物名，统一替换为「某人」，防止 AI 编出「张浩」之类角色。
    speak = _sanitize_text_for_script(speak, script)

    revealed = _update_revealed_clues(speak, (state.get("distributed_clues") or {}), (state.get("revealed_clues") or {}))
    new_memory = ((state.get("agent_memory") or {}).get(name, []) + [speak])[-3:]

    logger.info("node=ai_player_turn speaker=%s round=%d followup=%s cost=%.2fs",
                name, round_num, is_followup_reply, time.time() - t0)
    return {
        "messages": [{"speaker": name, "content": speak}],
        "thoughts": [f"{name}（内心）: {think}"],
        # M11：插队回应不计费（free_followup）——追问是加戏，不挤正常轮换；
        # 保险丝熔断后恢复计费。插队次数无论计费与否都如实累计（followup_count）。
        "phase_round": round_num + (0 if free_followup else 1),
        "followup_count": followup_count + (1 if is_followup_reply else 0),
        "revealed_clues": revealed,
        "agent_memory": {name: new_memory},
        **clear_pending,
    }


def human_turn_node(state: GameState) -> dict:
    """节点 4：轮到用户行动（interrupt 暂停，等用户发言或选动作）。"""
    user_role = state.get("user_role", "你")
    round_num = state.get("phase_round", 0)
    script = (state.get("script") or {})
    suspect_names = [s.get("name", "") for s in (script.get("suspects") or [])]
    targets = [n for n in suspect_names if n != user_role]
    own_clues = (state.get("distributed_clues") or {}).get(user_role, [])
    hidden = script.get("hidden_clues") or []
    investigated = state.get("investigated_clues") or []
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
        "locations": location_status(hidden, investigated),
    })

    # M9：沉默保留字归一化为 action dict（str 协议与 dict 协议在这里汇合，
    # 下面只需处理一个 kind == "silence" 分支）。
    if isinstance(action, str) and action.strip().lower() in SILENCE_WORDS:
        action = {"action": "silence"}

    investigated_new: list[str] = []
    if isinstance(action, dict):
        kind = action.get("action", "speak")
        if kind == "silence":
            # M9：保持沉默——占位消息入公开历史（对 AI 可见，他人可以解读你的沉默），
            # 不触发点名/追问守卫，轮次照常推进（防"永远沉默"卡死循环）。
            text = "（环视众人，沉默不语）"
            addressed = ""
            follow_up = 0
            consecutive = 0
            revealed = {}
        elif kind == "reveal_clue":
            clue = action.get("clue", "")
            # R7：归属校验——只能公开自己手里的线索（own_clues 含调查所得，M2）。
            # 官方 UI 均从白名单 select，此处是纵深防御：直连构造 resume 协议时，
            # 任意字符串不再能写进 revealed_clues 账本、进而注入全体 AI 的提示词
            # 与 DM 复盘。非法值降级为一条占位发言，不中断对局。
            if clue and clue in own_clues:
                text = f"我公开一条线索：{clue}"
                revealed = {clue: user_role}
            else:
                text = "（我翻遍口袋想公开点什么，却什么也没找着。）"
                revealed = {}
            addressed = ""
            follow_up = 0
            consecutive = 0
        elif kind == "accuse":
            target = action.get("target", "")
            text = f"我正式指控 {target} 是凶手！"
            # F5 守卫必须覆盖 accuse 分支：此前这里直接 follow_up=1，
            # 连续指控可无限续期追问权、烧穿讨论轮次预算（与 str/dict 发言分支不一致）。
            addressed, follow_up, consecutive = _apply_followup_guard(
                text, targets, state.get("follow_up", 0), state.get("consecutive_followups", 0)
            )
            revealed = _update_revealed_clues(text, (state.get("distributed_clues") or {}), (state.get("revealed_clues") or {}))
        elif kind == "investigate":
            location = str(action.get("location") or "").strip()
            # 如果 UI 指定了地点，只在该地点未发现的线索里选；旧协议不带 location 则回退全局随机。
            candidates = (
                clues_at_location(hidden, investigated, location)
                if location else available_hidden
            )
            if can_investigate and candidates:
                # M5：随机发放而非恒取 [0]——确定性顺序两局即背板，且 _ensure_clues
                # 的兜底线索恒在尾部，[0] 会让"保底线索"永远轮不到被调查。
                clue = random.choice(candidates)
                # M2：调查所得线索进入玩家私有渠道（distributed_clues[user_role]），
                # 公开频道只广播"去调查了"的动作，不广播原文——玩家自行决定何时公开、
                # 公开多少，保留藏牌博弈空间。线索也不计入 revealed_clues。
                where = f"在{location}" if location else "在现场"
                text = f"🔍 我去{where}调查了一番，发现了一些端倪……（线索已记入你的角色卡，可选择时机公开）"
                investigated_new = [clue]
                revealed = {}
            else:
                text = "我调查了一番，但没有新发现。" if not location else f"我在{location}仔细搜了一遍，但没有新发现。"
                revealed = {}
            addressed = ""
            follow_up = 0
            consecutive = 0
        else:
            # dict 协议的裸发言：与 str 协议走同一个守卫函数（M3）——
            # 此前 dict 分支没有 F5 守卫（连续点名可无限续期霸麦），接入即引爆。
            text = str(action.get("text", ""))[:MAX_CHAT_LEN]
            revealed = _update_revealed_clues(text, (state.get("distributed_clues") or {}), (state.get("revealed_clues") or {}))
            addressed, follow_up, consecutive = _apply_followup_guard(
                text, targets, state.get("follow_up", 0), state.get("consecutive_followups", 0))
    else:
        # str = 普通发言（F2：截断超长输入）。守卫逻辑与 dict 分支共用一份实现（M3）。
        # action 可能为 None（协议误用/空 resume）——直接 str(None) 会把 "None"
        # 当成台词广播给所有人，这里归一为空输入再降级成沉默占位。
        raw_text = "" if action is None else str(action)
        text = raw_text.strip()[:MAX_CHAT_LEN] or "（环视众人，沉默不语）"
        revealed = _update_revealed_clues(text, (state.get("distributed_clues") or {}), (state.get("revealed_clues") or {}))
        addressed, follow_up, consecutive = _apply_followup_guard(
            text, targets, state.get("follow_up", 0), state.get("consecutive_followups", 0))

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
        # M2：把调查线索并入玩家私有线索。distributed_clues 现在有合并 reducer
        # （game_state._merge_dict），只需返回"这一个角色"的增量，不必整字典替换——
        # 整字典替换在并行 superstep 下会把别人同时写入的线索覆盖掉。
        result["distributed_clues"] = {
            user_role: list((state.get("distributed_clues") or {}).get(user_role, [])) + investigated_new
        }
    return result


def _vote_one_player(name: str, secret: str, own_clues: list, history: str,
                     suspect_names: list[str], is_murderer: bool, task: str,
                     candidates: list[str] | None = None,
                     alibi_roster: str = "", revealed_text: str = "") -> tuple[str, str | None, str]:
    """单个 AI 玩家投票（H11：注入身份/任务，think+vote 双通道）。

    candidates：加时重投时只允许投平票候选人（H2）；None 表示全体嫌疑人。
    alibi_roster / revealed_text：H3/P1 的确定性事实层（不在场证明花名册 +
    已公开线索账本）——滑动窗口外的开场信息由此补回，投票不再"短期印象"。
    返回 (名字, 投票目标或 None, think 推理)。单个人调用失败不影响其他人。
    """
    allowed = candidates if candidates else suspect_names
    identity_hint = (
        "你是真凶。投票目标：投给一个有嫌疑的无辜者，绝不能投自己，尽量引导他人跟着你投。"
        if is_murderer else
        "你是无辜者。你的目标是根据线索和讨论投出你认为的真凶。"
    )
    if candidates:
        identity_hint += f"\n这是平票加时重投，你只能在 {'、'.join(candidates)} 中选择。"
    prompt = build_vote_prompt(name, secret, own_clues, history, allowed, identity_hint, task,
                               alibi_roster=alibi_roster, revealed_text=revealed_text)
    try:
        out = _parse_json(_safe_llm_text(prompt, purpose="vote", fallback="", json_mode=True))
        vote = out.get("vote", "")
        think = out.get("think", "")
        if vote in allowed and vote != name:
            return name, vote, think
    except Exception as e:
        logger.warning("AI 玩家 %s 投票失败（%s），算弃权", name, type(e).__name__)
    return name, None, ""


def ai_vote_node(state: GameState) -> dict:
    """节点 5：AI 玩家投票（逐人投票，信息差闭环）。

    H6：用线程池并发调用（httpx.Client 线程安全），可通过 VOTE_CONCURRENT=0 回退串行。
    H1：弃权显式写 None，覆盖上一轮旧票（合并 reducer 下不写会残留首轮票）。
    H2：加时重投时只投 tie_candidates 中的平票者。
    H3/P1：注入不在场证明花名册 + 已公开线索账本（确定性事实层，零 LLM 成本），
    滑动窗口外的开场信息补回投票决策——推理闭环在最后一环不再断裂。
    C1：vote_round 在此递增，作为"只加时一次"的硬保险丝。
    """
    script = (state.get("script") or {})
    suspects = script.get("suspects") or []
    user_role = state.get("user_role", "")
    suspect_names = [s.get("name", "?") for s in suspects]
    ai_names = [n for n in suspect_names if n != user_role]
    murderer = _get_murderer(script, suspect_names)

    # H2：加时轮只允许投平票候选人；首轮为全体嫌疑人
    candidates = state.get("tie_candidates") or None

    distributed = (state.get("distributed_clues") or {})
    history = "\n".join(
        f"{m.get('speaker', '?')}: {m.get('content', '')}" for m in (state.get("messages") or [])[-max(10, len(suspects) * 2):] if isinstance(m, dict)
    )

    # H3/P1：两个确定性事实层——花名册来自 script.suspects（开场自我介绍的声称），
    # 账本来自 revealed_clues（讨论中被公开过的私密线索）。纯 Python 拼接。
    alibi_roster = "\n".join(
        f"- {s.get('name', '?')}：{s.get('alibi', '未提供')}"
        for s in suspects if isinstance(s, dict)
    )
    revealed_all = (state.get("revealed_clues") or {})
    revealed_text = ("\n".join(f"- {holder}公开过：{clue}" for clue, holder in revealed_all.items())
                     if revealed_all else "")

    def _vote(name: str):
        secret = next((s.get("secret", "") for s in suspects if s.get("name") == name), "")
        task = next((s.get("task", "") for s in suspects if s.get("name") == name), "")
        # 注意：传 candidates（首轮 None / 加时轮平票名单），不是 allowed，
        # 否则首轮会被误判为"加时重投"而追加错误提示
        return _vote_one_player(name, secret, distributed.get(name, []), history,
                                suspect_names, name == murderer, task, candidates=candidates,
                                alibi_roster=alibi_roster, revealed_text=revealed_text)

    results = _run_concurrently(_vote, ai_names, label="AI 投票")

    votes = {}
    thoughts = []
    for voter, target, think in results:
        # H1：无论投出还是弃权都显式写入，None 覆盖上一轮旧票
        votes[voter] = target
        if think:
            thoughts.append(f"{voter}（投票内心）: {think}")

    out = {
        "votes": votes,
        "current_phase": "vote",
        "vote_round": state.get("vote_round", 0) + 1,   # C1 保险丝
    }
    if thoughts:
        out["thoughts"] = thoughts
    return out


# M9：投票弃权保留字——UI 输入这些词走显式弃权（None），而非"非法输入"循环纠错。
# 定义在 interrupt_handler.py（app.py / main.py 的 UI 层共用同一份），此处直接引用。
_ABSTAIN_WORDS = ABSTAIN_WORDS


def human_vote_node(state: GameState) -> dict:
    """节点 6：用户投票（interrupt 暂停，等用户输入）。

    H1：弃权显式写 None，覆盖首轮旧票。
    H2：加时重投时只允许投 tie_candidates 中的平票者。
    M9：保留字（弃权/弃权不投/abstain）显式转 None——"沉默观察、弃权自保"
    是剧本杀的真实策略，规则层有 None 通道，此处把它接通到输入协议。
    2026-11：候选人剔除玩家自己——AI 侧 prompt 明确"不能投自己"且
    _vote_one_player 有 `vote != name` 校验，玩家侧却能把票投给自己，
    同一套规则两副面孔（自投会让"平民胜利/凶手胜利"判定失真）。
    """
    user_role = state.get("user_role", "你")
    script = (state.get("script") or {})
    suspect_names = [s.get("name", "?") for s in script.get("suspects") or []]
    candidates = state.get("tie_candidates") or suspect_names
    # 剔除自己；极端情况（只剩 1 名嫌疑人）回退原名单，保证投票环节不会无选项可用
    others = [n for n in candidates if n != user_role]
    candidates = others or candidates

    user_vote = interrupt({"type": "human_vote", "suspects": candidates})

    # M9：弃权保留字规范化（strip + 大小写不敏感）
    raw = user_vote.strip() if isinstance(user_vote, str) else user_vote
    if isinstance(raw, str) and raw.lower() in _ABSTAIN_WORDS:
        return {"votes": {user_role: None}}
    if user_vote in candidates:
        return {"votes": {user_role: user_vote}}
    # 非法值/弃权：显式写 None，避免合并 reducer 残留上一轮旧票
    return {"votes": {user_role: None}}


def tally_node(state: GameState) -> dict:
    """节点 7：统计票数（确定性节点，纯 Python 逻辑）。

    H1：过滤 None（弃权票），只统计有效票。
    L4：平票信息收编为结构化字段——并列名单写入 tie_candidates。
    2026-11：vote_winner 不再兼任"平票"/"无人投票"哨兵字符串，改由
    vote_outcome（winner / tie / no_votes）表达结果类型，vote_winner 只存
    真实人名或空串。此前 4 处消费点靠 `startswith("平票")` / `== "无人投票"`
    嗅探语义，任何一处改文案就静默失配。
    """
    votes = {k: v for k, v in (state.get("votes") or {}).items() if v}
    counter = Counter(votes.values())
    vote_counts = dict(counter)
    if not counter:
        return {"vote_counts": {}, "vote_winner": "", "vote_outcome": "no_votes",
                "tie_candidates": [], "game_result": "平局"}

    top = counter.most_common()
    max_n = top[0][1]
    winners = [k for k, v in top if v == max_n]

    script = (state.get("script") or {})
    murderer = _get_murderer(script, [s.get("name", "") for s in script.get("suspects") or []])
    if len(winners) > 1:
        return {"vote_counts": vote_counts, "vote_winner": "", "vote_outcome": "tie",
                "tie_candidates": winners, "game_result": "平局"}
    vote_winner = winners[0]
    game_result = "平民胜利" if vote_winner == murderer else "凶手胜利"

    return {"vote_counts": vote_counts, "vote_winner": vote_winner, "vote_outcome": "winner",
            "tie_candidates": [], "game_result": game_result}


def tie_break_node(state: GameState) -> dict:
    """节点 7.4：平票加时辩护（H15/C1/M1/M9/M13/L4）。平票的 AI 候选人各做一句最后辩护，然后重投一轮。

    真人候选人不在这里辩护（玩家无法在 interrupt 外发言），但第二轮 human_vote
    会给玩家改票机会。
    L4：候选人直接读 tie_candidates（tally 写入的结构化字段）——
    此前从"平票（A、B）"字符串剥壳解析，名字含"平/票/（/）"等字即被截断。
    M13：候选人辩护并发调用（与 H1 自我介绍同构——平票是情绪高点，
    紧接着 6~24 秒无反馈静止最伤体验），map 保序 + 失败回退串行。
    """
    # L4：结构化字段直读，字符串剥壳逻辑整体删除
    tied_names: list[str] = list(state.get("tie_candidates") or [])
    user_role = state.get("user_role", "")
    script = (state.get("script") or {})
    suspects = script.get("suspects") or []
    suspect_map = {s.get("name"): s for s in suspects if isinstance(s, dict)}
    murderer = _get_murderer(script, [s.get("name", "") for s in suspects])

    def _defense(name: str) -> dict:
        suspect = suspect_map.get(name) or {}
        prompt = build_tiebreak_prompt(name, suspect.get("secret", "无"), name == murderer)
        resp = _safe_llm_text(prompt, purpose="player", allow_partial=False,
                              fallback=f"{name}沉默片刻，似乎有难言之隐。")
        resp = _sanitize_text_for_script(resp, script)
        return {"speaker": name, "content": f"（平票辩护）{resp}"}

    # 真人玩家自己辩护不替它生成；并发执行、按 tied_names 顺序输出（map 保序）
    targets = [n for n in tied_names if n != user_role and n in suspect_map]
    messages = _run_concurrently(_defense, targets, label="平票辩护")

    # C1：tie_break_done + tie_candidates 均已在 GameState 声明，
    # 后者供 ai_vote/human_vote 收窄加时轮投票名单（H2）。
    out: dict = {"tie_break_done": True, "tie_candidates": tied_names}
    if messages:
        out["messages"] = messages
    return out


def human_tiebreak_node(state: GameState) -> dict:
    """平票加时：如果玩家是候选人，给玩家一次最后陈述机会。

    此前 tie_break_node 只给 AI 候选人辩护，玩家被平票时只能直接重投——
    但玩家本局可能一直被怀疑，最后却没有“自辩”机会，体验和规则都不公平。
    """
    user_role = state.get("user_role", "")
    tied = state.get("tie_candidates") or []
    if user_role not in tied:
        return {}
    statement = interrupt({
        "type": "human_tiebreak",
        "speaker": user_role,
        "suspects": tied,
    })
    if isinstance(statement, str) and statement.strip().lower() in SILENCE_WORDS:
        text = "（环视众人，沉默不语）"
    else:
        raw = "" if statement is None else str(statement)
        text = raw.strip()[:MAX_CHAT_LEN] or "（张了张嘴，最终没有说出什么）"
    return {"messages": [{"speaker": user_role, "content": text}]}


def final_statement_node(state: GameState) -> dict:
    """节点 7.5：被投最高者的最终陈词。"""
    vote_winner = state.get("vote_winner", "")
    vote_outcome = state.get("vote_outcome", "winner")
    user_role = state.get("user_role", "")
    script = (state.get("script") or {})

    # L5：平票/无人投票/玩家自己被投时都不生成陈词。
    # 2026-11：判定改用 vote_outcome，不再对 vote_winner 做 startswith("平票") 嗅探。
    if vote_outcome != "winner" or not vote_winner or vote_winner == user_role:
        return {}

    suspects = script.get("suspects") or []
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
    resp = _sanitize_text_for_script(resp, script)
    return {
        "messages": [{"speaker": vote_winner, "content": f"（最终陈词）{resp}"}],
    }


def _render_vote(target) -> str:
    """投票目标展示归一化：None/空 →「（弃权）」（R1）。

    H1 让弃权以显式 None 写进 votes（合并 reducer 下覆盖旧票），但 None 是
    状态层语义；展示层若不归一，f"投{None}" 会让 DM 台词出现"张三投None"。
    app.py / main.py 结算页早已做 `target or '（弃权）'`，此处补齐第三处消费点。
    """
    return target if isinstance(target, str) and target else "（弃权）"


def _format_private_clues(distributed_clues: dict) -> str:
    """把 {角色名: [私密线索...]} 格式化成文本清单。"""
    lines = []
    for holder, clues in (distributed_clues or {}).items():
        for c in clues or []:
            lines.append(f"- {holder}：{c}")
    return "\n".join(lines) if lines else "（没有私密线索）"


def dm_reveal_node(state: GameState) -> dict:
    """节点 8：DM 揭晓真相 + 对比投票结果 + 线索复盘（信息差闭环）。"""
    script = (state.get("script") or {})
    truth = script.get("truth", "")
    votes = (state.get("votes") or {})
    vote_counts = (state.get("vote_counts") or {})
    vote_outcome = state.get("vote_outcome", "")
    vote_winner = state.get("vote_winner", "")

    if vote_outcome == "tie":
        winner_note = "（注意：这是平票，务必如实说明「多票并列」，不要假装有单一赢家）"
        winner_display = "（平票，无单一最高票）"
    elif vote_outcome == "no_votes":
        winner_note = "（注意：本局无人投出有效票，请如实说明「全员弃权」）"
        winner_display = "（无人投票）"
    else:
        winner_note = ""
        winner_display = vote_winner
    # R1：弃权票（None）归一为「（弃权）」，此前会渲染成 "投None" 进入 DM 台词
    votes_text = "、".join(f"{k}投{_render_vote(v)}" for k, v in votes.items()) or "无人投票"

    user_role = state.get("user_role", "你")
    user_vote = _render_vote(votes.get(user_role))

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
    clues_text = _format_private_clues((state.get("distributed_clues") or {}))
    # DM 揭晓只需要最近一段讨论来复盘；完整历史在长局里会白白烧 token。
    # 保留最后 40 条（约 2–3 轮），truth / 线索 / 投票账本单独传给 prompt。
    recent_messages = (state.get("messages") or [])[-40:]
    history = "\n".join(
        f"{m.get('speaker', '?')}: {m.get('content', '')}"
        for m in recent_messages if isinstance(m, dict)
    )

    prompt = build_reveal_prompt(
        truth=truth, votes_text=votes_text, vote_counts=vote_counts,
        vote_winner=winner_display, winner_note=winner_note, user_role=user_role,
        user_vote=user_vote, result_note=result_note, clues_text=clues_text,
        history=history, ending_label=ending_label, ending_note=ending_note,
    )
    resp = _safe_llm_text(prompt, purpose="dm", allow_partial=False,
                          fallback="主持人公布了投票结果，揭开了案件真相。")
    resp = _sanitize_text_for_script(resp, script)
    return {
        "messages": [{"speaker": "主持人", "content": resp}],
        "current_phase": "reveal",
    }


def _current_speaker(state: GameState) -> dict:
    """根据"上一位实际发言者"确定"这一轮轮到谁"（F3 修复：跳过主持人等名单外 speaker）。

    之前读 messages 末位 speaker，若末位是"主持人"（中场引导后）则回退到 suspects[0]，
    导致轮换重置、有人被跳过。现在从后向前找最近一位嫌疑人发言者。
    """
    script = (state.get("script") or {})
    suspects = script.get("suspects") or []
    if not suspects:
        return {}
    names = {s.get("name") for s in suspects if isinstance(s, dict)}
    # 从后向前找最近一位嫌疑人发言者，忽略主持人/DM/系统消息
    for m in reversed(state.get("messages") or []):
        if not isinstance(m, dict):
            continue
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
    script = (state.get("script") or {})
    suspects = script.get("suspects") or []
    if not suspects:
        return "vote"

    rounds_per_player = state.get("rounds_per_player", ROUNDS_PER_PLAYER)
    max_rounds = len(suspects) * rounds_per_player
    if round_num >= max_rounds:
        return "vote"

    if not state.get("midpoint_done") and round_num >= max_rounds // 2:
        return "midpoint"

    # F5：连续追问超过上限后强制回到正常轮换，防止玩家霸麦。
    # C3 修复：用 > 而非 >=。MAX_CONSECUTIVE_FOLLOWUPS=1 表示"允许 1 次追问"，
    # consecutive=1 时（刚点名、欠玩家一次追问）不应被限制；防霸麦的真实闸门
    # 是 human_turn_node 里的"追问不续期"，此处只是兜底。
    consecutive = state.get("consecutive_followups", 0)
    follow_up_limited = consecutive > MAX_CONSECUTIVE_FOLLOWUPS

    if state.get("pending_reply_to", ""):
        return "ai"

    if state.get("follow_up", 0) > 0 and not follow_up_limited:
        return "human"

    speaker = _current_speaker(state)
    if speaker.get("name") == state.get("user_role", ""):
        return "human"
    return "ai"


def route_after_tie_break(state: GameState) -> str:
    """tie_break 后的条件边：玩家在平票名单里才进入 human_tiebreak，否则直接 AI 重投。"""
    user_role = state.get("user_role", "")
    return "human_tiebreak" if user_role in (state.get("tie_candidates") or []) else "ai_vote"


def route_after_tally(state: GameState) -> str:
    """tally 后的条件边（H15/C1）：平票且未加时过 → tie_break，否则 → final_statement。

    C1 修复：tie_break_done 已在 GameState 声明（否则被 LangGraph 静默丢弃）；
    再加 vote_round 硬保险丝——即便守卫逻辑出错，第二轮投票后也强制结算，
    杜绝连续平票的无界重投循环。
    2026-11：平票判定读结构化 vote_outcome，不再 `startswith("平票")`。
    """
    already = state.get("tie_break_done", False) or state.get("vote_round", 1) >= 2
    if state.get("vote_outcome") == "tie" and not already:
        return "tie_break"
    return "final_statement"

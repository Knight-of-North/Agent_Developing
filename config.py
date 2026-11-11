"""
集中配置 —— 温度 / 重试 / 轮数 / 限制等可调参数统一在此管理。

之前这些值散落在 nodes.py 里硬编码（temperature=0.8 所有节点共用、
ROUNDS_PER_PLAYER=3 等），现在按节点用途区分，并支持环境变量覆盖。

⚠️ 加载时序（2026-11 修复）：本模块的常量在 **import 时**就求值完毕，
而 nodes.py 的 `load_dotenv()` 发生在 `from config import ...` 之后——
导致 `.env` 里的 24 项配置全部被静默忽略（只有调用期才读的 DEEPSEEK_* 生效）。
修法：把 load_dotenv 放到本模块最顶部，先加载 .env 再读 os.getenv；
任何入口只要 import config，就能拿到 .env 的值，不依赖调用方的 import 顺序。
"""
import os

from dotenv import load_dotenv

# 必须在任何 os.getenv 之前执行；重复调用是幂等的，不覆盖已存在的真实环境变量。
load_dotenv()


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _get_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ---- LLM 按用途分组配置（剧本要创造性、投票要稳定） ----
LLM_CONFIG = {
    "script": {"temperature": _get_float("LLM_TEMP_SCRIPT", 0.95), "max_tokens": _get_int("LLM_MAXTOK_SCRIPT", 8192)},
    "dm":     {"temperature": _get_float("LLM_TEMP_DM", 0.7),      "max_tokens": _get_int("LLM_MAXTOK_DM", 2048)},
    "player": {"temperature": _get_float("LLM_TEMP_PLAYER", 0.85), "max_tokens": _get_int("LLM_MAXTOK_PLAYER", 1024)},
    "vote":   {"temperature": _get_float("LLM_TEMP_VOTE", 0.3),    "max_tokens": _get_int("LLM_MAXTOK_VOTE", 512)},
}

LLM_TIMEOUT = _get_int("LLM_TIMEOUT", 120)
# R15：默认 5→3。langchain 层每次重试都可能吃满 LLM_TIMEOUT，5×120s 最坏让
# Streamlit UI 冻结约 10 分钟且无取消按钮；瞬时抖动 2~3 次已足够，持续故障下
# 快速失败 + _run_stream/main.safe_resume 的"重试"闭环体验更好。
LLM_MAX_RETRIES = _get_int("LLM_MAX_RETRIES", 3)

# L6：reasoning_effort 此前硬编码 "none"，违背"全参数可环境变量覆盖"的设计承诺。
# deepseek-v4 系列该参数对延迟/成本影响显著，正是最值得暴露的开关。
LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none")

# trust_env：之前硬编码 False（规避 streamlit 坏代理），但企业代理环境需要可配
TRUST_ENV = _get_bool("TRUST_ENV", False)

# ---- 游戏节奏 ----
ROUNDS_PER_PLAYER = _get_int("ROUNDS_PER_PLAYER", 3)

# 每局「调查」次数上限（H4：之前无限制，玩家可搜完所有隐藏线索）
INVESTIGATE_LIMIT = _get_int("INVESTIGATE_LIMIT", 2)

# 连续追问上限（F5：防止玩家点名霸麦饿死 AI）
MAX_CONSECUTIVE_FOLLOWUPS = _get_int("MAX_CONSECUTIVE_FOLLOWUPS", 1)

# ---- 用户输入限制（F2：防提示词注入 + 防超长输入烧 token） ----
MAX_THEME_LEN = _get_int("MAX_THEME_LEN", 50)
MAX_STORY_TIME_LEN = _get_int("MAX_STORY_TIME_LEN", 50)
MAX_STORY_LOCATION_LEN = _get_int("MAX_STORY_LOCATION_LEN", 50)
MAX_BG_STORY_LEN = _get_int("MAX_BG_STORY_LEN", 2000)
MAX_CHAT_LEN = _get_int("MAX_CHAT_LEN", 500)

# ---- 性能 ----
# AI 投票/自我介绍是否用线程池并发（H6）。httpx.Client 文档声明线程安全，
# 但若你的环境出现并发问题，设 VOTE_CONCURRENT=0 回退串行。
VOTE_CONCURRENT = _get_bool("VOTE_CONCURRENT", True)
VOTE_MAX_WORKERS = _get_int("VOTE_MAX_WORKERS", 5)

# ---- 调试 ----
# DEV_MODE=1 时在 Web 端显示 AI 内心戏、节点耗时等调试信息（H14）
DEV_MODE = _get_bool("DEV_MODE", False)

# ---- 剧本生成 ----
SCRIPT_MAX_ATTEMPTS = _get_int("SCRIPT_MAX_ATTEMPTS", 2)

# 单次 AI 发言的总时间预算（秒）。重试层数会相乘
# （节点 3 次尝试 × json_mode 的"JSON 失败→普通模式"回退 × SDK max_retries），
# 极端情况下一个回合可以发出十几次请求、把页面挂住十几分钟。
# 超过预算就停止重试、直接走确定性兜底文案——宁可一句过场，也不冻结 UI。
TURN_BUDGET_SECONDS = _get_int("TURN_BUDGET_SECONDS", 180)

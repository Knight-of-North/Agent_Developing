"""
集中配置 —— 温度 / 重试 / 轮数 / 限制等可调参数统一在此管理。

之前这些值散落在 nodes.py 里硬编码（temperature=0.8 所有节点共用、
ROUNDS_PER_PLAYER=3 等），现在按节点用途区分，并支持环境变量覆盖。
"""
import os


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
LLM_MAX_RETRIES = _get_int("LLM_MAX_RETRIES", 5)

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

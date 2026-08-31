"""
日志配置 —— 关键节点执行、LLM 调用失败、剧本校验问题都记录到文件+控制台。

之前 nodes.py 里 logger.warning 调用了但从没配置 handler，日志去向不明。
在 app.py / main.py 入口首行调用 setup_logging() 即可。

M12：用 contextvars 注入对局标识（gid）。Web 端两个用户同时游玩时，
logs/game.log 里两局的消息交错无法归属——排查"第 3 局的 AI 为什么全程说……"
时 grep g=ab12cd3 即可整局过滤。局限：ThreadPoolExecutor 工作线程不继承
提交时刻的 context（并发投票/自我介绍线程内的日志 gid 为 "-"），
主线程（图执行、节点函数）日志全部带 gid。
"""
import os
import sys
import logging
import contextvars
from logging.handlers import RotatingFileHandler

_CONFIGURED = False

# 当前对局的短标识（thread_id 前 8 位；uuid4 全长太吵）。默认 "-" 表示未开局。
_game_id: contextvars.ContextVar[str] = contextvars.ContextVar("game_id", default="-")


def set_game_id(tid: str) -> None:
    """把当前对局 thread_id 注入日志上下文（app.py / main.py 开局处调用）。"""
    _game_id.set((tid or "-")[:8])


class _GameFilter(logging.Filter):
    """给每条记录塞 gid 字段（挂在 handler 上，对所有来源的记录生效）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.gid = _game_id.get()
        return True


def setup_logging(level: int = logging.INFO) -> None:
    """幂等配置根 logger：文件（按大小轮转）+ 控制台。多次调用安全。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] g=%(gid)s %(name)s: %(message)s")
    game_filter = _GameFilter()

    fh = RotatingFileHandler(
        os.path.join(log_dir, "game.log"),
        maxBytes=2_000_000, backupCount=5, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.setLevel(level)
    fh.addFilter(game_filter)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(level)
    sh.addFilter(game_filter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(fh)
    root.addHandler(sh)

    _CONFIGURED = True

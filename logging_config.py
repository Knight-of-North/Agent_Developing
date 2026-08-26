"""
日志配置 —— 关键节点执行、LLM 调用失败、剧本校验问题都记录到文件+控制台。

之前 nodes.py 里 logger.warning 调用了但从没配置 handler，日志去向不明。
在 app.py / main.py 入口首行调用 setup_logging() 即可。
"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """幂等配置根 logger：文件（按大小轮转）+ 控制台。多次调用安全。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    fh = RotatingFileHandler(
        os.path.join(log_dir, "game.log"),
        maxBytes=2_000_000, backupCount=5, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.setLevel(level)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(fh)
    root.addHandler(sh)

    _CONFIGURED = True

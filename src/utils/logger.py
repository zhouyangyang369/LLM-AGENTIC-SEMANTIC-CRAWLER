"""
统一日志配置
"""
import logging
import sys
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
) -> None:
    """
    配置根 Logger。

    Args:
        level: 日志级别（DEBUG / INFO / WARNING / ERROR）
        log_file: 日志文件路径（None 只输出到终端）
    """
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout)
    ]

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        datefmt=datefmt,
        handlers=handlers,
        force=True,
    )

    # 抑制第三方库的冗长日志
    for noisy in ["httpx", "httpcore", "openai", "tavily"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)
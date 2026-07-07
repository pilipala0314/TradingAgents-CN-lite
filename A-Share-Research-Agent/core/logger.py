import logging
import sys

from core.config import settings

LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | %(message)s"
)


def setup_logging() -> None:
    """统一初始化项目日志，避免各模块各自配置导致格式漂移。"""
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format=LOG_FORMAT,
        stream=sys.stdout,
    )

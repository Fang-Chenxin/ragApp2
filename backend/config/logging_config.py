"""日志配置模块 - 统一管理后端日志输出

策略：
- 控制台：INFO 级别，只显示服务启动/关闭、连接、错误等关键信息，格式紧凑
- 文件：DEBUG 级别，记录完整的处理过程（LLM 轮次、工具调用、耗时等），便于事后分析
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional


_LOG_INITIALIZED = False

# 控制台格式：尽量接近原始输出，只显示消息本身
_CONSOLE_FORMAT = "%(message)s"
# 文件格式：纯消息，避免每行都占用大量宽度
_FILE_FORMAT = "%(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _ConsoleProcessingNoiseFilter(logging.Filter):
    """终端只保留用户真正需要看到的后端运行信息。"""

    _QUIET_LOGGERS = (
        "service.tool_chat",
        "service.rag",
        "service.sqlite_product",
        "service.product_search.query_tool",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith(self._QUIET_LOGGERS) and record.levelno < logging.ERROR:
            return False
        return True


def setup_logging(
    log_level: str = "DEBUG",
    log_file: Optional[str] = None,
    console_level: str = "INFO",
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> None:
    """初始化全局日志配置

    Args:
        log_level: 文件日志级别，控制写入文件的详细程度
        log_file: 日志文件路径，为 None 则不写文件
        console_level: 控制台日志级别，控制终端显示的信息量
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的历史日志文件数
    """
    global _LOG_INITIALIZED
    if _LOG_INITIALIZED:
        return
    _LOG_INITIALIZED = True

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # 根 logger 设为最低，由各 handler 过滤

    # 清除已有 handler（避免重复）
    root_logger.handlers.clear()

    # 控制台 handler：INFO 级别，紧凑格式
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(getattr(logging, console_level.upper(), logging.INFO))
    console_handler.setFormatter(logging.Formatter(_CONSOLE_FORMAT, _DATE_FORMAT))
    console_handler.addFilter(_ConsoleProcessingNoiseFilter())
    root_logger.addHandler(console_handler)

    # 文件 handler：DEBUG 级别，完整格式
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
        file_handler.setFormatter(logging.Formatter(_FILE_FORMAT, _DATE_FORMAT))
        root_logger.addHandler(file_handler)

    # 降低第三方库日志级别，避免噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """获取指定模块的 logger"""
    return logging.getLogger(name)

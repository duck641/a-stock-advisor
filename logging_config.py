"""
日志配置模块 — 项目全局统一使用此配置

用法：在每个需要记日志的文件里加两行：
    import logging
    logger = logging.getLogger(__name__)
"""
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"


def setup_logging():
    """初始化日志系统（幂等，多次调用不会重复添加 handler）"""
    LOG_DIR.mkdir(exist_ok=True)

    # 格式：时间 [模块] 级别 消息
    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 避免重复添加（如果被 import 多次）
    if not root.handlers:
        # 控制台输出
        console = logging.StreamHandler()
        console.setLevel(logging.INFO)
        console.setFormatter(fmt)
        root.addHandler(console)

        # 文件输出 — 每天午夜自动切新文件，保留 7 天
        try:
            file_handler = TimedRotatingFileHandler(
                filename=LOG_DIR / "agent.log",
                when="midnight",
                backupCount=7,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except PermissionError:
            root.warning("日志文件无写权限，仅输出到控制台")

    # 静音第三方库的 HTTP 请求日志（httpx/openai/httpcore）
    for lib in ("httpx", "openai", "httpcore"):
        logging.getLogger(lib).setLevel(logging.WARNING)


# 模块被 import 时自动执行一次
setup_logging()

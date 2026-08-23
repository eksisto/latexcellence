"""
统一日志系统。

基于 Python 标准库 logging + TimedRotatingFileHandler 实现：
    1. 文件日志：按日期自动旋转，保留最近 30 天，DEBUG 级别
    2. 控制台日志：开发时直接查看，级别由 config 控制
    3. 第三方库降噪：httpx/openai/httpcore 等库的日志降为 WARNING
    4. 容器模式（v2 P7）：env ``LATEXCELLENCE_LOG_STDOUT=1`` 时只输出
       stdout/stderr、不建日志文件——TimedRotatingFileHandler 多进程共写
       同一文件在轮转时存在 rename 竞争，多副本部署交 docker logs 收集

关键日志记录点（各模块应记录）：
    - LLM 请求/响应（含 token 消耗）
    - 文件读写操作（含备份/撤销记录）
    - API 异常捕获
    - 各模块功能入口/出口（参数 + 耗时）
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# 容器/多实例模式开关：仅接受 "1"/"true"（大小写不敏感）为开启，
# 其余取值（含未设置）维持裸机默认的按天轮转文件日志。
LOG_STDOUT_ENV = "LATEXCELLENCE_LOG_STDOUT"


def stdout_logging_enabled() -> bool:
    """是否启用纯 stdout/stderr 日志模式（容器模式交 docker logs 收集）。"""
    return os.environ.get(LOG_STDOUT_ENV, "").strip().lower() in ("1", "true")


def setup_logging(level: str = "INFO", log_dir: str = "logs/") -> None:
    """
    初始化全局日志系统。

    参数：
        level: 日志级别（DEBUG/INFO/WARNING/ERROR），控制控制台输出
        log_dir: 日志文件输出目录（stdout 模式下忽略）

    效果：
        - 默认：创建日志目录 + 按日期旋转的文件处理器（保留 30 天备份，
          DEBUG 级别）+ 控制台处理器（按 level 参数控制级别）
        - ``LATEXCELLENCE_LOG_STDOUT=1``：只添加控制台处理器（多副本容器
          部署用，规避 TimedRotatingFileHandler 的多进程轮转竞争）
        - 降低第三方 HTTP 库的日志级别以减少干扰
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 幂等：仅移除本模块此前添加的处理器（打标识别，不碰 pytest 等第三方
    # 挂在 root 上的捕获处理器），避免重复初始化导致重复输出
    for h in list(root_logger.handlers):
        if getattr(h, "_latexcellence_owned", False) or isinstance(
            h, TimedRotatingFileHandler
        ):
            root_logger.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响后续处理
                pass

    # 日志格式：时间 | 级别 | 模块名 | 消息
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not stdout_logging_enabled():
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)

        # 文件处理器：按天旋转，保留 30 天，记录所有级别（DEBUG+）
        # 用于长期存档和问题排查。注意：多进程共写同一文件在轮转时存在
        # rename 竞争——容器/多实例部署必须使用 LATEXCELLENCE_LOG_STDOUT=1。
        file_handler = TimedRotatingFileHandler(
            filename=log_path / "latexcellence.log",
            when="midnight",  # 每天午夜切换日志文件
            interval=1,
            backupCount=30,  # 保留最近 30 天的日志
            encoding="utf-8",
        )
        file_handler._latexcellence_owned = True  # type: ignore[attr-defined]
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # 控制台处理器：级别由参数控制，方便开发时阅读；容器模式下是唯一出口
    console_handler = logging.StreamHandler()
    console_handler._latexcellence_owned = True  # type: ignore[attr-defined]
    console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # 降噪：第三方 HTTP 客户端库的日志太嘈杂，仅在出错时显示
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    获取指定名称的日志器。

    用法：
        logger = get_logger("typo_check")
        logger.info("Processing paragraph 5/12")

    参数：
        name: 日志器名称（建议使用模块名或类名）

    返回：
        logging.Logger 实例
    """
    return logging.getLogger(name)

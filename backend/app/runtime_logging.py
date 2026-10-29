from __future__ import annotations

import atexit
import logging
import os
import platform
import queue
import sys
import threading
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path

from app import paths


LOG_FILE_NAME = "staffdeck.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
LOG_QUEUE_CAPACITY = 10_000
RUNTIME_LOGGER_NAMES = (
    "staffdeck.runtime",
    "staffdeck.static",
    "staffdeck.crash",
)

_configured_path: Path | None = None
_queue_handler: _DroppingQueueHandler | None = None
_file_handler: _RuntimeFileHandler | None = None
_listener: _RuntimeQueueListener | None = None
_atexit_registered = False


class _RuntimeFileHandler(RotatingFileHandler):
    """标记用子类，仅用于识别并替换已存在的 StaffDeck 文件处理器。"""


class _DroppingQueueHandler(QueueHandler):
    """日志队列写满时丢弃记录，绝不阻塞业务线程。"""

    def __init__(self, record_queue: queue.Queue[logging.LogRecord]) -> None:
        """绑定目标队列，并把丢弃计数初始化为 0。"""
        super().__init__(record_queue)
        self.dropped_records = 0

    def enqueue(self, record: logging.LogRecord) -> None:
        """非阻塞入队；队列已满时累加 dropped_records 并丢弃该条记录。"""
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped_records += 1


class _RuntimeQueueListener(QueueListener):
    """仅在关闭时可能短暂等待写入线程的队列监听器。"""

    def enqueue_sentinel(self) -> None:
        """向队列投放停止哨兵；关闭流程可短暂等待写入线程，请求线程不会。"""
        # Shutdown may wait briefly for the writer, but normal request threads never do.
        self.queue.put(self._sentinel)


class _NullTextStream:
    """PyInstaller 窗口模式下缺少 stdio 句柄时的可写占位输出流。"""

    encoding = "utf-8"

    def write(self, message: str) -> int:
        """丢弃写入内容，仅返回字符数以满足文本流约定。"""
        return len(message)

    def flush(self) -> None:
        """空实现：这里没有需要冲刷的缓冲。"""
        return

    def isatty(self) -> bool:
        """始终返回 False，表明不是交互式终端。"""
        return False


def runtime_log_path() -> Path:
    """返回当前用户数据目录下的 StaffDeck 日志文件路径。"""
    return paths.user_data_dir() / "logs" / LOG_FILE_NAME


def configure_runtime_logging() -> Path:
    """配置带隐私边界的桌面日志，并返回当前生效的日志路径。

    日志路径变化时重建轮转文件、队列与监听器；同时补齐缺失的 stdio 并安装
    进程/线程异常钩子，最后写入一条运行时启动日志。
    """
    global _atexit_registered, _configured_path, _file_handler, _listener, _queue_handler

    log_path = runtime_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_path = log_path.resolve()

    if _configured_path != resolved_path:
        shutdown_runtime_logging()
        file_handler = _RuntimeFileHandler(
            resolved_path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s"
            )
        )

        record_queue: queue.Queue[logging.LogRecord] = queue.Queue(
            maxsize=LOG_QUEUE_CAPACITY
        )
        queue_handler = _DroppingQueueHandler(record_queue)
        listener = _RuntimeQueueListener(
            record_queue,
            file_handler,
            respect_handler_level=True,
        )
        for logger_name in RUNTIME_LOGGER_NAMES:
            runtime_logger = logging.getLogger(logger_name)
            for existing in list(runtime_logger.handlers):
                if isinstance(existing, _DroppingQueueHandler):
                    runtime_logger.removeHandler(existing)
            runtime_logger.addHandler(queue_handler)
            runtime_logger.setLevel(logging.INFO)
            runtime_logger.propagate = False

        listener.start()
        _configured_path = resolved_path
        _file_handler = file_handler
        _listener = listener
        _queue_handler = queue_handler
        if not _atexit_registered:
            atexit.register(shutdown_runtime_logging)
            _atexit_registered = True

    if sys.stdout is None:
        sys.stdout = _NullTextStream()
    if sys.stderr is None:
        sys.stderr = _NullTextStream()

    _install_exception_hooks()
    logging.getLogger("staffdeck.runtime").info(
        "Runtime started platform=%s release=%s python=%s frozen=%s pid=%s",
        platform.system(),
        platform.release(),
        platform.python_version(),
        bool(getattr(sys, "frozen", False)),
        os.getpid(),
    )
    return resolved_path


def shutdown_runtime_logging() -> None:
    """冲刷队列中待写记录，并关闭 StaffDeck 持有的日志资源。"""
    global _configured_path, _file_handler, _listener, _queue_handler

    listener = _listener
    queue_handler = _queue_handler
    file_handler = _file_handler
    _listener = None
    _queue_handler = None
    _file_handler = None
    _configured_path = None

    if listener is not None:
        listener.stop()
    if queue_handler is not None:
        for logger_name in RUNTIME_LOGGER_NAMES:
            runtime_logger = logging.getLogger(logger_name)
            runtime_logger.removeHandler(queue_handler)
        queue_handler.close()
    if file_handler is not None:
        file_handler.close()


def _install_exception_hooks() -> None:
    """接管进程与线程的未捕获异常钩子，把堆栈写入 staffdeck.crash 日志。"""
    def log_process_exception(exc_type, exc_value, traceback) -> None:
        """记录进程级未捕获异常；KeyboardInterrupt 仍交给默认钩子处理。"""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback)
            return
        logging.getLogger("staffdeck.crash").critical(
            "Unhandled process exception type=%s\n%s",
            exc_type.__name__,
            _format_stack(traceback),
        )

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        """记录线程级未捕获异常的类型与堆栈帧。"""
        logging.getLogger("staffdeck.crash").critical(
            "Unhandled thread exception type=%s\n%s",
            args.exc_type.__name__,
            _format_stack(args.exc_traceback),
        )

    sys.excepthook = log_process_exception
    threading.excepthook = log_thread_exception


def _format_stack(traceback) -> str:
    """把 traceback 链格式化为“文件名:行号 in 函数名”的缩进文本。"""
    if traceback is None:
        return "<no traceback>"
    frames: list[str] = []
    while traceback is not None:
        code = traceback.tb_frame.f_code
        frames.append(
            f"  {Path(code.co_filename).name}:{traceback.tb_lineno} in {code.co_name}"
        )
        traceback = traceback.tb_next
    return "\n".join(frames)

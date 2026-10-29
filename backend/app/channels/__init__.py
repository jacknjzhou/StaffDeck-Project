from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from app.config import get_settings

logger = logging.getLogger(__name__)

# 进程级 ingress 管理器单例(懒创建,测试可替换)
_wechat_poll_manager = None
_wecom_stream_manager = None
_feishu_process_manager = None
_dingtalk_stream_manager = None
_binding_lifecycle_locks: dict[str, threading.RLock] = {}
_binding_lifecycle_locks_guard = threading.Lock()
_connector_lock_file: IO[bytes] | None = None
_connector_lock_pid: int | None = None
_intake_sweep_thread: threading.Thread | None = None


def _acquire_connector_process_lock() -> bool:
    """获取进程级 connector 锁，保证每个数据库只有一个 connector 进程。

    非 SQLite（如 PostgreSQL）无法用文件 flock 隔离实例：配置了 REDIS_URL 时由
    Redis 角色租约保证独占，否则仅告警放行。fork 出的子进程会先关闭继承的句柄。
    """
    global _connector_lock_file, _connector_lock_pid
    current_pid = os.getpid()
    if _connector_lock_file is not None and _connector_lock_pid == current_pid:
        return True
    if _connector_lock_file is not None:
        # preload 后 fork 的子进程不能把继承句柄当作自己已持有锁。
        _connector_lock_file.close()
        _connector_lock_file = None
        _connector_lock_pid = None
    from app.db import engine

    database_path = engine.url.database
    if engine.url.get_backend_name() != "sqlite" or not database_path or database_path == ":memory:":
        # Server deployments run on PostgreSQL, where a per-file flock cannot
        # fence instances. Exclusivity is enforced instead by the Redis role
        # lease that on_startup() acquires for the "connector" capability before
        # _startup_by_role() runs, so reaching this point already means we own it.
        if get_settings().redis_url:
            return True
        logger.warning(
            "渠道服务在非 SQLite 数据库且未配置 REDIS_URL 时无单实例保护；"
            "请确保仅部署一个 connector 进程"
        )
        return True
    lock_path = Path(database_path).resolve().with_name(f"{Path(database_path).name}.connector.lock")
    handle = lock_path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return False
    _connector_lock_file = handle
    _connector_lock_pid = current_pid
    return True


def _release_connector_process_lock() -> None:
    """释放 connector 进程锁；锁不属于当前进程时只关闭句柄而不解锁。"""
    global _connector_lock_file, _connector_lock_pid
    handle = _connector_lock_file
    if handle is None:
        return
    if _connector_lock_pid != os.getpid():
        handle.close()
        _connector_lock_file = None
        _connector_lock_pid = None
        return
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
        _connector_lock_file = None
        _connector_lock_pid = None


def get_wechat_poll_manager():
    """返回进程级微信轮询管理器单例，首次调用时懒创建。"""
    global _wechat_poll_manager
    if _wechat_poll_manager is None:
        from app.channels.adapters.wechat import WeChatPollManager

        _wechat_poll_manager = WeChatPollManager()
    return _wechat_poll_manager


def get_wecom_stream_manager():
    """返回进程级企微流式管理器单例，首次调用时懒创建。"""
    global _wecom_stream_manager
    if _wecom_stream_manager is None:
        from app.channels.adapters.wecom import WeComStreamManager

        _wecom_stream_manager = WeComStreamManager()
    return _wecom_stream_manager


def _feishu_supported() -> bool:
    """判断当前数据库能否承载飞书长连接 connector。

    子进程直接用 sqlite3 暂存入站消息，因此只支持文件型 SQLite；
    PostgreSQL 部署暂不可用（M1 服务端模式）。
    """
    from app.db import engine

    database = engine.url.database
    return (
        engine.url.get_backend_name() == "sqlite"
        and bool(database)
        and database != ":memory:"
    )


def get_feishu_process_manager():
    """返回进程级飞书长连接进程管理器单例，首次调用时懒创建。"""
    global _feishu_process_manager
    if _feishu_process_manager is None:
        from app.channels.feishu_manager import FeishuProcessManager

        _feishu_process_manager = FeishuProcessManager()
    return _feishu_process_manager


def _optional_feishu_manager():
    """在数据库可承载时返回飞书管理器，否则返回 None。

    绑定生命周期的暂停/恢复/等待逻辑必须在 PostgreSQL 部署上也能安全调用，
    因此这里吞掉管理器不可用时的 RuntimeError。
    """
    if not _feishu_supported():
        return None
    try:
        return get_feishu_process_manager()
    except RuntimeError:
        return None


def get_dingtalk_stream_manager():
    """返回进程级钉钉流式管理器单例，首次调用时懒创建。"""
    global _dingtalk_stream_manager
    if _dingtalk_stream_manager is None:
        from app.channels.adapters.dingtalk import DingTalkStreamManager

        _dingtalk_stream_manager = DingTalkStreamManager()
    return _dingtalk_stream_manager


def channel_services_enabled() -> bool:
    """判断当前 staffdeck_role 是否需要启动渠道服务（all 或 connector）。"""
    # staffdeck_role 预留角色拆分：all=单体全量，connector=仅渠道连接器
    return get_settings().staffdeck_role in {"all", "connector"}


def _ensure_adapters_registered() -> None:
    """导入各渠道适配器模块，触发模块级的适配器自注册。"""
    # 各适配器模块导入即自注册(模块级 register_channel_adapter)
    import app.channels.adapters.dingtalk  # noqa: F401
    import app.channels.adapters.feishu  # noqa: F401
    import app.channels.adapters.wechat  # noqa: F401
    import app.channels.adapters.wechat_kf  # noqa: F401
    import app.channels.adapters.wecom  # noqa: F401


def start_binding_ingress(channel: str, binding_id: str) -> None:
    """按注册表经适配器协议拉起指定绑定的 ingress。"""
    _ensure_adapters_registered()
    from app.channels.adapters.base import get_channel_adapter

    starter = getattr(get_channel_adapter(channel), "start_ingress", None)
    if callable(starter):
        starter(binding_id)


def stop_binding_ingress(channel: str, binding_id: str) -> None:
    """经适配器协议停止指定绑定的 ingress，适配器未实现时静默跳过。"""
    _ensure_adapters_registered()
    from app.channels.adapters.base import get_channel_adapter

    stopper = getattr(get_channel_adapter(channel), "stop_ingress", None)
    if callable(stopper):
        stopper(binding_id)


def _ingress_manager(channel: str):
    """按渠道返回对应的 ingress 管理器，未知渠道或飞书不可用时返回 None。"""
    if channel == "wechat":
        return get_wechat_poll_manager()
    if channel == "wecom":
        return get_wecom_stream_manager()
    if channel == "feishu":
        return _optional_feishu_manager()
    if channel == "dingtalk":
        return get_dingtalk_stream_manager()
    return None


@contextmanager
def binding_lifecycle_lock(binding_id: str):
    """串行化同一 binding 的重配/删除,避免两个 HTTP 请求交错切换代际。"""
    with _binding_lifecycle_locks_guard:
        lock = _binding_lifecycle_locks.setdefault(binding_id, threading.RLock())
    with lock:
        yield


def pause_binding_ingress(channel: str, binding_id: str) -> None:
    """暂停 reconcile 并停止当前 producer/consumer。"""
    _ensure_adapters_registered()
    manager = _ingress_manager(channel)
    pause = getattr(manager, "pause_binding", None)
    if callable(pause):
        pause(binding_id)
        return
    stop_binding_ingress(channel, binding_id)


def resume_binding_ingress(channel: str, binding_id: str, *, start: bool = True) -> None:
    """解除 reconcile 暂停;start=False 时由后续 reconcile 按数据库旧配置恢复。"""
    _ensure_adapters_registered()
    manager = _ingress_manager(channel)
    resume = getattr(manager, "resume_binding", None)
    if callable(resume):
        resume(binding_id, start=start)
        return
    if start:
        start_binding_ingress(channel, binding_id)


def wait_binding_ingress_stopped(channel: str, binding_id: str, timeout_seconds: float = 5.0) -> bool:
    """有界等待指定绑定的 ingress 线程退出(重配凭证前调用)。"""
    _ensure_adapters_registered()
    if channel == "wechat":
        return get_wechat_poll_manager().wait_binding_stopped(binding_id, timeout_seconds)
    if channel == "wecom":
        return get_wecom_stream_manager().wait_binding_stopped(binding_id, timeout_seconds)
    if channel == "feishu":
        manager = _optional_feishu_manager()
        if manager is None:
            return True
        return manager.wait_binding_stopped(binding_id, timeout_seconds)
    if channel == "dingtalk":
        return get_dingtalk_stream_manager().wait_binding_stopped(binding_id, timeout_seconds)
    return True


def restart_binding_ingress(channel: str, binding_id: str, *, wait_seconds: float = 5.0) -> bool:
    """兼容入口:只有旧代际完全退出才启动新代际。"""
    pause_binding_ingress(channel, binding_id)
    stopped = wait_binding_ingress_stopped(channel, binding_id, wait_seconds)
    resume_binding_ingress(channel, binding_id, start=stopped)
    return stopped


def start_channel_services() -> None:
    """启动渠道后台服务：各渠道 manager、投递与入站守护线程。

    非 connector 角色直接跳过；先抢进程级 connector 锁，失败即抛错。飞书依赖文件型
    SQLite，不满足时只告警跳过；任何步骤失败都会回滚已启动的服务后再抛出。
    """
    global _intake_sweep_thread
    if not channel_services_enabled():
        logger.info("staffdeck_role=%s,渠道服务不启动", get_settings().staffdeck_role)
        return
    if not _acquire_connector_process_lock():
        raise RuntimeError("检测到另一 connector 进程正在运行；每个数据库仅允许一个 connector")
    try:
        _ensure_adapters_registered()
        from app.channels.service_intake import (
            start_staged_inbound_daemon,
            sweep_stale_inbound_events,
        )
        from app.channels.service_outbox import start_delivery_daemon

        get_wechat_poll_manager().start()
        get_wecom_stream_manager().start()
        if _feishu_supported():
            get_feishu_process_manager().start()
        else:
            # The Feishu long-connection connector speaks sqlite3 directly from its
            # child process, so it cannot run against PostgreSQL yet. Skip it rather
            # than failing the whole connector role (other channels stay available).
            logger.warning(
                "飞书长连接 connector 需要文件 SQLite；当前数据库不支持，已跳过飞书渠道。"
                "其他渠道照常运行。"
            )
        get_dingtalk_stream_manager().start()
        start_delivery_daemon()
        start_staged_inbound_daemon()
        # 启动恢复:一次性清扫崩溃残留的 processing 入站事件(独立线程,不阻塞启动)
        _intake_sweep_thread = threading.Thread(
            target=sweep_stale_inbound_events,
            name="staffdeck-channel-intake-sweep",
            daemon=True,
        )
        _intake_sweep_thread.start()
    except Exception:
        stop_channel_services()
        raise


def stop_channel_services(timeout_seconds: float = 5.0) -> bool:
    """停止全部渠道后台线程，返回是否都在期限内退出。

    所有步骤共享一个单调时钟 deadline；全部停止成功才释放 connector 锁，否则保留到进程结束。
    """
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    from app.channels.service_intake import stop_staged_inbound_daemon
    from app.channels.service_outbox import stop_delivery_daemon

    intake_stopped = stop_staged_inbound_daemon(
        timeout_seconds=max(0.0, deadline - time.monotonic())
    )

    outbox_stopped = stop_delivery_daemon(
        timeout_seconds=max(0.0, deadline - time.monotonic())
    )
    poll_manager = _wechat_poll_manager
    wechat_stopped = poll_manager is None or poll_manager.stop(
        timeout_seconds=max(0.0, deadline - time.monotonic())
    )
    stream_manager = _wecom_stream_manager
    wecom_stopped = stream_manager is None or stream_manager.stop(
        timeout_seconds=max(0.0, deadline - time.monotonic())
    )
    feishu_manager = _feishu_process_manager
    feishu_stopped = feishu_manager is None or feishu_manager.stop(
        timeout_seconds=max(0.0, deadline - time.monotonic())
    )
    dingtalk_manager = _dingtalk_stream_manager
    dingtalk_stopped = dingtalk_manager is None or dingtalk_manager.stop(
        timeout_seconds=max(0.0, deadline - time.monotonic())
    )
    sweep_thread = _intake_sweep_thread
    if sweep_thread and sweep_thread.is_alive():
        sweep_thread.join(timeout=max(0.0, deadline - time.monotonic()))
    sweep_stopped = not (sweep_thread and sweep_thread.is_alive())
    stopped = (
        intake_stopped
        and outbox_stopped
        and wechat_stopped
        and wecom_stopped
        and feishu_stopped
        and dingtalk_stopped
        and sweep_stopped
    )
    if stopped:
        _release_connector_process_lock()
    else:
        logger.error("渠道线程未在期限内退出，保留 connector 锁直到进程结束")
    return stopped

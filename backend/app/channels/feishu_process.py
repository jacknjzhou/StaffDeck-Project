from __future__ import annotations

import multiprocessing
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from feishu_connector_worker import (
    BindingProcessLock,
    ConnectorChildSpec,
    PRODUCTION_RUNTIME,
    binding_lock_path,
    connector_child_entry,
)


class ConnectorState(str, Enum):
    """连接器进程的生命周期状态：缺失、启动中、运行中、停止中、崩溃退避。"""
    ABSENT = "absent"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    CRASH_BACKOFF = "crash_backoff"


@dataclass
class ConnectorRecord:
    """单个绑定对应的子进程句柄、IPC 连接、状态与看门狗计时数据。"""
    spec: ConnectorChildSpec
    process: Any
    connection: Any
    state: ConnectorState = ConnectorState.STARTING
    connected: bool = False
    dispatch_deadlines: dict[str, float] = field(default_factory=dict)
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=256))
    exit_observed: bool = False
    exit_code: int | None = None
    termination_phase: str | None = None
    termination_sent_at: float | None = None
    started_at: float = field(default_factory=time.monotonic)


class FeishuProcessSupervisor:
    """以 spawn 方式托管飞书长连接子进程：代次围栏、崩溃退避与有界停止。

    每个绑定最多一个存活进程；事件须同时匹配 binding_id、config_revision、
    child_nonce 与 pid 才被接纳，避免旧代次残留事件串入。停止按 STOP、
    terminate、kill 三级推进并受总时限约束，超时则返回 False 交调用方重试。
    """

    def __init__(
        self,
        *,
        runtime_path: str = PRODUCTION_RUNTIME,
        database_path: Path | None = None,
        data_dir: Path | None = None,
        watchdog_seconds: float = 2.5,
        terminate_grace_seconds: float = 0.2,
        max_processes: int = 20,
        crash_limit: int = 3,
        crash_window_seconds: float = 60.0,
        backoff_seconds: float = 60.0,
        max_backoff_seconds: float = 900.0,
        lock_wait_seconds: float = 3.0,
        parent_watchdog_grace_seconds: float = 0.1,
        startup_timeout_seconds: float = 10.0,
    ):
        """记录各项超时与上限参数，初始化锁、条件变量并启动后台监控线程。"""
        self._ctx = multiprocessing.get_context("spawn")
        self._runtime_path = runtime_path
        root = (data_dir or Path.cwd()).expanduser().resolve()
        self._database_path = (database_path or root / "skill_agent_loop.db").expanduser().resolve()
        self._watchdog_seconds = watchdog_seconds
        self._terminate_grace_seconds = terminate_grace_seconds
        self._max_processes = max_processes
        self._crash_limit = crash_limit
        self._crash_window_seconds = crash_window_seconds
        self._backoff_seconds = backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._lock_wait_seconds = lock_wait_seconds
        self._parent_watchdog_grace_seconds = parent_watchdog_grace_seconds
        self._startup_timeout_seconds = startup_timeout_seconds
        self._records: dict[str, ConnectorRecord] = {}
        self._reserved_process_slots = 0
        self._crashes: dict[str, deque[float]] = {}
        self._backoff_until: dict[str, float] = {}
        self._pending_lock_paths: set[Path] = set()
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._global_stop_lock = threading.Lock()
        self._binding_operations: dict[str, threading.RLock] = {}
        self._closing = False
        self._closed = False
        self._active_operations = 0
        self._monitor_stop = threading.Event()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="staffdeck-feishu-supervisor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _operation_lock(self, binding_id: str) -> threading.RLock:
        """取该绑定专属的可重入操作锁，首次访问时创建并缓存。"""
        with self._lock:
            return self._binding_operations.setdefault(binding_id, threading.RLock())

    @contextmanager
    def _binding_operation(self, binding_id: str) -> Iterator[None]:
        """以活动操作租约包裹一次绑定操作，串行化同绑定操作并阻塞并发 stop()。

        监督器正在关闭或已关闭时直接抛 RuntimeError；退出时无论成功与否都归还租约。
        """
        with self._condition:
            if self._closing or self._closed:
                raise RuntimeError("Feishu connector supervisor is closing")
            self._active_operations += 1
        try:
            with self._operation_lock(binding_id):
                yield
        finally:
            with self._condition:
                self._active_operations -= 1
                self._condition.notify_all()

    def _monitor_loop(self) -> None:
        """后台监控循环：每 20ms 轮询一次进程状态，直到收到停止信号。"""
        while not self._monitor_stop.wait(0.02):
            self.poll_once()

    def start_binding(self, binding_id: str, config_revision: int) -> ConnectorRecord:
        """启动绑定的连接器进程并返回其记录；同代次进程已存活时直接复用。"""
        with self._binding_operation(binding_id):
            return self._start_binding_core(binding_id, config_revision)

    def _start_binding_core(self, binding_id: str, config_revision: int) -> ConnectorRecord:
        """核心启动逻辑：复用同代次存活进程，否则预留进程槽位后新建子进程。

        配置版本不一致、处于崩溃退避期或达到进程数上限时抛 RuntimeError。
        """
        with self._lock:
            if self._closing or self._closed:
                raise RuntimeError("Feishu connector supervisor is closing")
            current = self._records.get(binding_id)
            if current and current.process.is_alive():
                if current.spec.config_revision != config_revision:
                    raise RuntimeError(
                        "Feishu connector revision changed; stop/reconfigure is required"
                    )
                return current
            if current:
                self._dispose_record(current)
                self._records.pop(binding_id, None)
            now = time.monotonic()
            if self._backoff_until.get(binding_id, 0.0) > now:
                raise RuntimeError(f"Feishu connector is in crash backoff: {binding_id}")
            if (
                sum(record.process.is_alive() for record in self._records.values())
                + self._reserved_process_slots
                >= self._max_processes
            ):
                raise RuntimeError("Feishu connector process limit reached")
            self._reserved_process_slots += 1

        try:
            return self._spawn_reserved_binding(binding_id, config_revision)
        finally:
            with self._condition:
                self._reserved_process_slots -= 1
                self._condition.notify_all()

    def _spawn_reserved_binding(
        self, binding_id: str, config_revision: int
    ) -> ConnectorRecord:
        """等绑定文件锁空闲后 spawn 子进程并登记记录，失败时关闭 IPC 与进程句柄。"""
        lock_path = binding_lock_path(binding_id, self._database_path)
        if not self._wait_for_binding_lock(lock_path, self._lock_wait_seconds):
            raise RuntimeError(f"Feishu connector binding lock is still held: {binding_id}")
        nonce = secrets.token_hex(16)
        parent_connection, child_connection = self._ctx.Pipe(duplex=True)
        spec = ConnectorChildSpec(
            binding_id=binding_id,
            config_revision=config_revision,
            child_nonce=nonce,
            runtime_path=self._runtime_path,
            binding_lock_path=str(lock_path),
            database_path=str(self._database_path),
            watchdog_seconds=self._watchdog_seconds,
        )
        process = self._ctx.Process(
            target=connector_child_entry,
            args=(spec, child_connection),
            name=f"staffdeck-feishu-{binding_id}",
        )
        record = ConnectorRecord(spec=spec, process=process, connection=parent_connection)
        try:
            process.start()
        except Exception:
            parent_connection.close()
            child_connection.close()
            try:
                process.close()
            except ValueError:
                pass
            raise
        child_connection.close()
        with self._lock:
            if self._closing or self._closed:
                record.state = ConnectorState.STOPPING
                self._records[binding_id] = record
                stopped = self._stop_records([record], time.monotonic() + 1.0)
                if stopped and self._records.get(binding_id) is record:
                    self._records.pop(binding_id, None)
                    self._dispose_record(record)
                raise RuntimeError("Feishu connector supervisor is closing")
            self._records[binding_id] = record
        return record

    @staticmethod
    def _valid_event(record: ConnectorRecord, event: dict[str, Any]) -> bool:
        """校验事件四元组（绑定、配置版本、子进程 nonce、pid），过滤旧代次残留。"""
        return (
            event.get("binding_id") == record.spec.binding_id
            and event.get("config_revision") == record.spec.config_revision
            and event.get("child_nonce") == record.spec.child_nonce
            and event.get("pid") == record.process.pid
        )

    def _accept_event(self, record: ConnectorRecord, event: dict[str, Any]) -> None:
        """接纳合法事件并更新记录：连接态、分发截止时间与退出标记。

        非法事件直接丢弃；INBOX_STAGED 会唤醒入站暂存线程，末尾统一唤醒等待者。
        """
        if not self._valid_event(record, event):
            return
        record.events.append(event)
        event_name = event.get("event")
        if event_name == "CONNECTED":
            record.connected = True
            record.state = ConnectorState.RUNNING
        elif event_name == "DISCONNECTED":
            record.connected = False
        elif event_name == "DISPATCH_STARTED":
            token = str(event.get("frame_token") or "")
            deadline = event.get("deadline_monotonic")
            if token and isinstance(deadline, (int, float)):
                record.dispatch_deadlines[token] = (
                    float(deadline) + self._parent_watchdog_grace_seconds
                )
        elif event_name == "DISPATCH_FINISHED":
            record.dispatch_deadlines.pop(str(event.get("frame_token") or ""), None)
        elif event_name == "STOPPED":
            record.connected = False
        elif event_name == "INBOX_STAGED":
            from app.channels.service_intake import wake_staged_inbound_worker

            wake_staged_inbound_worker()
        self._condition.notify_all()

    def poll_once(self) -> None:
        """单次轮询：收事件、判定启动超时与看门狗，并把已退出进程归位到 ABSENT 或退避。"""
        with self._condition:
            now = time.monotonic()
            for binding_id, record in list(self._records.items()):
                self._drain_events(record)
                if (
                    record.process.is_alive()
                    and record.state == ConnectorState.STARTING
                    and now - record.started_at >= self._startup_timeout_seconds
                    and record.termination_phase is None
                ):
                    record.process.terminate()
                    record.termination_phase = "terminate"
                    record.termination_sent_at = now
                if record.process.is_alive():
                    self._advance_watchdog_termination(record, now)
                if record.process.is_alive():
                    continue
                record.process.join(timeout=0)
                record.connected = False
                record.exit_code = record.process.exitcode
                if not record.exit_observed and record.state != ConnectorState.STOPPING:
                    self._record_crash(binding_id, now)
                record.exit_observed = True
                if self._backoff_until.get(binding_id, 0.0) > now:
                    record.state = ConnectorState.CRASH_BACKOFF
                else:
                    record.state = ConnectorState.ABSENT
            self._condition.notify_all()

    def _drain_events(self, record: ConnectorRecord) -> None:
        """非阻塞读空 IPC 管道里的事件；对端已关闭或管道异常时静默结束。"""
        try:
            while record.connection.poll():
                try:
                    event = record.connection.recv()
                except (EOFError, OSError):
                    break
                if isinstance(event, dict):
                    self._accept_event(record, event)
        except (OSError, ValueError):
            pass

    def _advance_watchdog_termination(self, record: ConnectorRecord, now: float) -> None:
        """推进看门狗终止：分发超期先 terminate，宽限期满仍存活再 kill。"""
        expired = any(deadline <= now for deadline in record.dispatch_deadlines.values())
        if not expired and record.termination_phase is None:
            return
        if record.termination_phase is None:
            record.process.terminate()
            record.termination_phase = "terminate"
            record.termination_sent_at = now
            return
        if (
            record.termination_phase == "terminate"
            and record.termination_sent_at is not None
            and now - record.termination_sent_at >= self._terminate_grace_seconds
            and record.process.is_alive()
        ):
            if hasattr(record.process, "kill"):
                record.process.kill()
            else:
                record.process.terminate()
            record.termination_phase = "kill"
            record.termination_sent_at = now

    def _record_crash(self, binding_id: str, now: float) -> None:
        """记录一次非预期退出；崩溃窗口内达到上限后按指数退避推迟下次启动。"""
        crashes = self._crashes.setdefault(binding_id, deque())
        crashes.append(now)
        while crashes and crashes[0] < now - self._crash_window_seconds:
            crashes.popleft()
        if len(crashes) >= self._crash_limit:
            exponent = len(crashes) - self._crash_limit
            delay = min(self._backoff_seconds * (2**exponent), self._max_backoff_seconds)
            self._backoff_until[binding_id] = now + delay

    def reset_backoff(self, binding_id: str) -> None:
        """清除绑定的崩溃计数与退避截止时间，允许立即重启。"""
        with self._lock:
            self._crashes.pop(binding_id, None)
            self._backoff_until.pop(binding_id, None)

    def wait_for_event(self, binding_id: str, event_name: str, timeout: float = 5.0) -> dict:
        """阻塞等待该绑定出现指定事件，超时或进程退出时抛 TimeoutError。

        事件缓冲保留最近 256 条，因此早于本次调用发生的事件同样能命中。
        """
        deadline = time.monotonic() + timeout
        with self._condition:
            while True:
                record = self._records[binding_id]
                for event in record.events:
                    if event.get("event") == event_name:
                        return event
                if not record.process.is_alive() or time.monotonic() >= deadline:
                    break
                self._condition.wait(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
        raise TimeoutError(f"Feishu connector event timed out: {binding_id}/{event_name}")

    @staticmethod
    def _stop_message(record: ConnectorRecord) -> dict[str, Any]:
        """构造要求子进程优雅退出的 STOP 消息，带代次与 nonce 供子进程自校验。"""
        return {
            "command": "STOP",
            "binding_id": record.spec.binding_id,
            "config_revision": record.spec.config_revision,
            "child_nonce": record.spec.child_nonce,
        }

    def _send_stop(self, record: ConnectorRecord) -> None:
        """尽力发送 STOP 消息；管道已断时忽略，后续由 terminate 兜底。"""
        try:
            record.connection.send(self._stop_message(record))
        except (BrokenPipeError, EOFError, OSError):
            pass

    @staticmethod
    def _wait_processes(records: list[ConnectorRecord], deadline: float) -> None:
        """轮询回收进程（is_alive 探测加零超时 join），直到全部退出或到达截止时间。"""
        while time.monotonic() < deadline:
            alive = [record for record in records if record.process.is_alive()]
            if not alive:
                return
            for record in alive:
                record.process.join(timeout=0)
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def _stop_records(self, records: list[ConnectorRecord], deadline: float) -> bool:
        """按 STOP、terminate、kill 三级推进停止，在截止时间内尽量回收全部进程。

        时限按 50%/80% 切成两段宽限，返回是否所有进程都已退出。
        """
        if not records:
            return True
        now = time.monotonic()
        total = max(0.0, deadline - now)
        grace_end = now + total * 0.5
        terminate_end = now + total * 0.8
        for record in records:
            self._send_stop(record)
        self._wait_processes(records, grace_end)
        for record in records:
            if record.process.is_alive():
                record.process.terminate()
        self._wait_processes(records, terminate_end)
        for record in records:
            if not record.process.is_alive():
                continue
            if hasattr(record.process, "kill"):
                record.process.kill()
            else:
                record.process.terminate()
        self._wait_processes(records, deadline)
        return all(not record.process.is_alive() for record in records)

    def stop_binding(self, binding_id: str, timeout: float = 5.0) -> bool:
        """停止单个绑定的连接器进程，返回是否已彻底退出且释放绑定文件锁。"""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._binding_operation(binding_id):
            with self._condition:
                record = self._records.get(binding_id)
                if record is None:
                    return True
                record.state = ConnectorState.STOPPING
                self._condition.notify_all()
            stopped = self._stop_records([record], deadline)
            with self._condition:
                self._drain_events(record)
                if stopped:
                    record.process.join(timeout=0)
                    record.exit_code = record.process.exitcode
                if self._records.get(binding_id) is record and stopped:
                    self._records.pop(binding_id, None)
                    self._dispose_record(record)
                self._condition.notify_all()
            return stopped and self._binding_lock_is_free(Path(record.spec.binding_lock_path))

    def replace_binding(
        self,
        binding_id: str,
        *,
        expected_revision: int,
        new_revision: int,
        commit_revision: Callable[[], bool],
        timeout: float = 5.0,
    ) -> ConnectorRecord:
        """换配流程：停掉旧代次、提交 DB CAS、再启动新代次并返回新记录。

        旧代次未在时限内退出、绑定锁仍被占用或 CAS 失败都会抛错，此时不会启动新进程。
        """
        deadline = time.monotonic() + max(0.0, timeout)
        with self._binding_operation(binding_id):
            with self._condition:
                current = self._records.get(binding_id)
                if not current or current.spec.config_revision != expected_revision:
                    raise RuntimeError("Feishu connector revision does not match expected generation")
                current.state = ConnectorState.STOPPING
            if not self._stop_records([current], deadline):
                raise TimeoutError("old Feishu connector did not stop before reconfiguration")
            if not self._binding_lock_is_free(Path(current.spec.binding_lock_path)):
                raise TimeoutError("old Feishu connector binding lock is still held")
            with self._condition:
                self._drain_events(current)
                current.process.join(timeout=0)
                current.exit_code = current.process.exitcode
                if self._records.get(binding_id) is current:
                    self._records.pop(binding_id, None)
                    self._dispose_record(current)
            if not commit_revision():
                raise RuntimeError("Feishu binding revision CAS failed")
            if time.monotonic() >= deadline:
                raise TimeoutError("Feishu connector reconfiguration deadline exceeded")
            return self._start_binding_without_operation(binding_id, new_revision)

    def _start_binding_without_operation(
        self, binding_id: str, config_revision: int
    ) -> ConnectorRecord:
        """在调用方已持有该绑定操作租约时启动新代次，只做关闭检查后转交核心启动。"""
        # replace_binding already owns this binding's RLock and active-operation lease.
        with self._condition:
            if self._closing or self._closed:
                raise RuntimeError("Feishu connector supervisor is closing")
        return self._start_binding_core(binding_id, config_revision)

    def stop(self, timeout: float = 5.0) -> bool:
        """停止全部连接器并回收监控线程，返回是否在时限内完成关闭。

        先在全局锁内拒绝新操作并等待活动操作归零，再停进程、停监控线程；
        若仍有进程或绑定锁未释放，则记录待清理锁路径并返回 False，可再次调用。
        """
        with self._global_stop_lock:
            deadline = time.monotonic() + max(0.0, timeout)
            with self._condition:
                if self._closed:
                    return True
                self._closing = True
                while self._active_operations and time.monotonic() < deadline:
                    self._condition.wait(
                        timeout=min(0.02, max(0.0, deadline - time.monotonic()))
                    )
                if self._active_operations:
                    return False
                records = list(self._records.values())
                for record in records:
                    record.state = ConnectorState.STOPPING
                self._condition.notify_all()

            stopped = self._stop_records(records, deadline)
            self._monitor_stop.set()
            self._monitor_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            monitor_stopped = not self._monitor_thread.is_alive()

            with self._condition:
                for record in records:
                    self._drain_events(record)
                    if not record.process.is_alive():
                        record.process.join(timeout=0)
                        record.exit_code = record.process.exitcode
                if stopped:
                    for record in records:
                        self._dispose_record(record)
                    self._records.clear()
                self._condition.notify_all()
            lock_paths = self._pending_lock_paths | {
                Path(record.spec.binding_lock_path) for record in records
            }
            blocked_paths = {
                path for path in lock_paths if not self._binding_lock_is_free(path)
            }
            locks_free = not blocked_paths
            with self._condition:
                self._pending_lock_paths = blocked_paths
                self._closed = stopped and monitor_stopped and locks_free
                self._condition.notify_all()
            return stopped and monitor_stopped and locks_free

    def forget_binding(self, binding_id: str) -> None:
        """丢弃已退出绑定的记录、崩溃计数与退避状态；进程仍存活时抛 RuntimeError。"""
        with self._binding_operation(binding_id):
            with self._condition:
                record = self._records.get(binding_id)
                if record and record.process.is_alive():
                    raise RuntimeError("cannot forget a running Feishu connector")
                if record:
                    self._records.pop(binding_id, None)
                    self._dispose_record(record)
                # Keep the operation lock for the supervisor lifetime so no waiter can be
                # split onto a second lock object for the same binding.
                self._crashes.pop(binding_id, None)
                self._backoff_until.pop(binding_id, None)

    @staticmethod
    def _dispose_record(record: ConnectorRecord) -> None:
        """关闭记录的 IPC 连接；进程已退出时一并关闭进程句柄。"""
        try:
            record.connection.close()
        except OSError:
            pass
        if not record.process.is_alive():
            try:
                record.process.close()
            except ValueError:
                pass

    @staticmethod
    def _binding_lock_is_free(path: Path) -> bool:
        """试探性获取绑定文件锁后立即释放，判断该锁当前是否空闲。"""
        lock = BindingProcessLock(path)
        try:
            acquired = lock.acquire()
        except OSError:
            return False
        if not acquired:
            return False
        lock.release()
        return True

    def _wait_for_binding_lock(self, path: Path, timeout: float) -> bool:
        """在时限内轮询等待绑定文件锁空闲，超时返回 False。"""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            if self._binding_lock_is_free(path):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.01)

    def state(self, binding_id: str) -> ConnectorState:
        """返回绑定状态：有记录取记录状态，退避期内为 CRASH_BACKOFF，否则 ABSENT。"""
        with self._lock:
            record = self._records.get(binding_id)
            if record:
                return record.state
            if self._backoff_until.get(binding_id, 0.0) > time.monotonic():
                return ConnectorState.CRASH_BACKOFF
            return ConnectorState.ABSENT

    def connected(self, binding_id: str) -> bool:
        """判断绑定进程是否存活且已上报 CONNECTED。"""
        with self._lock:
            record = self._records.get(binding_id)
            return bool(record and record.process.is_alive() and record.connected)

    def inject_event_for_test(self, binding_id: str, event: dict[str, Any]) -> None:
        """测试用：向指定绑定注入一条事件，走与真实事件相同的四元组校验。"""
        with self._condition:
            self._accept_event(self._records[binding_id], event)

    def monitor_alive(self) -> bool:
        """返回后台监控线程是否仍在运行。"""
        return self._monitor_thread.is_alive()

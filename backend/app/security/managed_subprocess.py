from __future__ import annotations

import os
import signal
import subprocess
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Sequence


class ManagedProcessError(RuntimeError):
    """受管子进程启动或进程隔离初始化失败时抛出的运行期错误。"""


class _WindowsJobAssignmentError(OSError):
    """Job Object 分配失败，并附带父/子进程原有的 Job 归属状态。"""

    def __init__(
        self,
        winerror: int,
        message: str,
        *,
        parent_in_job: bool | None,
        child_in_job: bool | None,
    ) -> None:
        """保存 winerror、提示信息与父子进程的 Job 归属探测结果。"""
        super().__init__(winerror, message)
        self.winerror = winerror
        self.parent_in_job = parent_in_job
        self.child_in_job = child_in_job


def _is_process_in_windows_job(kernel32: Any, process_handle: Any) -> bool | None:
    """返回进程是否属于某个 Job Object；Windows 无法查询该句柄时返回 None。"""

    import ctypes
    from ctypes import wintypes

    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(process_handle, None, ctypes.byref(in_job)):
        return None
    return bool(in_job.value)


def _resume_windows_process(proc: subprocess.Popen[Any]) -> None:
    """用 NtResumeProcess 恢复以 CREATE_SUSPENDED 创建的子进程，失败时抛 OSError。"""
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    status = ntdll.NtResumeProcess(wintypes.HANDLE(proc._handle))
    if status < 0:
        raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")


class _WindowsJob:
    """关闭时一并终止全部后代的 Windows Job Object 封装。"""

    def __init__(self, handle: Any, kernel32: Any) -> None:
        """保存 Job 句柄与已绑定函数原型的 kernel32。"""
        self._handle = handle
        self._kernel32 = kernel32

    @classmethod
    def assign(cls, proc: subprocess.Popen[Any]) -> _WindowsJob:
        """创建 KILL_ON_JOB_CLOSE 的 Job 并挂入子进程，失败抛带归属状态的错误。"""
        import ctypes
        from ctypes import wintypes

        class BasicLimitInformation(ctypes.Structure):
            """Job Object 的基础限额信息结构体。"""

            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            """Job Object 的 IO 计数器结构体。"""

            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            """扩展限额信息结构体，用于设置 KILL_ON_JOB_CLOSE 标志。"""

            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        kernel32.IsProcessInJob.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = 0x00002000  # KILL_ON_JOB_CLOSE
        configured = kernel32.SetInformationJobObject(
            handle,
            9,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not configured:
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        parent_in_job = _is_process_in_windows_job(
            kernel32,
            kernel32.GetCurrentProcess(),
        )
        child_handle = wintypes.HANDLE(proc._handle)
        child_in_job = _is_process_in_windows_job(kernel32, child_handle)
        if not kernel32.AssignProcessToJobObject(handle, child_handle):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            native_error = ctypes.WinError(error)
            raise _WindowsJobAssignmentError(
                error,
                str(native_error),
                parent_in_job=parent_in_job,
                child_in_job=child_in_job,
            ) from native_error
        return cls(handle, kernel32)

    def resume(self, proc: subprocess.Popen[Any]) -> None:
        """恢复已挂入 Job 的挂起子进程，使其开始执行。"""
        _resume_windows_process(proc)

    def close(self) -> None:
        """终止 Job 内全部进程并关闭句柄；重复调用安全。"""
        if not self._handle:
            return
        self._kernel32.TerminateJobObject(self._handle, 1)
        self._kernel32.CloseHandle(self._handle)
        self._handle = None


def _popen_platform_options(platform_name: str) -> dict[str, Any]:
    """返回按平台隔离子进程所需的 Popen 选项：Windows 挂起创建，POSIX 独立会话。"""
    if platform_name == "nt":
        return {
            "creationflags": (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
            )
        }
    return {"start_new_session": True}


@dataclass
class ManagedProcess:
    """受管子进程：启动时建立平台进程隔离，close 时确保整棵进程树被回收。"""

    process: subprocess.Popen[Any]
    platform_name: str
    isolation_mode: str = "posix_session"
    isolation_details: dict[str, Any] = field(default_factory=dict)
    _windows_job: _WindowsJob | None = None
    _closed: bool = False

    @classmethod
    def start(
        cls,
        argv: Sequence[str],
        *,
        platform_name: str | None = None,
        **popen_kwargs: Any,
    ) -> ManagedProcess:
        """按平台启动子进程并建立隔离，返回受管句柄。

        Windows 用 Job Object 隔离（先挂起创建）；嵌套 Job 被拒时降级为进程组，
        其余失败会清理进程树并抛 ManagedProcessError。
        """
        platform = platform_name or os.name
        proc = subprocess.Popen(
            list(argv),
            **popen_kwargs,
            **_popen_platform_options(platform),
        )
        job: _WindowsJob | None = None
        isolation_mode = "posix_session"
        isolation_details: dict[str, Any] = {}
        if platform == "nt":
            try:
                job = _WindowsJob.assign(proc)
                job.resume(proc)
                isolation_mode = "windows_job"
            except _WindowsJobAssignmentError as exc:
                nested_job_conflict = (
                    exc.winerror in {5, 50}
                    and (exc.parent_in_job is True or exc.child_in_job is True)
                )
                if not nested_job_conflict:
                    _terminate_process_tree(proc, platform)
                    raise ManagedProcessError(
                        f"Windows 进程隔离初始化失败，子进程未启动：{exc}"
                    ) from exc
                try:
                    _resume_windows_process(proc)
                except OSError as resume_exc:
                    _terminate_process_tree(proc, platform)
                    raise ManagedProcessError(
                        f"Windows 进程隔离降级后恢复子进程失败：{resume_exc}"
                    ) from resume_exc
                isolation_mode = "windows_process_group_fallback"
                isolation_details = {
                    "fallback_reason": "nested_job_assignment_rejected",
                    "windows_error_code": exc.winerror,
                    "parent_in_job": exc.parent_in_job,
                    "child_in_job": exc.child_in_job,
                    "cleanup": "taskkill_tree",
                }
            except OSError as exc:
                if job is not None:
                    job.close()
                _terminate_process_tree(proc, platform)
                raise ManagedProcessError(
                    f"Windows 进程隔离初始化失败，子进程未启动：{exc}"
                ) from exc
        return cls(
            process=proc,
            platform_name=platform,
            isolation_mode=isolation_mode,
            isolation_details=isolation_details,
            _windows_job=job,
        )

    def close(self) -> None:
        """幂等关闭：先结束 Job Object，再回收子进程树及其标准流。"""
        if self._closed:
            return
        self._closed = True
        if self._windows_job is not None:
            self._windows_job.close()
            self._windows_job = None
        _terminate_process_tree(self.process, self.platform_name)


def _terminate_process_tree(proc: subprocess.Popen[Any], platform_name: str) -> None:
    """尽力回收整个进程树：Windows 用 taskkill /T，POSIX 用进程组信号，最后兜底 terminate/kill。"""
    if platform_name == "nt" and proc.poll() is None:
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
    elif platform_name != "nt":
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGTERM)

    if proc.poll() is None:
        with suppress(OSError):
            proc.terminate()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        if platform_name != "nt":
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        with suppress(OSError):
            proc.kill()
        with suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=1)

    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            with suppress(OSError, ValueError):
                stream.close()

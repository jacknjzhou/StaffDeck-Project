"""``config init --new`` 的后台进程管理（对话内现场创建飞书应用）。

官方 CLI 为 agent 设计的流程：进程阻塞直到用户在浏览器完成创建，需要
后台运行并从输出中提取 verification URL 接力给用户。本模块按用户 HOME
维护一个进程注册表：启动、取 URL、查询进度、超时与孤儿回收。

完成判定不依赖进程输出，而以「HOME 下 config.json 出现应用」为准——
即便后端重启丢失注册表，用户完成创建后重新查询仍能得到 configured。
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.lark_cli.runner import configured_app_ids, process_environment

_URL_PATTERN = re.compile(r"https://[^\s\"'<>]+")
_WAIT_FOR_URL_SECONDS = 20.0
_PROCESS_TTL_SECONDS = 900.0
_MAX_OUTPUT_CHARS = 20_000
_PIDFILE_NAME = "config_init_new.pid"


@dataclass
class _InitProcess:
    """一个 ``config init --new`` 后台进程的注册项：句柄、输出缓冲与提取到的 URL。"""

    process: subprocess.Popen[str]
    started_at: float
    output: list[str] = field(default_factory=list)
    output_chars: int = 0
    verification_url: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader: threading.Thread | None = None


_REGISTRY: dict[str, _InitProcess] = {}
_REGISTRY_LOCK = threading.RLock()


def config_init_new_status(binary: Path, home: Path) -> dict[str, object]:
    """启动或查询本用户的 ``config init --new`` 后台流程。

    返回 dict：``status`` 为 ``configured`` / ``pending_user`` / ``failed``，
    并带模型可直接执行的 ``next_step`` 指引。同一 HOME 重复调用是幂等的
    查询，不会重复起进程。
    """

    key = str(home)
    with _REGISTRY_LOCK:
        # 结果判定优先于进程状态：配置出现即成功。
        apps = configured_app_ids(home)
        if apps:
            entry = _REGISTRY.pop(key, None)
            if entry is not None:
                _terminate(entry)
            _remove_pidfile(home)
            return {
                "status": "configured",
                "app_ids": sorted(apps),
                "next_step": (
                    "应用已创建并配置完成。继续调用 auth status 检查用户登录态，"
                    "未登录则走设备码登录流程。"
                ),
            }
        entry = _REGISTRY.get(key)
        if entry is not None:
            exit_code = entry.process.poll()
            if exit_code is not None:
                _REGISTRY.pop(key, None)
                _remove_pidfile(home)
                if entry.reader is not None:
                    # 让读线程吃完管道残余，避免 tail 截断。
                    entry.reader.join(timeout=2)
                return _failed(
                    f"应用创建进程已退出（exit {exit_code}）但未产生配置："
                    f"{_output_tail(entry)}"
                )
            if time.monotonic() - entry.started_at > _PROCESS_TTL_SECONDS:
                _REGISTRY.pop(key, None)
                _terminate(entry)
                _remove_pidfile(home)
                return _failed(
                    f"等待用户完成创建超时（{_PROCESS_TTL_SECONDS:.0f}s），"
                    "进程已回收；可重新发起。"
                )
        else:
            _kill_stale_pidfile(home)
            entry = _spawn(binary, home)
            _REGISTRY[key] = entry
    url = _wait_for_url(entry)
    # 等待 URL 期间进程可能已结束（极快失败/极快完成），重查一次终态。
    if entry.process.poll() is not None:
        return config_init_new_status(binary, home)
    return {
        "status": "pending_user",
        "verification_url": url,
        "next_step": (
            "把 verification_url 发给用户，请其在浏览器中登录飞书开放平台并完成"
            "应用创建；用户回复完成后，再次调用 config init --new 查询进度。"
            if url
            else "创建进程已启动但尚未输出 verification URL，请稍后再次调用"
            " config init --new 查询。"
        ),
    }


def _spawn(binary: Path, home: Path) -> _InitProcess:
    """起一个 ``config init --new`` 子进程，挂上读线程并写 pidfile 后返回注册项。"""
    process = subprocess.Popen(
        [str(binary), "config", "init", "--new"],
        cwd=str(home),
        env=process_environment(home),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=(os.name == "posix"),
    )
    _write_pidfile(home, process.pid)
    entry = _InitProcess(process=process, started_at=time.monotonic())
    entry.reader = threading.Thread(target=_read_output, args=(entry,), daemon=True)
    entry.reader.start()
    return entry


def _read_output(entry: _InitProcess) -> None:
    """读线程主体：按行缓存输出（有字符上限）并从首个命中处提取 verification URL。"""
    stream = entry.process.stdout
    if stream is None:
        return
    for line in stream:
        with entry.lock:
            if entry.output_chars < _MAX_OUTPUT_CHARS:
                entry.output.append(line)
                entry.output_chars += len(line)
            if entry.verification_url is None:
                match = _URL_PATTERN.search(line)
                if match:
                    entry.verification_url = match.group(0).rstrip(".,;)")


def _wait_for_url(entry: _InitProcess) -> str | None:
    """轮询等待输出里出现 verification URL，最多 _WAIT_FOR_URL_SECONDS 秒。

    进程提前退出时立即结束等待，返回已拿到的 URL（可能仍为 None）。
    """
    deadline = time.monotonic() + _WAIT_FOR_URL_SECONDS
    while time.monotonic() < deadline:
        with entry.lock:
            if entry.verification_url:
                return entry.verification_url
        if entry.process.poll() is not None:
            break
        time.sleep(0.2)
    with entry.lock:
        return entry.verification_url


def _output_tail(entry: _InitProcess) -> str:
    """取输出末尾 800 字符放进错误详情；完全没有输出时返回占位文案。"""
    with entry.lock:
        return "".join(entry.output)[-800:].strip() or "（无输出）"


def _failed(detail: str) -> dict[str, object]:
    """构造统一的失败结果 dict，detail 会被原样转述给用户。"""
    return {"status": "failed", "detail": detail}


def _terminate(entry: _InitProcess) -> None:
    """杀掉注册项对应的进程并回收；进程已退出时直接返回。"""
    if entry.process.poll() is not None:
        return
    _kill_pid(entry.process.pid)
    with contextlib.suppress(Exception):
        entry.process.wait(timeout=5)


def _pidfile(home: Path) -> Path:
    """返回该 HOME 下的 pidfile 路径，必要时先创建 tmp 目录。"""
    tmp = home / "tmp"
    tmp.mkdir(exist_ok=True)
    return tmp / _PIDFILE_NAME


def _write_pidfile(home: Path, pid: int) -> None:
    """把进程 pid 写入 pidfile，供后端重启后回收孤儿进程；写失败静默忽略。"""
    with contextlib.suppress(OSError):
        _pidfile(home).write_text(str(pid), encoding="utf-8")


def _remove_pidfile(home: Path) -> None:
    """删除 pidfile；文件不存在或删除失败都静默忽略。"""
    with contextlib.suppress(OSError):
        _pidfile(home).unlink(missing_ok=True)


def _kill_stale_pidfile(home: Path) -> None:
    """后端重启会丢注册表；孤儿的阻塞进程按 pidfile 回收后再新起。"""

    try:
        pid = int(_pidfile(home).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    _kill_pid(pid)
    _remove_pidfile(home)


def _kill_pid(pid: int) -> None:
    """强杀指定 pid：POSIX 优先杀整个进程组，不可用时退回单进程 kill。"""
    if pid <= 0:
        return
    if os.name == "posix":
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)

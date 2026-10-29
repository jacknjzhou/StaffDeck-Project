from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_script(name: str):
    """按文件路径加载 scripts 目录下的脚本模块，绕过常规包导入。"""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_supervisor_uses_platform_specific_executables() -> None:
    """验证 supervisor 按平台选择后端 Python 与前端 vite 的可执行文件路径。"""
    supervisor = _load_script("dev_supervisor")

    assert supervisor._backend_python("win32") == ROOT_DIR / "backend/.venv/Scripts/python.exe"
    assert supervisor._backend_python("linux") == ROOT_DIR / "backend/.venv/bin/python"
    assert supervisor._vite_executable("win32") == ROOT_DIR / "frontend-enterprise/node_modules/.bin/vite.cmd"
    assert supervisor._vite_executable("darwin") == ROOT_DIR / "frontend-enterprise/node_modules/.bin/vite"


def test_pid_alive_recognizes_current_process() -> None:
    """验证 pid_alive 能判定当前进程仍然存活。"""
    process_utils = _load_script("process_utils")

    assert process_utils.pid_alive(os.getpid())


def test_dev_cli_uses_next_port_in_packaged_app_range(monkeypatch) -> None:
    """验证起始端口被占用时顺延到打包应用端口区间内的下一个可用端口。"""
    dev = _load_script("dev")
    monkeypatch.delenv("ULTRARAG_PORT_RANGE_START", raising=False)
    monkeypatch.delenv("ULTRARAG_PORT_RANGE_END", raising=False)
    monkeypatch.setattr(dev, "_port_available", lambda _host, port: port != 5173)

    assert dev._select_available_port("127.0.0.1", 5173) == 5174


def test_url_ready_does_not_require_reading_response_body(monkeypatch) -> None:
    """验证 URL 就绪探测只看状态码，不读取响应体也能判定可用。"""
    dev = _load_script("dev")

    class Response:
        """模拟状态码正常、但读取响应体即断开连接的响应对象。"""

        status = 200

        def __enter__(self):
            """返回自身，使响应对象可用于 with 语句。"""
            return self

        def __exit__(self, *_args):
            """退出上下文时不吞掉异常。"""
            return False

        def read(self):
            """模拟连接已关闭导致读取响应体失败。"""
            raise ConnectionResetError("body connection closed")

    monkeypatch.setattr(dev.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert dev._url_ready("http://127.0.0.1:5173/api/health") is True


def test_url_ready_ignores_response_close_failure(monkeypatch) -> None:
    """验证关闭响应对象抛错时仍判定 URL 可用。"""
    dev = _load_script("dev")

    class Response:
        """模拟仅提供 close 且关闭即报错的响应对象。"""

        status = 200

        def close(self):
            """模拟连接已断开导致的关闭失败。"""
            raise ConnectionResetError("connection closed")

    monkeypatch.setattr(dev.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert dev._url_ready("http://127.0.0.1:5173/api/health") is True


def test_dev_cli_honors_packaged_app_port_range(monkeypatch) -> None:
    """验证 dev CLI 只在环境变量给定的端口区间内挑选可用端口。"""
    dev = _load_script("dev")
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_START", "6200")
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_END", "6202")
    monkeypatch.setattr(dev, "_port_available", lambda _host, port: port == 6202)

    assert dev._select_available_port("127.0.0.1", 6200) == 6202


def test_dev_cli_keeps_complete_frontend_dependencies(monkeypatch) -> None:
    """验证前端依赖已完整时只做检查、不触发重新安装。"""
    dev = _load_script("dev")
    calls: list[list[str]] = []
    monkeypatch.setattr(dev, "_npm_executable", lambda: "npm")

    def run(command, **_kwargs):
        """记录 npm 命令并返回成功退出码，模拟依赖完整。"""
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(dev.subprocess, "run", run)

    dev._ensure_frontend_dependencies()

    assert len(calls) == 1
    assert calls[0][-3:] == ["ls", "--depth=0", "--json"]


def test_dev_cli_refreshes_incomplete_frontend_dependencies(monkeypatch) -> None:
    """验证前端依赖不完整时执行 npm ci 重新安装。"""
    dev = _load_script("dev")
    calls: list[list[str]] = []
    monkeypatch.setattr(dev, "_npm_executable", lambda: "npm")

    def run(command, **_kwargs):
        """记录命令并对含 ls 的检查返回失败，模拟依赖缺失。"""
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if "ls" in command else 0)

    monkeypatch.setattr(dev.subprocess, "run", run)

    dev._ensure_frontend_dependencies()

    assert len(calls) == 2
    assert calls[1][-3:] == ["ci", "--no-audit", "--no-fund"]


def test_dev_cli_detects_missing_backend_dependency(tmp_path, monkeypatch) -> None:
    """验证后端依赖清单中存在未安装的包时判定为不完整。"""
    dev = _load_script("dev")
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text(
        '[project]\ndependencies = ["present>=1", "missing>=1"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(dev, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        dev,
        "_installed_distribution_version",
        lambda name: "1.2" if name == "present" else None,
    )

    assert dev._backend_dependencies_complete() is False


def test_dev_cli_refreshes_incomplete_backend_dependencies(monkeypatch) -> None:
    """验证后端依赖不完整时以可编辑模式重新安装 backend。"""
    dev = _load_script("dev")
    calls: list[list[str]] = []
    monkeypatch.setattr(dev, "_backend_dependencies_complete", lambda: False)

    def run(command, **_kwargs):
        """记录安装命令并返回成功，避免真正执行 pip。"""
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(dev.subprocess, "run", run)

    dev._ensure_backend_dependencies()

    assert calls == [
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-e",
            str(ROOT_DIR / "backend"),
        ]
    ]


def test_supervisor_does_not_restart_during_startup_grace(monkeypatch) -> None:
    """验证启动宽限期内健康检查失败不会累计重启次数。"""
    supervisor = _load_script("dev_supervisor")

    class RunningProcess:
        """模拟始终处于运行状态的子进程。"""

        def poll(self):
            """返回 None 表示进程仍在运行。"""
            return

    service = supervisor.Service(name="app", cwd=ROOT_DIR, command=["unused"])
    service.health_url = "http://127.0.0.1:5173/api/health"
    service.process = RunningProcess()
    service.startup_deadline = 100.0
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 50.0)
    monkeypatch.setattr(service, "healthy", lambda: False)

    service.poll()

    assert service.unhealthy_count == 0
    assert service.restart_count == 0


def test_shell_wrappers_delegate_to_cross_platform_cli() -> None:
    """验证各 shell 包装脚本都调用后端虚拟环境并委托给跨平台 dev CLI。"""
    for command in ("up", "down", "status"):
        script = (SCRIPTS_DIR / f"dev_{command}.sh").read_text(encoding="utf-8")
        assert '$ROOT_DIR/backend/.venv/bin/python' in script
        assert 'scripts/dev.py" ' + command in script


def test_powershell_wrappers_delegate_to_cross_platform_cli() -> None:
    """验证各 PowerShell 包装脚本都委托给跨平台 dev CLI。"""
    for command in ("up", "down", "status"):
        script = (SCRIPTS_DIR / f"dev_{command}.ps1").read_text(encoding="utf-8")
        assert f'"$PSScriptRoot\\dev.ps1" {command}' in script


def test_powershell_launcher_accepts_newer_python_3_versions() -> None:
    """验证 PowerShell 启动器同时接受 Python 3.11 与更新的 3.x 版本。"""
    script = (SCRIPTS_DIR / "dev.ps1").read_text(encoding="utf-8")

    assert 'Prefix = @("-3.11")' in script
    assert 'Prefix = @("-3")' in script

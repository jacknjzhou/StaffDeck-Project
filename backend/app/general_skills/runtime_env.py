from __future__ import annotations

import os
import subprocess
import sys
import threading
import venv
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO, Iterator

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from app.config import get_settings

IMPORT_NAMES = {
    "beautifulsoup4": "bs4",
    "python-docx": "docx",
    "python-dateutil": "dateutil",
}
_RUNTIME_PREPARE_LOCK = threading.RLock()


class GeneralSkillRuntimeError(RuntimeError):
    """通用技能运行环境准备失败（创建 venv、安装依赖或依赖声明非法）时抛出。"""


def ensure_runtime_python() -> Path:
    """返回可用的通用技能解释器路径，必要时创建 venv 并按配置补齐依赖。

    进程内用 RLock、跨进程用文件锁串行化准备过程，避免并发首次启动重复建环境。
    """
    with _RUNTIME_PREPARE_LOCK:
        settings = get_settings()
        python_path = _resolve_runtime_python(
            settings.general_skill_runtime_python,
            settings.general_skill_runtime_venv,
        )
        with _runtime_prepare_file_lock(python_path):
            if not python_path.exists():
                _create_runtime_venv(python_path)
            if settings.general_skill_runtime_auto_install and settings.general_skill_network_install:
                _ensure_packages(python_path, settings.general_skill_runtime_package_list)
        return python_path


def runtime_environment(
    base_env: dict[str, str] | None = None,
    *,
    python_path: Path | None = None,
) -> dict[str, str]:
    """在基础环境上注入通用技能运行环境：venv 的 bin 目录进 PATH，并导出解释器变量。"""
    python_path = python_path or ensure_runtime_python()
    env = dict(base_env or os.environ)
    bin_dir = python_path.parent
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["VIRTUAL_ENV"] = str(bin_dir.parent)
    env["GENERAL_SKILL_RUNTIME_PYTHON"] = str(python_path)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _backend_dir() -> Path:
    """返回 backend 目录，作为 pip 安装的工作目录与默认 venv 的定位基准。"""
    return Path(__file__).resolve().parents[2]


def _bundled_python() -> Path:
    """定位安装包内附带的 python-build-standalone 解释器，按平台区分目录布局。"""
    # 打包态：附带的 python-build-standalone 位置随平台不同。
    # - macOS .app：runtime 在 Contents/Resources/runtime（放 Resources 是为了 codesign 密封通过），
    #   sys.executable 在 Contents/MacOS/staffdeck，需跳到同级的 Resources。
    # - Linux/Windows onedir：runtime 在可执行文件同级 runtime/。
    # 不用 resource_dir()==sys._MEIPASS（onedir 下指向 _internal/）。
    exe_dir = Path(sys.executable).resolve().parent
    if sys.platform == "darwin" and exe_dir.name == "MacOS" and exe_dir.parent.name == "Contents":
        root = exe_dir.parent / "Resources" / "runtime"
    else:
        root = exe_dir / "runtime"
    return (root / "python.exe") if sys.platform == "win32" else (root / "bin" / "python3")


def _resolve_runtime_python(explicit_python: str, explicit_venv: str) -> Path:
    """按「显式解释器 → 显式 venv → 打包内置 → backend/.venv → .runtime_venv」定序选择。"""
    if explicit_python.strip():
        return Path(explicit_python).expanduser()
    if explicit_venv.strip():
        return _python_in_venv(Path(explicit_venv).expanduser())
    from app import paths
    if paths.is_frozen() and _bundled_python().exists():
        return _bundled_python()
    backend_venv = _backend_dir() / ".venv"
    if _python_in_venv(backend_venv).exists():
        return _python_in_venv(backend_venv)
    return _python_in_venv(_backend_dir() / ".runtime_venv")


def _python_in_venv(venv_dir: Path) -> Path:
    """返回 venv 内解释器的约定路径（Windows 在 Scripts/，其他平台在 bin/）。"""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _create_runtime_venv(python_path: Path) -> None:
    """按解释器路径推导 venv 目录并创建带 pip 的虚拟环境，失败时抛错而不是静默跳过。"""
    venv_dir = python_path.parent.parent
    venv_dir.mkdir(parents=True, exist_ok=True)
    venv.EnvBuilder(with_pip=True, clear=False).create(venv_dir)
    if not python_path.exists():
        raise GeneralSkillRuntimeError(f"通用技能运行环境创建失败：{python_path}")


def _ensure_packages(python_path: Path, packages: list[str]) -> None:
    """把缺失的依赖一次性装进运行环境；版本不满足或 import 名不可导入都算缺失。

    安装失败时抛出带 pip stderr 的 GeneralSkillRuntimeError，便于定位网络或索引问题。
    """
    settings = get_settings()
    requirements = [_parse_requirement(package) for package in packages]
    missing = [
        str(requirement)
        for requirement in requirements
        if not _requirement_satisfied(python_path, requirement)
        or not _can_import(python_path, _import_name(requirement.name))
    ]
    if not missing:
        return
    args = [str(python_path), "-m", "pip", "install", *missing]
    if settings.general_skill_pip_index_url.strip():
        args += ["-i", settings.general_skill_pip_index_url.strip()]
    env = os.environ.copy()
    try:
        import certifi
        env["SSL_CERT_FILE"] = certifi.where()
        env["PIP_CERT"] = certifi.where()
    except (ImportError, OSError):
        pass
    result = subprocess.run(
        args,
        cwd=str(_backend_dir()),
        text=True,
        capture_output=True,
        timeout=settings.general_skill_pip_timeout_seconds,
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise GeneralSkillRuntimeError(
            "通用技能运行环境依赖安装失败："
            + ", ".join(missing)
            + "\n"
            + (result.stderr or result.stdout or "").strip()
        )


def _can_import(python_path: Path, import_name: str) -> bool:
    """在目标解释器里实际 import 一次，确认模块名可用（用于兜底发行名与导入名不一致）。"""
    result = subprocess.run(
        [
            str(python_path),
            "-c",
            "import importlib,sys; importlib.import_module(sys.argv[1])",
            import_name,
        ],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    return result.returncode == 0


def _requirement_satisfied(python_path: Path, requirement: Requirement) -> bool:
    """查已安装版本并比对 specifier；环境标记不适用于当前平台时按已满足处理。"""
    if requirement.marker is not None and not requirement.marker.evaluate():
        return True
    result = subprocess.run(
        [
            str(python_path),
            "-c",
            "import importlib.metadata,sys; print(importlib.metadata.version(sys.argv[1]))",
            requirement.name,
        ],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        return False
    try:
        version = Version(result.stdout.strip())
    except InvalidVersion:
        return False
    return not requirement.specifier or version in requirement.specifier


def _parse_requirement(value: str) -> Requirement:
    """解析依赖声明，拒绝 URL 直链与非法写法，保证自动安装只走包索引。"""
    try:
        requirement = Requirement(value)
    except InvalidRequirement as exc:
        raise GeneralSkillRuntimeError(f"通用技能依赖声明无效：{value}") from exc
    if requirement.url:
        raise GeneralSkillRuntimeError("通用技能自动依赖仅支持包索引中的版本约束。")
    return requirement


def _import_name(package: str) -> str:
    """把发行包名映射为可导入的模块名，未登记时把连字符换成下划线。"""
    normalized = package.strip()
    return IMPORT_NAMES.get(normalized, normalized.replace("-", "_"))


@contextmanager
def _runtime_prepare_file_lock(python_path: Path) -> Iterator[None]:
    """以解释器路径哈希为名的用户级文件锁串行化建环境，跨进程避免并发写坏 venv。"""
    from app import paths

    digest = sha256(str(python_path.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = paths.user_data_dir() / f"runtime-prepare-{digest}.lock"
    handle = lock_path.open("a+b")
    try:
        _lock_file(handle)
        yield
    finally:
        _unlock_file(handle)
        handle.close()


def _lock_file(handle: BinaryIO) -> None:
    """对已打开的文件句柄加独占锁；Windows 用 msvcrt，其他平台用 flock。"""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: BinaryIO) -> None:
    """释放 _lock_file 取得的独占锁，调用方随后负责关闭句柄。"""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

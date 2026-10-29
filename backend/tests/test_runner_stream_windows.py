import subprocess
import sys

from app.general_skills import runner


def test_stream_output_reads_stdout_cross_platform() -> None:
    """验证默认流式实现能收集子进程 stdout 与 stderr，且不误判超时。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('hello-out'); sys.stderr.write('hello-err')"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out, err, timed_out = runner._stream_process_output(proc, [], None, 1)
    assert "hello-out" in out
    assert "hello-err" in err
    assert timed_out is False


def test_threaded_reader_reads_output_directly() -> None:
    """验证线程读取实现同样能收集 stdout 与 stderr，且不误判超时。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdout.write('t-out'); sys.stderr.write('t-err')"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out, err, timed_out = runner._stream_process_output_threaded(proc, [], None, 1)
    assert "t-out" in out
    assert "t-err" in err
    assert timed_out is False


def test_windows_stream_impl_selected(monkeypatch) -> None:
    """验证平台为 win32 时选用线程读取实现。"""
    monkeypatch.setattr(sys, "platform", "win32")
    assert runner._use_thread_reader() is True


def test_posix_stream_impl_selected(monkeypatch) -> None:
    """验证非 Windows 平台不启用线程读取实现，沿用默认可 select 路径。"""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert runner._use_thread_reader() is False

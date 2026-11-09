"""LLMClient 生命周期测试：关闭、幂等、上下文管理与流式所有权。"""

from collections.abc import Iterator
from typing import ClassVar

import pytest

from app.core import response_generator as response_generator_module
from app.core.response_generator import ResponseGenerator
from app.db.models import ChatSession
from app.llm.client import LLMClient
from app.session.session_schema import RouterDecision, StepAgentResult


class _StubTransport:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _FailingTransport:
    def close(self) -> None:
        raise RuntimeError("transport already broken")


def _bare_client(transport: object) -> LLMClient:
    """绕过 __init__ 构造只带底层传输的客户端，避免依赖数据库配置。"""
    client = object.__new__(LLMClient)
    client.client = transport
    return client


def test_client_closes_transport() -> None:
    """close() 必须释放底层 SDK 客户端，避免长跑进程句柄泄漏。"""
    transport = _StubTransport()
    client = _bare_client(transport)

    client.close()

    assert transport.close_calls == 1
    assert client._closed is True


def test_client_close_is_idempotent() -> None:
    """重复 close() 只关闭一次，兼容 with 与显式关闭并存的路径。"""
    transport = _StubTransport()
    client = _bare_client(transport)

    client.close()
    client.close()

    assert transport.close_calls == 1


def test_client_close_never_raises() -> None:
    """底层关闭失败只记录日志，不能把异常抛给调用方。"""
    client = _bare_client(_FailingTransport())

    client.close()

    assert client._closed is True


def test_client_is_context_manager() -> None:
    """with 退出时关闭连接，__enter__ 返回自身。"""
    transport = _StubTransport()
    client = _bare_client(transport)

    with client as entered:
        assert entered is client

    assert transport.close_calls == 1


def test_context_manager_closes_on_exception() -> None:
    """with 内抛异常时仍然关闭连接，并让异常继续向上传播。"""
    transport = _StubTransport()
    client = _bare_client(transport)

    with pytest.raises(RuntimeError, match="boom"), client:
        raise RuntimeError("boom")

    assert transport.close_calls == 1


class _StreamingStubClient:
    """记录关闭状态的最小 LLMClient 替身，供流式所有权测试使用。"""

    instances: ClassVar[list["_StreamingStubClient"]] = []

    def __init__(self, model_config: object) -> None:
        self.closed = False
        type(self).instances.append(self)

    def generate_text_stream(
        self, system_prompt: str, payload: object
    ) -> Iterator[str]:
        yield "first"
        yield "second"

    def close(self) -> None:
        self.closed = True


def test_stream_client_closes_when_consumer_stops_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """消费方提前结束迭代时，流式生成器仍要关闭自己持有的客户端。"""
    _StreamingStubClient.instances.clear()
    monkeypatch.setattr(response_generator_module, "LLMClient", _StreamingStubClient)
    stream = ResponseGenerator().generate_stream(
        message="查一下订单",
        session=ChatSession(id="session_test", tenant_id="tenant_demo"),
        skill=None,
        router_decision=RouterDecision(decision="continue_active"),
        step_result=StepAgentResult(),
        tool_result=None,
        model_config=None,  # type: ignore[arg-type]
    )

    assert next(stream) == "first"
    stream.close()

    assert _StreamingStubClient.instances[0].closed is True

# backend/tests/test_harness_session_lock_redis.py
"""Redis 后端的 Harness 会话互斥：跨进程排他、TTL 与 token 校验释放。"""

import fakeredis
import pytest

from app.core.harness_session_lock import (
    HarnessSessionBusy,
    HarnessSessionLock,
    HarnessSessionLockHandle,
)

_KEY = "staffdeck:harness_session:session-1"


def _lock(client: fakeredis.FakeStrictRedis) -> HarnessSessionLock:
    """用假 Redis 客户端构造 redis 后端，避免测试依赖真实 Redis。"""
    return HarnessSessionLock("redis", client=client)


def test_redis_acquire_and_release_roundtrip():
    """验证 acquire 写入带 TTL 的键，release 用同一 token 把键删掉。"""
    client = fakeredis.FakeStrictRedis()
    lock = _lock(client)
    handle = lock.acquire("session-1")
    assert client.get(_KEY) is not None
    assert client.pttl(_KEY) > 0
    lock.release(handle)
    assert client.get(_KEY) is None


def test_second_acquire_fails_while_held():
    """验证同一会话被另一个进程持有时，第二次 acquire 抛忙碌错误。"""
    client = fakeredis.FakeStrictRedis()
    first = _lock(client)
    second = _lock(client)
    handle = first.acquire("session-1")
    with pytest.raises(HarnessSessionBusy):
        second.acquire("session-1")
    first.release(handle)
    second.acquire("session-1")


def test_release_with_wrong_token_keeps_lock():
    """验证 token 不匹配的释放不会删掉持有者的键。"""
    client = fakeredis.FakeStrictRedis()
    lock = _lock(client)
    lock.acquire("session-1")
    forged = HarnessSessionLockHandle(
        session_id="session-1", backend="redis", token="another-owner"
    )
    lock.release(forged)
    assert client.get(_KEY) is not None


def test_blank_session_id_is_rejected():
    """验证空白 session_id 在两种后端前就被拒绝，避免出现共享空键。"""
    lock = _lock(fakeredis.FakeStrictRedis())
    with pytest.raises(ValueError):
        lock.acquire("   ")


class _EvalBoomClient:
    """set 正常、eval 抛错，模拟释放时 Redis 短暂不可用。"""

    def __init__(self, client: fakeredis.FakeStrictRedis) -> None:
        """包装真客户端，只在 eval（释放）路径注入故障。"""
        self._client = client

    def set(self, *args, **kwargs):
        """透传抢锁写入，保证 acquire 行为不变。"""
        return self._client.set(*args, **kwargs)

    def eval(self, *args, **kwargs):
        """释放时模拟 Redis 故障。"""
        raise RuntimeError("redis eval unavailable")


def test_release_is_best_effort_when_redis_errors(caplog):
    """释放失败只记一条 warning，不抛出；键清理后仍可重新抢锁。"""
    import logging

    client = fakeredis.FakeStrictRedis()
    lock = HarnessSessionLock("redis", client=_EvalBoomClient(client))
    caplog.set_level(logging.WARNING, logger="app.core.harness_session_lock")
    handle = lock.acquire("session-1")

    lock.release(handle)  # 不应抛出：回合收尾路径不能因 Redis 抖动而 500

    warnings = [
        record
        for record in caplog.records
        if record.name == "app.core.harness_session_lock"
        and record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "harness session lock release failed" in warnings[0].getMessage()

    client.delete(_KEY)  # 模拟 TTL 到期或他人已清理
    lock.acquire("session-1")

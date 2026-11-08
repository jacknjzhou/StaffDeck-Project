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

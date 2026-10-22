# backend/tests/test_distributed_lease.py
import fakeredis

from app.distributed.lease import RedisLease


def test_acquire_is_exclusive():
    client = fakeredis.FakeStrictRedis()
    a = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-a")
    b = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-b")
    assert a.acquire() is True
    assert b.acquire() is False
    a.release()
    assert b.acquire() is True
    b.release()


def test_release_only_by_owner():
    client = fakeredis.FakeStrictRedis()
    a = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-a")
    a.acquire()
    b = RedisLease(client, "staffdeck:lease:api", ttl_seconds=60, holder="proc-b")
    b.release()  # 不应删掉 a 的锁
    assert client.get("staffdeck:lease:api") is not None
    a.release()
    assert client.get("staffdeck:lease:api") is None


def test_renew_extends_ttl():
    import time

    client = fakeredis.FakeStrictRedis()
    lease = RedisLease(client, "staffdeck:lease:api", ttl_seconds=1, holder="p")
    assert lease.acquire() is True
    time.sleep(0.05)
    assert lease.renew() is True
    assert client.ttl("staffdeck:lease:api") > 0

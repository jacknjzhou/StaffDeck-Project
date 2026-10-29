import base64
import json
import logging
import threading
import time

import httpx
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.channels.adapters.wechat import (
    WeChatAdapter,
    WeChatClient,
    WeChatPollManager,
    decrypt_wechat_media,
    is_self_message,
    normalize_wechat_message,
    random_wechat_uin,
    split_wechat_text,
)
from app.channels.crypto import encrypt_channel_secret
from app.db.models import ChannelBinding, Tenant

BASE_URL = "https://ilinkai.weixin.qq.com"


def _test_engine():
    """创建内存 SQLite 引擎并建表，供轮询相关用例隔离使用。"""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _client(handler) -> WeChatClient:
    """用给定请求处理器构造指向测试域名的微信客户端。"""
    return WeChatClient(BASE_URL, "bot_token_x", transport=httpx.MockTransport(handler))


def test_decrypt_wechat_media_aes_ecb_pkcs7() -> None:
    """验证 AES-ECB/PKCS7 媒体解密能还原明文。"""
    key = b"0123456789abcdef"
    aes_key = base64.b64encode(key.hex().encode("ascii")).decode("ascii")
    plaintext = b"\xff\xd8\xffjpeg-data\xff\xd9"
    encrypted = bytes.fromhex("06780e9084a586882bfbed1c7dbd461b")

    assert decrypt_wechat_media(encrypted, aes_key, expected_size=len(plaintext)) == plaintext


def _text_message(**overrides) -> dict:
    """构造一条标准单聊文本消息，可按需覆盖任意字段。"""
    msg = {
        "seq": 429,
        "message_id": 9812451782375,
        "from_user_id": "user_ab12cd34@im.wechat",
        "to_user_id": "bot_1@im.bot",
        "client_id": "wx-msg-1",
        "session_id": "user_ab12cd34@im.wechat#bot_1@im.bot",
        "message_type": 1,
        "message_state": 2,
        "context_token": "ctx_token_1",
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
    }
    msg.update(overrides)
    return msg


def test_random_wechat_uin_format() -> None:
    """验证随机 uin 是 base64 编码的十进制字符串，且取值在 32 位范围内。"""
    first, second = random_wechat_uin(), random_wechat_uin()
    assert first != second
    decoded = base64.b64decode(first).decode("utf-8")
    assert decoded.isdigit()
    assert 0 <= int(decoded) <= 2**32 - 1


def test_split_text_short() -> None:
    """验证短文本不被切分，空文本返回空列表。"""
    assert split_wechat_text("") == []
    assert split_wechat_text("短文本") == ["短文本"]
    assert split_wechat_text("x" * 2000) == ["x" * 2000]


def test_split_text_prefers_paragraph_break() -> None:
    """验证长文本优先在空行处切分。"""
    head = "a" * 1500
    tail = "b" * 1200
    text = head + "\n\n" + tail
    chunks = split_wechat_text(text)
    assert chunks == [head, tail]


def test_split_text_falls_back_to_newline_then_space() -> None:
    """验证无空行时依次退化为按换行、空格切分。"""
    head = "a" * 1500
    tail = "b" * 900
    chunks = split_wechat_text(head + "\n" + tail)
    assert chunks == [head, tail]

    head_space = "a" * 1500
    tail_space = "b" * 900
    chunks = split_wechat_text(head_space + " " + tail_space)
    assert chunks == [head_space, tail_space]


def test_split_text_hard_cut_without_boundaries() -> None:
    """验证没有可切分边界时按上限硬切并保持内容完整。"""
    text = "x" * 4500
    chunks = split_wechat_text(text)
    assert [len(chunk) for chunk in chunks] == [2000, 2000, 500]
    assert "".join(chunks) == text


def test_get_updates_request_and_parse() -> None:
    """验证 getupdates 的请求头、请求体、响应解析以及消息归一化结果。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """记录请求细节并返回一条新消息与下一页游标。"""
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["auth_type"] = request.headers.get("AuthorizationType")
        captured["uin"] = request.headers.get("X-WECHAT-UIN")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "ret": 0,
                "msgs": [_text_message()],
                "get_updates_buf": "next_cursor",
                "longpolling_timeout_ms": 35000,
            },
        )

    client = _client(handler)
    resp = client.get_updates("cur_cursor")

    assert captured["url"] == f"{BASE_URL}/ilink/bot/getupdates"
    assert captured["authorization"] == "Bearer bot_token_x"
    assert captured["auth_type"] == "ilink_bot_token"
    assert captured["uin"]
    assert captured["body"]["get_updates_buf"] == "cur_cursor"
    assert captured["body"]["base_info"]["channel_version"]
    assert resp["get_updates_buf"] == "next_cursor"

    inbound = normalize_wechat_message(resp["msgs"][0], ilink_bot_id="bot_1@im.bot")
    assert inbound is not None
    assert inbound.event_id == "9812451782375"
    assert inbound.text == "你好"
    assert inbound.context_token == "ctx_token_1"
    assert inbound.is_group is False
    assert inbound.external_conv_id == "wechat_p2p_user_ab12cd34@im.wechat"


def test_normalize_image_and_file_items() -> None:
    """验证图片与文件条目被归一化为带下载参数的附件。"""
    image = normalize_wechat_message(
        _text_message(
            item_list=[{"type": 2, "image_item": {"media_id": "image-1"}}],
        )
    )
    assert image is not None
    assert image.attachments[0].media_id == "image-1"
    assert image.attachments[0].download_params["context_token"] == "ctx_token_1"

    file = normalize_wechat_message(
        _text_message(
            item_list=[
                {
                    "type": 4,
                    "file_item": {
                        "file_name": "a.txt",
                        "len": "12",
                        "md5": "md5",
                        "media": {
                            "aes_key": "aes",
                            "encrypt_query_param": "encrypted",
                            "full_url": f"{BASE_URL}/c2c/download?encrypted_query_param=encrypted&taskid=task",
                        },
                    },
                }
            ],
        )
    )
    assert file is not None
    assert file.attachments[0].media_id.endswith("/c2c/download?encrypted_query_param=encrypted&taskid=task")
    assert file.attachments[0].filename == "a.txt"


def test_normalize_text_message_does_not_emit_attachment_warning(caplog) -> None:
    """验证纯文本消息不会产生附件诊断告警日志。"""
    with caplog.at_level(logging.WARNING, logger="app.channels.adapters.wechat"):
        assert normalize_wechat_message(_text_message()) is not None

    assert "附件诊断" not in caplog.text


def test_normalize_actual_image_media_shape() -> None:
    """验证真实图片媒体结构被解析为图片附件及预期下载参数。"""
    inbound = normalize_wechat_message(
        _text_message(
            item_list=[
                {
                    "type": 2,
                    "image_item": {
                        "aeskey": "aes",
                        "media": {
                            "aes_key": "aes",
                            "encrypt_query_param": "encrypted",
                            "full_url": f"{BASE_URL}/c2c/download?encrypted_query_param=encrypted&taskid=task",
                        },
                        "mid_size": 10,
                    },
                }
            ],
        )
    )
    assert inbound is not None
    attachment = inbound.attachments[0]
    assert attachment.kind == "image"
    assert attachment.download_params["full_url"].endswith("taskid=task")
    assert attachment.download_params["aes_key"] == "aes"
    assert attachment.download_params["declared_size"] == 10
    assert "expected_size" not in attachment.download_params


def test_download_media_request() -> None:
    """验证下载媒体请求的地址与请求体参数。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """记录下载请求并返回图片字节。"""
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/jpeg"})

    data = _client(handler).download_media("ctx-1", "media-1")
    assert data == b"image-bytes"
    assert captured["url"] == f"{BASE_URL}/ilink/bot/downloadmedia"
    assert captured["body"]["context_token"] == "ctx-1"
    assert captured["body"]["media_id"] == "media-1"


def test_send_message_payload() -> None:
    """验证发送消息的地址与消息体字段。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """记录发送请求并返回空的成功响应。"""
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    client = _client(handler)
    client.send_message("user_1@im.wechat", "ctx_token_1", "回复文本")

    assert captured["url"] == f"{BASE_URL}/ilink/bot/sendmessage"
    msg = captured["body"]["msg"]
    assert msg["to_user_id"] == "user_1@im.wechat"
    assert msg["context_token"] == "ctx_token_1"
    assert msg["message_type"] == 2
    assert msg["message_state"] == 2
    assert msg["client_id"]
    assert msg["item_list"] == [{"type": 1, "text_item": {"text": "回复文本"}}]
    assert captured["body"]["base_info"]["channel_version"]


def test_send_message_raises_on_errcode() -> None:
    """验证接口返回错误码时发送抛出异常。"""
    def handler(request: httpx.Request) -> httpx.Response:
        """返回带错误码的响应，模拟发送失败。"""
        return httpx.Response(200, json={"ret": -2, "errmsg": "参数错误"})

    client = _client(handler)
    try:
        client.send_message("user_1", "ctx", "hi")
        raised = False
    except Exception as exc:
        raised = True
        assert "-2" in str(exc)
    assert raised


def test_adapter_splits_long_text_into_multiple_sends() -> None:
    """验证超长文本被拆成多条分片发送，且分片 client_id 互不相同。"""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """记录每次发送的消息体并返回成功。"""
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = _client(handler)
    adapter = WeChatAdapter(client_factory=lambda binding: client)
    binding = ChannelBinding(tenant_id="t", agent_id="a", channel="wechat", status="active")
    adapter.send(binding, {"to_user_id": "user_1", "context_token": "ctx"}, "x" * 4500)

    assert len(sent) == 3
    texts = [payload["msg"]["item_list"][0]["text_item"]["text"] for payload in sent]
    assert "".join(texts) == "x" * 4500
    assert all(len(chunk) <= 2000 for chunk in texts)
    client_ids = {payload["msg"]["client_id"] for payload in sent}
    assert len(client_ids) == 3


def test_adapter_requires_target_fields() -> None:
    """验证目标字段缺失时适配器拒绝发送并抛出 ValueError。"""
    adapter = WeChatAdapter(client_factory=lambda binding: None)
    binding = ChannelBinding(tenant_id="t", agent_id="a", channel="wechat", status="active")
    try:
        adapter.send(binding, {"to_user_id": "", "context_token": ""}, "hi")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_normalize_drops_self_messages() -> None:
    """验证机器人自己发出的消息在判断与归一化阶段都被丢弃。"""
    assert is_self_message(_text_message(message_type=2), "bot_1@im.bot") is True
    assert is_self_message(_text_message(from_user_id="bot_1@im.bot"), "bot_1@im.bot") is True
    assert normalize_wechat_message(_text_message(message_type=2), ilink_bot_id="bot_1@im.bot") is None
    assert normalize_wechat_message(
        _text_message(from_user_id="bot_1@im.bot"), ilink_bot_id="bot_1@im.bot"
    ) is None


def test_normalize_drops_non_text_or_missing_context() -> None:
    """验证无文本、缺上下文或空 item 列表的消息被丢弃。"""
    image_only = _text_message(item_list=[{"type": 2, "image_item": {"url": "x"}}])
    assert normalize_wechat_message(image_only) is None
    assert normalize_wechat_message(_text_message(context_token="")) is None
    assert normalize_wechat_message(_text_message(item_list=[])) is None


def test_voice_message_text_is_extracted() -> None:
    """验证语音消息中的文本被提取为入站文本。"""
    voice_msg = _text_message(
        item_list=[
            {
                "type": 3,
                "voice_item": {
                    "media": {"encrypt_query_param": "x"},
                    "encode_type": 6,
                    "text": "我下午三点到。",
                },
            }
        ]
    )
    inbound = normalize_wechat_message(voice_msg)
    assert inbound is not None
    assert inbound.text == "我下午三点到。"


def test_voice_message_without_text_is_dropped() -> None:
    """验证无文本的语音消息被丢弃。"""
    voice_msg = _text_message(item_list=[{"type": 3, "voice_item": {"encode_type": 6}}])
    assert normalize_wechat_message(voice_msg) is None


def test_text_and_voice_items_join() -> None:
    """验证文本与语音条目按顺序拼接为一条入站文本。"""
    mixed = _text_message(
        item_list=[
            {"type": 1, "text_item": {"text": "先听语音"}},
            {"type": 3, "voice_item": {"text": "语音内容"}},
        ]
    )
    inbound = normalize_wechat_message(mixed)
    assert inbound is not None
    assert inbound.text == "先听语音\n语音内容"


def test_normalize_group_message() -> None:
    """验证群消息被识别为群会话，并用兜底规则区分单聊。"""
    group_msg = _text_message(group_id="room_123456", session_id="room_123456")
    inbound = normalize_wechat_message(group_msg)
    assert inbound is not None
    assert inbound.is_group is True
    assert inbound.conv_key == "room_123456"
    assert inbound.external_conv_id == "wechat_group_room_123456"

    # 兜底:无 group_id 时,不含 # 且不同于发言人的 session_id 视为群
    fallback_group = _text_message(group_id=None, session_id="room_999")
    inbound = normalize_wechat_message(fallback_group)
    assert inbound is not None and inbound.is_group is True

    # p2p 会话 session_id 形如 "user#bot",不能误判为群
    p2p = normalize_wechat_message(_text_message())
    assert p2p is not None and p2p.is_group is False


def test_event_id_fallback_order() -> None:
    """验证事件 ID 依次回退到 message_id、msg_id、client_id，全缺则丢弃。"""
    by_msg_id = normalize_wechat_message(_text_message(message_id=None, msg_id="mid_1"))
    assert by_msg_id is not None and by_msg_id.event_id == "mid_1"
    by_client_id = normalize_wechat_message(_text_message(message_id=None, msg_id=None))
    assert by_client_id is not None and by_client_id.event_id == "wx-msg-1"
    assert normalize_wechat_message(
        _text_message(message_id=None, msg_id=None, client_id=None)
    ) is None


def test_qrcode_endpoints() -> None:
    """验证扫码登录两个接口的请求方式、参数与响应解析。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """按路径校验并响应二维码相关请求。"""
        path = request.url.path
        captured[path] = request
        if path == "/ilink/bot/get_bot_qrcode":
            assert request.method == "POST"
            assert request.url.params["bot_type"] == "3"
            assert json.loads(request.content) == {"local_token_list": ["old_token_1"]}
            return httpx.Response(200, json={"qrcode": "qrc_1", "qrcode_img_content": "https://weixin.qq.com/x/abc"})
        if path == "/ilink/bot/get_qrcode_status":
            assert request.method == "GET"
            assert request.headers.get("iLink-App-ClientVersion") == "1"
            return httpx.Response(
                200,
                json={
                    "status": "confirmed",
                    "bot_token": "tok",
                    "ilink_bot_id": "bot@im.bot",
                    "baseurl": BASE_URL,
                },
            )
        return httpx.Response(404)

    client = _client(handler)
    qrcode = client.get_bot_qrcode(local_token_list=["old_token_1"])
    assert qrcode["qrcode"] == "qrc_1"
    status = client.get_qrcode_status("qrc_1", verify_code="8823")
    assert status["status"] == "confirmed"
    assert status["ilink_bot_id"] == "bot@im.bot"
    assert captured["/ilink/bot/get_qrcode_status"].url.params["qrcode"] == "qrc_1"
    assert captured["/ilink/bot/get_qrcode_status"].url.params["verify_code"] == "8823"


def test_get_bot_qrcode_defaults_to_empty_token_list() -> None:
    """验证未传 token 列表时请求体默认为空列表。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """记录请求体并返回二维码响应。"""
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"qrcode": "qrc_1"})

    client = _client(handler)
    client.get_bot_qrcode()
    assert captured["body"] == {"local_token_list": []}


def test_get_qrcode_status_without_verify_code() -> None:
    """验证未传 verify_code 时查询参数不包含该字段。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """记录查询参数并返回等待扫码状态。"""
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"status": "wait"})

    client = _client(handler)
    assert client.get_qrcode_status("qrc_1")["status"] == "wait"
    assert "verify_code" not in captured["params"]


def test_get_config_and_send_typing_payloads() -> None:
    """验证 getconfig 与 sendtyping 的请求体结构。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        """按路径记录请求体并返回对应响应。"""
        captured[request.url.path] = json.loads(request.content)
        if request.url.path == "/ilink/bot/getconfig":
            return httpx.Response(200, json={"ret": 0, "typing_ticket": "ticket_1"})
        return httpx.Response(200, json={"ret": 0})

    client = _client(handler)
    data = client.get_config("user_1@im.wechat", "ctx_1")
    assert data["typing_ticket"] == "ticket_1"
    assert captured["/ilink/bot/getconfig"] == {
        "ilink_user_id": "user_1@im.wechat",
        "context_token": "ctx_1",
        "base_info": {"channel_version": "1.0.0"},
    }

    client.send_typing("user_1@im.wechat", "ticket_1", 1)
    assert captured["/ilink/bot/sendtyping"] == {
        "ilink_user_id": "user_1@im.wechat",
        "typing_ticket": "ticket_1",
        "status": 1,
        "base_info": {"channel_version": "1.0.0"},
    }


def test_client_for_binding_decrypts_credentials() -> None:
    """验证按绑定构造客户端时解密凭据并采用配置的域名。"""
    binding = ChannelBinding(
        tenant_id="t",
        agent_id="a",
        channel="wechat",
        status="active",
        credentials_enc=encrypt_channel_secret("real_bot_token"),
        config_json={"baseurl": "https://szilinkai.weixin.qq.com"},
    )
    client = WeChatClient.for_binding(binding)
    assert client.bot_token == "real_bot_token"
    assert client.base_url == "https://szilinkai.weixin.qq.com"


def test_poll_loop_marks_expired_on_minus_14() -> None:
    """验证连续 -14 达到上限后绑定被判过期并清空游标。"""
    engine = _test_engine()

    def handler(request: httpx.Request) -> httpx.Response:
        """始终返回会话超时错误码。"""
        return httpx.Response(200, json={"ret": -14, "errcode": -14, "errmsg": "session timeout"})

    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_1",
            channel="wechat",
            status="active",
            connected=True,
            credentials_enc=encrypt_channel_secret("tok"),
            config_json={"baseurl": BASE_URL, "ilink_bot_id": "bot@im.bot", "get_updates_buf": "cur"},
        )
        db.add(binding)
        db.commit()
        binding_id = binding.id

    client = _client(handler)
    manager = WeChatPollManager(
        db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=0.02
    )
    # 连续 -14 达恢复上限后:判真过期(expired + 清游标 + 线程退出)
    manager._poll_loop(binding_id, threading.Event())

    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert binding.status == "expired"
        assert binding.connected is False
        assert binding.config_json["session_expired"] is True
        assert binding.config_json["get_updates_buf"] == ""


def test_poll_loop_persists_cursor_and_processes_messages() -> None:
    """验证轮询会持久化游标并把消息交给 intake 处理。"""
    engine = _test_engine()
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        """首轮返回一条消息与游标，之后返回 -14 触发退出。"""
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                200,
                json={"ret": 0, "msgs": [_text_message()], "get_updates_buf": "next_cursor"},
            )
        return httpx.Response(200, json={"ret": -14, "errcode": -14})

    processed: list[dict] = []

    def fake_process_inbound(binding, msg, *, db_engine=None):
        """记录收到的消息并直接返回处理成功。"""
        processed.append(msg)
        return True

    import app.channels.service_intake as intake

    original = intake.process_inbound
    intake.process_inbound = fake_process_inbound
    try:
        with Session(engine) as db:
            db.add(Tenant(id="tenant_demo", name="Demo"))
            binding = ChannelBinding(
                tenant_id="tenant_demo",
                agent_id="agent_1",
                channel="wechat",
                status="active",
                credentials_enc=encrypt_channel_secret("tok"),
                config_json={"baseurl": BASE_URL, "ilink_bot_id": "bot_1@im.bot"},
            )
            db.add(binding)
            db.commit()
            binding_id = binding.id

        client = _client(handler)
        manager = WeChatPollManager(
            db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=0.02
        )
        manager._poll_loop(binding_id, threading.Event())
    finally:
        intake.process_inbound = original

    assert len(processed) == 1
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        # 第二轮起连续 -14 达恢复上限:游标被清空并标记过期
        assert binding.status == "expired"


def test_reconcile_starts_and_stops_binding_threads() -> None:
    """验证 reconcile 拉起新激活绑定并停掉已消失绑定的线程。"""
    engine = _test_engine()

    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        active = ChannelBinding(tenant_id="tenant_demo", agent_id="a1", channel="wechat", status="active")
        db.add(active)
        db.commit()
        active_id = active.id

    manager = WeChatPollManager(db_engine=engine, client_factory=lambda binding: None)
    started: list[str] = []
    stopped: list[str] = []
    manager.ensure_binding = lambda binding_id: started.append(binding_id)  # noqa: E731
    manager.stop_binding = lambda binding_id: stopped.append(binding_id)  # noqa: E731
    manager.running_binding_ids = lambda: {"stale_binding"}

    manager.reconcile_once()
    assert started == [active_id]
    assert stopped == ["stale_binding"]


class _RecordingPollClient:
    """记录每次 get_updates 的 timeout_seconds,按脚本返回响应。"""

    def __init__(self, responses: list[dict]):
        """保存脚本响应并初始化超时记录。"""
        self.responses = list(responses)
        self.timeouts: list[float | None] = []

    def get_updates(self, cursor: str, *, timeout_seconds: float = 40.0):
        """记录本次超时值并弹出下一条脚本响应。"""
        self.timeouts.append(timeout_seconds)
        return self.responses.pop(0)


def test_poll_loop_follows_longpolling_timeout_ms() -> None:
    """验证轮询超时随服务端下发值调整，并被钳制在上下限内。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_1",
            channel="wechat",
            status="active",
            config_json={"ilink_bot_id": "bot_1@im.bot"},
        )
        db.add(binding)
        db.commit()
        binding_id = binding.id

    client = _RecordingPollClient(
        [
            {"ret": 0, "msgs": [], "get_updates_buf": "c1", "longpolling_timeout_ms": 15000},
            {"ret": 0, "msgs": [], "get_updates_buf": "c2", "longpolling_timeout_ms": 5000},
            {"ret": 0, "msgs": [], "get_updates_buf": "c3", "longpolling_timeout_ms": 120000},
            # 自愈策略下 -14 进入恢复冷却,连续 5 次后判真过期退出
            {"ret": -14, "errcode": -14},
            {"ret": -14, "errcode": -14},
            {"ret": -14, "errcode": -14},
            {"ret": -14, "errcode": -14},
            {"ret": -14, "errcode": -14},
        ]
    )
    manager = WeChatPollManager(
        db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=0.02
    )
    manager._poll_loop(binding_id, threading.Event())

    # 默认 40s → 服务端下发 15s → 下限钳制 10s → 上限钳制 60s(此后按 60s 复用)
    assert client.timeouts[:4] == [40.0, 15.0, 10.0, 60.0]


# ---------- -14 自愈恢复策略 ----------


def _wait_for(condition, timeout: float = 5.0) -> bool:
    """轮询等待条件成立，超时返回 False。"""
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if condition():
            return True
        _time.sleep(0.02)
    return False


def _seed_poll_binding(engine, **config_overrides) -> str:
    """写入一条激活的微信绑定并返回 ID，可覆盖配置字段。"""
    config = {"baseurl": BASE_URL, "ilink_bot_id": "bot@im.bot", "get_updates_buf": "cur"}
    config.update(config_overrides)
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_1",
            channel="wechat",
            status="active",
            connected=True,
            credentials_enc=encrypt_channel_secret("tok"),
            config_json=config,
        )
        db.add(binding)
        db.commit()
        return binding.id


class _ScriptedPollClient:
    """按脚本应答 getupdates;脚本用完后返回 tail 响应。"""

    def __init__(self, responses: list[dict], tail: dict):
        """保存脚本响应、兜底响应与调用记录。"""
        self.responses = list(responses)
        self.tail = tail
        self.calls = 0
        self.cursors: list[str] = []

    def get_updates(self, get_updates_buf: str, *, timeout_seconds: float = 40.0) -> dict:
        """按脚本返回响应，脚本用尽后返回兜底响应。"""
        self.calls += 1
        self.cursors.append(get_updates_buf)
        if self.responses:
            return self.responses.pop(0)
        return dict(self.tail)


def test_first_minus_14_enters_recovery_without_expired() -> None:
    """验证首次 -14 只进入恢复冷却，不判过期也不清游标。"""
    engine = _test_engine()
    binding_id = _seed_poll_binding(engine)
    client = _ScriptedPollClient([], {"ret": -14, "errcode": -14})
    manager = WeChatPollManager(
        db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=60.0
    )
    manager.ensure_binding(binding_id)

    def in_recovery():
        """检查绑定是否已记录一次恢复失败。"""
        with Session(engine) as db:
            binding = db.get(ChannelBinding, binding_id)
            config = dict(binding.config_json or {})
            return config.get("recovery_failures") == 1

    assert _wait_for(in_recovery)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        config = dict(binding.config_json or {})
        # 首次 -14:不判 expired、不清游标、状态仍 active、三字段正确、线程活着等待
        assert binding.status == "active"
        assert binding.connected is False
        assert config["session_expired"] is True
        assert config["recovery_failures"] == 1
        assert config["next_recovery_at"]
        assert config["get_updates_buf"] == "cur"
    assert binding_id in manager.running_binding_ids()

    # 冷却期间 stop 能立即打断(不拖累停机),且不判 expired
    started = time.monotonic()
    manager.stop_binding(binding_id)
    assert _wait_for(lambda: binding_id not in manager.running_binding_ids(), timeout=2.0)
    assert time.monotonic() - started < 2.0
    with Session(engine) as db:
        assert db.get(ChannelBinding, binding_id).status == "active"


def test_recovery_success_clears_state_and_resumes_polling() -> None:
    """验证恢复成功后清除过期标记并继续推进游标。"""
    engine = _test_engine()
    binding_id = _seed_poll_binding(engine)
    client = _ScriptedPollClient(
        [{"ret": -14, "errcode": -14}],
        {"ret": 0, "msgs": [], "get_updates_buf": "cur2"},
    )
    manager = WeChatPollManager(
        db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=0.02
    )
    manager.ensure_binding(binding_id)

    def recovered():
        """检查绑定是否已恢复连接并清除过期标记。"""
        with Session(engine) as db:
            binding = db.get(ChannelBinding, binding_id)
            config = dict(binding.config_json or {})
            return binding.connected and config.get("session_expired") is False

    assert _wait_for(recovered)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        config = dict(binding.config_json or {})
        assert binding.status == "active"
        assert binding.connected is True
        assert config["session_expired"] is False
        assert config["recovery_failures"] == 0
        assert "next_recovery_at" not in config
        # 恢复后正常轮询:游标照常被持久化推进
        assert config["get_updates_buf"] == "cur2"
    # 重试用的是原游标(cur),恢复成功后推进到 cur2
    assert "cur" in client.cursors
    manager.stop_binding(binding_id)


def test_repeated_minus_14_marks_expired_at_cap() -> None:
    """验证连续 -14 达到上限次数后绑定才被判过期。"""
    engine = _test_engine()
    binding_id = _seed_poll_binding(engine)
    client = _ScriptedPollClient([], {"ret": -14, "errcode": -14})
    manager = WeChatPollManager(
        db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=0.02
    )
    manager._poll_loop(binding_id, threading.Event())

    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert binding.status == "expired"
        assert binding.connected is False
        assert binding.config_json["session_expired"] is True
        assert binding.config_json["get_updates_buf"] == ""
    # 4 次恢复冷却 + 第 5 次判死,共 5 次 getupdates
    assert client.calls == 5


def test_reconcile_does_not_restart_recovering_binding() -> None:
    """验证恢复冷却中的绑定不会被 reconcile 重复拉起或误杀。"""
    engine = _test_engine()
    binding_id = _seed_poll_binding(engine)
    client = _ScriptedPollClient([], {"ret": -14, "errcode": -14})
    manager = WeChatPollManager(
        db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=60.0
    )
    manager.ensure_binding(binding_id)
    assert _wait_for(lambda: client.calls >= 1)
    assert binding_id in manager.running_binding_ids()

    ensured: list[str] = []
    original_ensure = manager.ensure_binding
    manager.ensure_binding = lambda bid: ensured.append(bid)  # noqa: E731
    manager.reconcile_once()
    manager.ensure_binding = original_ensure
    # 恢复等待中的绑定 status 仍是 active 且线程活着:reconcile 不重复拉起、不误杀
    assert ensured == []
    assert binding_id in manager.running_binding_ids()
    manager.stop_binding(binding_id)


def test_cursor_advances_only_after_full_batch() -> None:
    """验证批内处理失败时游标不推进，下批重拉同一窗口。"""
    engine = _test_engine()
    binding_id = _seed_poll_binding(engine)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        """首轮返回一条消息，之后返回 -14 结束轮询。"""
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                200,
                json={"ret": 0, "msgs": [_text_message()], "get_updates_buf": "c1"},
            )
        return httpx.Response(200, json={"ret": -14, "errcode": -14})

    import app.channels.service_intake as intake

    attempts: list[dict] = []

    def failing_process_inbound(binding, msg, *, db_engine=None):
        """模拟处理过程崩溃并记录被处理的消息。"""
        attempts.append(msg)
        raise RuntimeError("处理中途崩溃")

    original = intake.process_inbound
    intake.process_inbound = failing_process_inbound
    try:
        client = _client(handler)
        manager = WeChatPollManager(
            db_engine=engine, client_factory=lambda binding: client, recovery_cooldown_seconds=0.02
        )
        manager._poll_loop(binding_id, threading.Event())
    finally:
        intake.process_inbound = original

    assert len(attempts) == 1
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        # 批内异常:游标不推进(下次重拉同批),c1 从未落库
        assert binding.config_json.get("get_updates_buf", "") == ""
        assert binding.status == "expired"


# ---------- 接入域名安全校验 ----------


def test_validate_wechat_host_rules() -> None:
    """验证接入域名校验只放行微信官方域名及其端口写法。"""
    from app.channels.adapters.wechat import validate_wechat_host

    assert validate_wechat_host("ilinkai.weixin.qq.com") is True
    assert validate_wechat_host("szilinkai.weixin.qq.com") is True
    assert validate_wechat_host("ILINKAI.WEIXIN.QQ.COM") is True
    assert validate_wechat_host("ilinkai.weixin.qq.com:443") is True
    assert validate_wechat_host("evil.com") is False
    assert validate_wechat_host("weixin.qq.com.evil.com") is False
    assert validate_wechat_host("") is False


def test_sanitize_wechat_baseurl_normalizes_and_falls_back() -> None:
    """验证域名清洗规范化为 https 域名，非法输入回退默认值。"""
    from app.channels.adapters.wechat import sanitize_wechat_baseurl

    default = "https://ilinkai.weixin.qq.com"
    # 合法域名:规范为 https://{host},丢弃 path/query
    assert sanitize_wechat_baseurl("https://szilinkai.weixin.qq.com/x/y?a=1", default=default) == (
        "https://szilinkai.weixin.qq.com"
    )
    assert sanitize_wechat_baseurl("http://ilinkai.weixin.qq.com", default=default) == default
    # 非法:回退默认
    assert sanitize_wechat_baseurl("https://evil.com", default=default) == default
    assert sanitize_wechat_baseurl("https://weixin.qq.com.evil.com", default=default) == default
    assert sanitize_wechat_baseurl("not-a-url", default=default) == default


def test_for_binding_clamps_stored_illegal_baseurl() -> None:
    """验证按绑定构造客户端时会纠正配置里非法的域名。"""
    binding = ChannelBinding(
        tenant_id="t",
        agent_id="a",
        channel="wechat",
        status="active",
        credentials_enc=encrypt_channel_secret("real_bot_token"),
        config_json={"baseurl": "https://evil.com"},
    )
    client = WeChatClient.for_binding(binding)
    assert client.base_url == "https://ilinkai.weixin.qq.com"

    binding.config_json = {"baseurl": "https://szilinkai.weixin.qq.com"}
    client = WeChatClient.for_binding(binding)
    assert client.base_url == "https://szilinkai.weixin.qq.com"


def test_reconfigure_stop_aborts_inflight_poll_and_restarts() -> None:
    """验证重配会中止在飞长轮询，并在重扫后用新客户端重启。"""
    engine = _test_engine()
    binding_id = _seed_poll_binding(engine)

    class BlockingPollClient:
        """模拟会阻塞在长轮询上的客户端，close 可解除阻塞。"""
        def __init__(self):
            """初始化关闭标记、调用计数与阻塞闸门。"""
            self.closed = False
            self.calls = 0
            self._gate = threading.Event()

        def get_updates(self, cursor, *, timeout_seconds: float = 40.0):
            """首轮立即返回，之后阻塞直到 close 唤醒。"""
            self.calls += 1
            if self.calls == 1:
                return {"ret": 0, "msgs": [], "get_updates_buf": "c1"}
            self._gate.wait(60)  # 模拟在飞长轮询
            return {"ret": 0, "msgs": [], "get_updates_buf": cursor}

        def close(self):
            """标记关闭并放行阻塞中的长轮询。"""
            self.closed = True
            self._gate.set()

    old_client = BlockingPollClient()
    created: list = []

    def factory(binding):
        # 同一连接期内复用同一 client;被 close(重配)后下次工厂调用给新 client
        """复用当前连接期的客户端，被关闭后为新连接期创建新实例。"""
        if not created:
            created.append(old_client)
        elif created[-1].closed:
            created.append(BlockingPollClient())
        return created[-1]

    manager = WeChatPollManager(db_engine=engine, client_factory=factory)
    manager.ensure_binding(binding_id)
    assert _wait_for(lambda: old_client.calls >= 2)

    started = time.monotonic()
    manager.stop_binding(binding_id)
    # 在飞长轮询被 close 中止,等待有界(远小于 40s)
    assert manager.wait_binding_stopped(binding_id, timeout_seconds=5.0) is True
    assert time.monotonic() - started < 5.0
    assert old_client.closed is True

    # 重扫重启:新 client 接管,老 client 不再处理新消息
    manager.ensure_binding(binding_id)
    assert _wait_for(lambda: len(created) >= 2 and created[1].calls >= 1)
    assert old_client.calls == 2
    manager.stop_binding(binding_id)
    assert manager.wait_binding_stopped(binding_id, timeout_seconds=5.0)


def test_timeout_then_reconcile_restores_old_wechat_config_once() -> None:
    """验证超时后暂停绑定，reconcile 只在旧线程退出后重启一次。"""
    engine = _test_engine()
    binding_id = _seed_poll_binding(engine)

    class ControlledPollClient:
        """可控制释放时机的轮询客户端，用于观察暂停与重启时序。"""
        def __init__(self, *, release_on_close: bool):
            """初始化关闭标记、调用计数以及是否在 close 时放行闸门。"""
            self.closed = False
            self.calls = 0
            self.release_on_close = release_on_close
            self.gate = threading.Event()

        def get_updates(self, cursor, *, timeout_seconds: float = 40.0):
            """等待闸门放行后原样返回游标。"""
            self.calls += 1
            self.gate.wait(10.0)
            return {"ret": 0, "msgs": [], "get_updates_buf": cursor}

        def close(self):
            """标记关闭，并按配置决定是否放行阻塞调用。"""
            self.closed = True
            if self.release_on_close:
                self.gate.set()

    created: list[ControlledPollClient] = []

    def factory(binding):
        """按需创建客户端，关闭后替换为新实例。"""
        if not created or created[-1].closed:
            created.append(ControlledPollClient(release_on_close=bool(created)))
        return created[-1]

    manager = WeChatPollManager(db_engine=engine, client_factory=factory)
    manager.ensure_binding(binding_id)
    assert _wait_for(lambda: len(created) == 1 and created[0].calls == 1)

    manager.pause_binding(binding_id)
    assert manager.wait_binding_stopped(binding_id, timeout_seconds=0.05) is False
    manager.resume_binding(binding_id, start=False)
    manager.reconcile_once()
    assert len(created) == 1

    created[0].gate.set()
    assert manager.wait_binding_stopped(binding_id, timeout_seconds=5.0)
    manager.reconcile_once()
    assert _wait_for(lambda: len(created) == 2 and created[1].calls == 1)
    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert binding.config_json["ilink_bot_id"] == "bot@im.bot"
        assert binding.config_revision == 0
    manager.pause_binding(binding_id)
    assert manager.wait_binding_stopped(binding_id, timeout_seconds=5.0)


def test_cursor_patch_preserves_api_owned_config_fields() -> None:
    """验证游标补丁只改游标，保留 API 维护的配置字段。"""
    engine = _test_engine()
    with Session(engine) as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        binding = ChannelBinding(
            tenant_id="tenant_demo",
            agent_id="agent_1",
            channel="wechat",
            status="active",
            config_json={
                "ilink_bot_id": "bot@im.bot",
                "auto_route": False,
                "get_updates_buf": "old",
            },
            config_revision=7,
        )
        db.add(binding)
        db.commit()
        binding_id = binding.id

    manager = WeChatPollManager(db_engine=engine, client_factory=lambda binding: None)
    assert manager._persist_cursor(binding_id, "new", expected_revision=7)

    with Session(engine) as db:
        binding = db.get(ChannelBinding, binding_id)
        assert binding.config_json["auto_route"] is False
        assert binding.config_json["ilink_bot_id"] == "bot@im.bot"
        assert binding.config_json["get_updates_buf"] == "new"


def test_wechat_adapter_client_id_deterministic_per_dedupe_key() -> None:
    """验证同一幂等键重试得到相同 client_id，未传时保持随机。"""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """记录发送的消息体并返回成功。"""
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = _client(handler)
    adapter = WeChatAdapter(client_factory=lambda binding: client)
    binding = ChannelBinding(tenant_id="t", agent_id="a", channel="wechat", status="active")
    target = {"to_user_id": "u1", "context_token": "ctx"}
    adapter.send(binding, target, "hi", idempotency_key="msg_1")
    adapter.send(binding, target, "hi", idempotency_key="msg_1")
    adapter.send(binding, target, "hi")

    client_ids = [payload["msg"]["client_id"] for payload in sent]
    # 同一投递重试 client_id 一致(带分片下标);不传 idempotency_key 时保持随机
    assert client_ids[0] == "staffdeck:msg_1:0"
    assert client_ids[1] == "staffdeck:msg_1:0"
    assert client_ids[2] != "staffdeck:msg_1:0"


def test_chunk_client_id_carries_chunk_index() -> None:
    """验证长文本分片的 client_id 带分片下标，且重试时保持一致。"""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """记录发送的消息体并返回成功。"""
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={})

    client = _client(handler)
    adapter = WeChatAdapter(client_factory=lambda binding: client)
    binding = ChannelBinding(tenant_id="t", agent_id="a", channel="wechat", status="active")
    target = {"to_user_id": "u1", "context_token": "ctx"}

    # 三分片:client_id 依次带 chunk_index,服务端按 client_id 幂等也不会丢后续分片
    adapter.send(binding, target, "x" * 4500, idempotency_key="msg_1")
    ids = [payload["msg"]["client_id"] for payload in sent]
    assert ids == ["staffdeck:msg_1:0", "staffdeck:msg_1:1", "staffdeck:msg_1:2"]

    # 同一片重试恒等
    sent.clear()
    adapter.send(binding, target, "x" * 4500, idempotency_key="msg_1")
    assert [payload["msg"]["client_id"] for payload in sent] == ids

    # 无 idempotency_key:每片随机且互不相同
    sent.clear()
    adapter.send(binding, target, "x" * 4500)
    random_ids = [payload["msg"]["client_id"] for payload in sent]
    assert len(set(random_ids)) == 3
    assert all(not cid.startswith("staffdeck:msg_1") for cid in random_ids)

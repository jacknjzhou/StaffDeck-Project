from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import threading
import time
from pathlib import PurePath
from typing import Any
from urllib.parse import unquote

import httpx

from app.channels.adapters.base import (
    ChannelInbound,
    ChannelInboundAttachment,
    register_channel_adapter,
)
from app.channels.crypto import decrypt_channel_secret
from app.channels.media import MAX_CHANNEL_MEDIA_BYTES
from app.db.models import ChannelBinding

WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
TOKEN_REFRESH_SKEW_SECONDS = 300
TEXT_LIMIT_BYTES = 2048
WECOM_KF_IMAGE_MAX_BYTES = 2 * 1024 * 1024
WECOM_KF_FILE_MAX_BYTES = 20 * 1024 * 1024


class WeChatKfPermanentError(RuntimeError):
    """不可重试的微信客服错误，例如凭证缺失、无权限或参数非法。"""
    retryable = False


class WeChatKfTransientError(RuntimeError):
    """可重试的微信客服错误，如网络失败、token 失效或下游限流。"""


def wechat_kf_credentials(binding: ChannelBinding) -> dict[str, str]:
    """解密并返回该绑定的微信客服凭证；缺失或格式非法时抛永久错误。"""
    if not binding.credentials_enc:
        raise WeChatKfPermanentError("微信客服凭证未配置")
    try:
        credentials = json.loads(decrypt_channel_secret(binding.credentials_enc))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise WeChatKfPermanentError("微信客服凭证无效") from exc
    if not isinstance(credentials, dict):
        raise WeChatKfPermanentError("微信客服凭证无效")
    return {str(key): str(value) for key, value in credentials.items()}


class WeChatKfTokenProvider:
    """按 binding 与配置版本缓存微信客服 access_token，提前 5 分钟过期以避开临界失效。

    缓存键为 (binding.id, config_revision)，配置变更后旧 token 自然不再命中。
    """

    def __init__(self, client_factory=httpx.Client) -> None:
        """保存 HTTP 客户端工厂并初始化 token 缓存与互斥锁。"""
        self._client_factory = client_factory
        self._cache: dict[tuple[str, int], tuple[str, float]] = {}
        self._lock = threading.Lock()

    def invalidate(self, binding: ChannelBinding) -> None:
        """清掉该绑定的缓存 token，供接口返回 token 失效后立即换新。"""
        with self._lock:
            self._cache.pop((binding.id, binding.config_revision), None)

    def get(self, binding: ChannelBinding) -> str:
        """取可用 access_token：命中未过期缓存则直接复用，否则调用 gettoken 刷新并缓存。"""
        key = (binding.id, binding.config_revision)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[1] > now:
                return cached[0]
        config = dict(binding.config_json or {})
        credentials = wechat_kf_credentials(binding)
        corp_id = str(config.get("corp_id") or "").strip()
        secret = credentials.get("secret", "").strip()
        if not corp_id or not secret:
            raise WeChatKfPermanentError("微信客服企业 ID 或 Secret 缺失")
        try:
            with self._client_factory(timeout=15.0) as client:
                response = client.get(
                    f"{WECOM_API_BASE}/gettoken",
                    params={"corpid": corp_id, "corpsecret": secret},
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeChatKfTransientError("获取微信客服 access_token 失败") from exc
        if int(data.get("errcode") or 0) != 0:
            raise WeChatKfPermanentError(
                f"获取微信客服 access_token 失败: {data.get('errmsg') or data.get('errcode')}"
            )
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise WeChatKfTransientError("微信客服 access_token 响应为空")
        expires_in = max(0, int(data.get("expires_in") or 7200) - TOKEN_REFRESH_SKEW_SECONDS)
        with self._lock:
            self._cache[key] = (token, now + expires_in)
        return token


def normalize_wechat_kf_message(
    raw: dict[str, Any], *, account_scope: str = ""
) -> ChannelInbound | None:
    """把微信客服回调帧归一化成 ChannelInbound；非客户消息或必填字段缺失时返回 None。

    只处理 origin=3（客户发送）的 text/image/file/mixed；mixed 会拼接文本并收集附件。
    """
    if not isinstance(raw, dict):
        return None
    try:
        if int(raw.get("origin") or 0) != 3:
            return None
    except (TypeError, ValueError):
        return None
    msg_id = str(raw.get("msgid") or "").strip()
    external_userid = str(raw.get("external_userid") or "").strip()
    open_kfid = str(raw.get("open_kfid") or "").strip()
    msg_type = str(raw.get("msgtype") or "").strip()
    if not msg_id or not external_userid or not open_kfid:
        return None
    text = ""
    attachments: list[ChannelInboundAttachment] = []
    if msg_type == "text":
        text_payload = raw.get("text")
        if not isinstance(text_payload, dict):
            return None
        text = str(text_payload.get("content") or "").strip()
    elif msg_type in {"image", "file"}:
        attachments = _wechat_kf_attachments(raw, msg_id, msg_type)
    elif msg_type == "mixed":
        mixed = raw.get("mixed")
        if not isinstance(mixed, dict):
            return None
        items = mixed.get("msg_item")
        if not isinstance(items, list):
            return None
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("msgtype") or "").strip()
            if item_type == "text":
                text_payload = item.get("text")
                if not isinstance(text_payload, dict):
                    continue
                value = str(text_payload.get("content") or "").strip()
                if value:
                    text = f"{text}\n{value}".strip() if text else value
            elif item_type in {"image", "file"}:
                attachments.extend(_wechat_kf_attachments(item, msg_id, item_type))
    if not text and not attachments:
        return None
    return ChannelInbound(
        channel="wechat_kf",
        event_id=msg_id,
        from_user_id=external_userid,
        to_user_id=open_kfid,
        session_id=external_userid,
        group_id="",
        context_token=open_kfid,
        text=text,
        is_group=False,
        raw=raw,
        account_scope=account_scope,
        attachments=attachments,
    )


def _wechat_kf_attachments(
    raw: dict[str, Any], message_id: str, msg_type: str
) -> list[ChannelInboundAttachment]:
    """从 image/file 消息体里取出 media_id，组装带下载参数与大小上限的附件描述。"""
    info = raw.get(msg_type) or {}
    if not isinstance(info, dict):
        return []
    media_id = str(info.get("media_id") or info.get("file_id") or "").strip()
    if not media_id:
        return []
    if msg_type == "image":
        return [
            ChannelInboundAttachment(
                media_id=media_id,
                kind="image",
                filename=f"{message_id}.jpg",
                content_type="image/jpeg",
                download_params={
                    "media_id": media_id,
                    "provider_max_bytes": WECOM_KF_IMAGE_MAX_BYTES,
                },
            )
        ]
    filename = str(
        info.get("filename")
        or info.get("file_name")
        or info.get("name")
        or f"{message_id}.bin"
    ).strip()
    content_type = str(info.get("content_type") or info.get("mime_type") or "").strip()
    content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return [
        ChannelInboundAttachment(
            media_id=media_id,
            kind="file",
            filename=filename,
            content_type=content_type,
            download_params={
                "media_id": media_id,
                "provider_max_bytes": WECOM_KF_FILE_MAX_BYTES,
            },
        )
    ]


def _split_utf8_text(text: str, limit: int = TEXT_LIMIT_BYTES) -> list[str]:
    """按 UTF-8 字节数把长文本切成多段，单段不超过 limit，且不切断多字节字符。"""
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for char in text:
        char_bytes = len(char.encode())
        if current and current_bytes + char_bytes > limit:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(char)
        current_bytes += char_bytes
    if current:
        chunks.append("".join(current))
    return chunks


def _filename_from_content_disposition(value: str) -> str:
    """从 Content-Disposition 头解析并规整文件名，只保留末段并截断到 255 字符。"""
    if not value:
        return ""
    match = re.search(
        r"filename\*=UTF-8''([^;]+)|filename=\"?([^;\"]+)",
        value,
        re.IGNORECASE,
    )
    if not match:
        return ""
    filename = unquote((match.group(1) or match.group(2) or "").strip()).strip()
    return PurePath(filename).name[:255]


class WeChatKfAdapter:
    """微信客服渠道适配器：负责消息归一化、media 下载、账号管理与文本消息投递。"""

    def __init__(self, token_provider: WeChatKfTokenProvider | None = None) -> None:
        """允许注入 token provider 以便测试，默认使用进程内缓存实现。"""
        self._tokens = token_provider or WeChatKfTokenProvider()

    def normalize(self, raw: dict[str, Any]) -> ChannelInbound | None:
        """把原始回调帧转成统一入站消息，无法识别时返回 None。"""
        return normalize_wechat_kf_message(raw)

    def _post(self, binding: ChannelBinding, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """带 access_token 调用微信客服接口并校验 errcode，按错误类型抛出可重试/永久错误。"""
        token = self._tokens.get(binding)
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(
                    f"{WECOM_API_BASE}{path}",
                    params={"access_token": token},
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeChatKfTransientError("微信客服接口请求失败") from exc
        errcode = int(data.get("errcode") or 0)
        if errcode in {40014, 42001}:
            self._tokens.invalidate(binding)
            raise WeChatKfTransientError("微信客服 access_token 已失效")
        if errcode != 0:
            error = WeChatKfPermanentError(
                f"微信客服接口失败: {data.get('errmsg') or errcode}"
            )
            if errcode in {45009, 45011}:
                error.retryable = True
            raise error
        return data

    def download_media(
        self,
        binding: ChannelBinding,
        attachment: ChannelInboundAttachment,
        *,
        max_bytes: int = 0,
    ) -> bytes:
        """经微信客服 media/get 下载客户图片或文件，返回字节；超限或返回 JSON 错误时抛异常。

        实际大小上限取调用方 max_bytes 与渠道上限的较小值；Content-Disposition 与
        Content-Type 会回写到 attachment 上，供后续识别文件名与类型。
        """
        media_id = str(
            attachment.download_params.get("media_id") or attachment.media_id
        ).strip()
        if not media_id:
            raise WeChatKfPermanentError("微信客服附件缺少 media_id")
        provider_limit = int(
            attachment.download_params.get("provider_max_bytes") or MAX_CHANNEL_MEDIA_BYTES
        )
        limit = min(max_bytes or MAX_CHANNEL_MEDIA_BYTES, provider_limit)
        token = self._tokens.get(binding)
        try:
            with httpx.Client(timeout=20.0) as client, client.stream(
                "GET",
                f"{WECOM_API_BASE}/media/get",
                params={"access_token": token, "media_id": media_id},
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                content_disposition = response.headers.get("content-disposition", "")
                response_filename = _filename_from_content_disposition(content_disposition)
                if response_filename:
                    attachment.filename = response_filename
                if content_type and "application/octet-stream" not in content_type.lower():
                    attachment.content_type = content_type.split(";", 1)[0].strip()
                elif attachment.filename:
                    attachment.content_type = (
                        mimetypes.guess_type(attachment.filename)[0]
                        or attachment.content_type
                    )
                content_length = int(response.headers.get("content-length") or 0)
                if content_length > limit:
                    raise ValueError(f"微信客服附件超过大小上限: size>{limit}")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError(f"微信客服附件超过大小上限: size>{limit}")
                    chunks.append(chunk)
                data = b"".join(chunks)
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, ValueError) and "超过大小上限" in str(exc):
                raise
            raise WeChatKfTransientError("下载微信客服附件失败") from exc
        if "application/json" in content_type.lower():
            try:
                payload = json.loads(data)
            except json.JSONDecodeError as exc:
                raise WeChatKfTransientError("微信客服附件下载响应无效") from exc
            errcode = int(payload.get("errcode") or 0)
            if errcode in {40014, 42001}:
                self._tokens.invalidate(binding)
                raise WeChatKfTransientError("微信客服 access_token 已失效")
            raise WeChatKfPermanentError(
                f"下载微信客服附件失败: {payload.get('errmsg') or errcode}"
            )
        if not data:
            raise WeChatKfTransientError("微信客服附件下载内容为空")
        return data

    def sync_messages(
        self,
        binding: ChannelBinding,
        *,
        callback_token: str,
        cursor: str,
        open_kfid: str = "",
    ) -> dict[str, Any]:
        """用微信客服 sync_msg 拉取增量消息，返回含 next_cursor 与 msg_list 的原始响应。"""
        config = dict(binding.config_json or {})
        body: dict[str, Any] = {
            "open_kfid": open_kfid or str(config.get("open_kfid") or ""),
            "token": callback_token,
            "limit": 1000,
            "voice_format": 0,
        }
        if cursor:
            body["cursor"] = cursor
        return self._post(binding, "/kf/sync_msg", body)

    def validate_account(self, binding: ChannelBinding) -> None:
        """校验配置的 open_kfid 在账号列表里且具备管理权限，否则抛永久错误。"""
        config = dict(binding.config_json or {})
        open_kfid = str(config.get("open_kfid") or "").strip()
        data = self._post(binding, "/kf/account/list", {"offset": 0, "limit": 100})
        accounts = data.get("account_list") or []
        if not any(
            str(account.get("open_kfid") or "").strip() == open_kfid
            and account.get("manage_privilege") is not False
            for account in accounts
            if isinstance(account, dict)
        ):
            raise WeChatKfPermanentError("应用无权管理该微信客服账号")

    def list_accounts(self, binding: ChannelBinding) -> list[dict[str, Any]]:
        """翻页拉取全部微信客服账号；超过 50 页视为异常并抛可重试错误。"""
        accounts: list[dict[str, Any]] = []
        offset = 0
        limit = 100
        for _ in range(50):
            data = self._post(binding, "/kf/account/list", {"offset": offset, "limit": limit})
            page = [item for item in data.get("account_list") or [] if isinstance(item, dict)]
            accounts.extend(page)
            if len(page) < limit:
                return accounts
            offset += limit
        raise WeChatKfTransientError("微信客服账号列表分页超过安全上限")

    def create_account(self, binding: ChannelBinding, name: str) -> str:
        """创建客服账号需要头像 media_id，此简化入口一律拒绝并提示改用带头像接口。"""
        raise WeChatKfPermanentError("创建微信客服账号需要头像 media_id")

    def create_account_with_avatar(
        self, binding: ChannelBinding, name: str, media_id: str
    ) -> str:
        """用名称与头像 media_id 创建客服账号，返回新的 open_kfid。

        名称限 16 字符以内；响应缺少 open_kfid 时按可重试错误处理。
        """
        name = name.strip()
        media_id = media_id.strip()
        if not name or len(name) > 16 or not media_id:
            raise WeChatKfPermanentError("微信客服账号名称不能为空且不能超过 16 个字符")
        data = self._post(binding, "/kf/account/add", {"name": name, "media_id": media_id})
        open_kfid = str(data.get("open_kfid") or "").strip()
        if not open_kfid:
            raise WeChatKfTransientError("微信客服创建账号响应缺少 open_kfid")
        return open_kfid

    def delete_account(self, binding: ChannelBinding, open_kfid: str) -> None:
        """删除指定的微信客服账号；open_kfid 为空时直接拒绝。"""
        open_kfid = open_kfid.strip()
        if not open_kfid:
            raise WeChatKfPermanentError("客服账号 ID 不能为空")
        self._post(binding, "/kf/account/del", {"open_kfid": open_kfid})

    def update_account(
        self,
        binding: ChannelBinding,
        open_kfid: str,
        name: str,
        media_id: str | None = None,
    ) -> None:
        """更新客服账号名称，可同时替换头像 media_id；名称为空或超长时拒绝。"""
        open_kfid = open_kfid.strip()
        name = name.strip()
        if not open_kfid or not name or len(name) > 16:
            raise WeChatKfPermanentError("客服账号 ID 和名称不能为空，名称不能超过 16 个字符")
        body: dict[str, str] = {"open_kfid": open_kfid, "name": name}
        if media_id:
            body["media_id"] = media_id.strip()
        self._post(binding, "/kf/account/update", body)

    def upload_avatar(
        self,
        binding: ChannelBinding,
        data: bytes,
        filename: str,
        content_type: str = "image/jpeg",
    ) -> str:
        """上传头像图片到微信 media 接口，返回可复用的 media_id。"""
        token = self._tokens.get(binding)
        try:
            with httpx.Client(timeout=15.0) as client:
                response = client.post(
                    f"{WECOM_API_BASE}/media/upload",
                    params={"access_token": token, "type": "image"},
                    files={"media": (filename, data, content_type)},
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WeChatKfTransientError("上传微信客服头像失败") from exc
        if int(payload.get("errcode") or 0) != 0:
            raise WeChatKfPermanentError(
                f"上传微信客服头像失败: {payload.get('errmsg') or payload.get('errcode')}"
            )
        media_id = str(payload.get("media_id") or "").strip()
        if not media_id:
            raise WeChatKfTransientError("微信客服头像上传响应缺少 media_id")
        return media_id

    def contact_way(self, binding: ChannelBinding, *, open_kfid: str, scene: str = "staffdeck") -> str:
        """生成客户可扫码/点击的微信客服咨询链接，响应为空时按可重试错误处理。"""
        open_kfid = open_kfid.strip()
        data = self._post(
            binding,
            "/kf/add_contact_way",
            {"open_kfid": open_kfid, "scene": scene},
        )
        url = str(data.get("url") or "").strip()
        if not url:
            raise WeChatKfTransientError("微信客服咨询链接响应为空")
        return url

    def send(
        self,
        binding: ChannelBinding,
        target: dict[str, Any],
        text: str,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        """发送文本消息：按 UTF-8 字节上限分段，idempotency_key 派生出各段的 msgid 以去重。"""
        to_user = str(target.get("to_user_id") or "").strip()
        open_kfid = str(target.get("open_kfid") or "").strip()
        if not to_user or not open_kfid:
            raise WeChatKfPermanentError("微信客服投递目标无效")
        key = hashlib.sha256(str(idempotency_key or "").encode()).hexdigest()[:32]
        for index, chunk in enumerate(_split_utf8_text(text)):
            msg_id = key
            if index:
                msg_id = hashlib.sha256(f"{key}:{index}".encode()).hexdigest()[:32]
            self._post(
                binding,
                "/kf/send_msg",
                {
                    "touser": to_user,
                    "open_kfid": open_kfid,
                    "msgid": msg_id,
                    "msgtype": "text",
                    "text": {"content": chunk},
                },
            )

    def start_ingress(self, binding_id: str) -> None:
        """微信客服走回调推送，无需主动起拉取线程，故为空实现。"""
        return

    def stop_ingress(self, binding_id: str) -> None:
        """与 start_ingress 对称：没有后台线程需要停止，故为空实现。"""
        return


register_channel_adapter("wechat_kf", WeChatKfAdapter())

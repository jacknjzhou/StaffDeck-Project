from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import base64
import binascii
import json
import re
from types import SimpleNamespace
from threading import Event
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx


_DATA_URL = re.compile(r"^data:(image/(?:jpeg|png|gif|webp));base64,(.+)$", re.DOTALL)
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_IMAGE_COUNT = 6
_MAX_TOTAL_IMAGE_BYTES = 18 * 1024 * 1024
_MAX_REQUEST_BYTES = 25 * 1024 * 1024


class ProtocolCallError(Exception):
    """模型协议调用失败时抛出的统一异常，携带可重试标记与上游错误上下文。"""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        provider_code: str | None = None,
        provider_message: str | None = None,
        upstream_body: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """保存错误码与可重试标记，并记录上游状态码、错误码、消息、响应体与 request id。"""
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.provider_code = provider_code
        self.provider_message = provider_message
        self.upstream_body = upstream_body
        self.request_id = request_id


class CancellationToken:
    """跨线程取消信号，供调用方在流式输出过程中中断请求。"""

    def __init__(self) -> None:
        """创建一个尚未触发的取消事件。"""
        self._event = Event()

    def cancel(self) -> None:
        """置位取消事件，此后 cancelled 返回 True。"""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """取消是否已触发，驱动在每个 chunk 前检查它。"""
        return self._event.is_set()


class ProtocolDriver(Protocol):
    """模型协议驱动的结构约定：请求类型、可观测请求体、非流式与流式调用。"""

    request_kind: str

    def observable_request(
        self,
        request: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """返回实际发往上游的请求体（剔除内部下划线字段），供日志与观测使用。"""

    def complete(self, request: dict[str, Any]) -> Any:
        """发起一次非流式调用，返回统一结构的补全结果。"""

    def stream(self, request: dict[str, Any]) -> Iterator[Any]:
        """发起流式调用，返回逐步产出增量 chunk 的迭代器。"""


@dataclass(frozen=True)
class ChatCompletionsDriver:
    """OpenAI Chat Completions 协议驱动，把内部请求直接转给 chat.completions。"""

    client: Any
    request_kind: str = "chat.completions"

    def observable_request(
        self,
        request: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """返回 chat.completions 的线上请求体；stream 为真时补上 stream 字段。"""
        payload = _wire_request(request)
        if stream:
            payload["stream"] = True
        return payload

    def complete(self, request: dict[str, Any]) -> Any:
        """调用 chat.completions 完成一次非流式请求，已取消时抛 MODEL_CANCELLED。"""
        _raise_if_cancelled(request)
        return self.client.chat.completions.create(**_wire_request(request))

    def stream(self, request: dict[str, Any]) -> Iterator[Any]:
        """以 stream=True 建立流式调用，返回逐 chunk 产出并在结束时关闭底层流的迭代器。"""
        _raise_if_cancelled(request)
        stream = self.client.chat.completions.create(**_wire_request(request), stream=True)

        def iterate() -> Iterator[Any]:
            """迭代上游 chunk，每个 chunk 前检查取消，结束时确保关闭底层流。"""
            try:
                for chunk in stream:
                    _raise_if_cancelled(request)
                    yield chunk
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()

        return iterate()


@dataclass(frozen=True)
class OpenAIResponsesDriver:
    """OpenAI Responses 协议驱动，把内部消息转换为 responses 的 input 结构。"""

    client: Any
    request_kind: str = "responses"

    def observable_request(
        self,
        request: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """返回 responses 接口的线上请求体；stream 为真时补上 stream 字段。"""
        payload = _responses_request(request)
        if stream:
            payload["stream"] = True
        return payload

    def complete(self, request: dict[str, Any]) -> Any:
        """调用 responses 接口，并把响应折叠成与 chat.completions 一致的补全结构。

        上游异常统一转换为 ProtocolCallError。
        """
        _raise_if_cancelled(request)
        try:
            response = self.client.responses.create(**_responses_request(request))
        except ProtocolCallError:
            raise
        except Exception as exc:
            raise _protocol_call_error(exc) from exc
        return _responses_completion(response)

    def stream(self, request: dict[str, Any]) -> Iterator[Any]:
        """把 responses 事件流转换为增量 chunk；失败事件抛可重试的 MODEL_UPSTREAM_ERROR。"""
        _raise_if_cancelled(request)
        try:
            events = self.client.responses.create(
                **_responses_request(request),
                stream=True,
            )
        except Exception as exc:
            raise _protocol_call_error(exc) from exc
        response_id = None
        try:
            for event in events:
                _raise_if_cancelled(request)
                event_type = _object_value(event, "type")
                if event_type == "response.created":
                    response_id = _object_value(_object_value(event, "response"), "id")
                    yield _stream_chunk(response_id, provider_event=event)
                    continue
                if event_type == "response.output_text.delta":
                    yield _stream_chunk(
                        response_id,
                        text=str(_object_value(event, "delta") or ""),
                        provider_event=event,
                    )
                    continue
                if event_type in {"response.completed", "response.incomplete"}:
                    response = _object_value(event, "response")
                    response_id = _object_value(response, "id") or response_id
                    yield _stream_chunk(
                        response_id,
                        finish_reason=_responses_finish_reason(response),
                        usage=_responses_usage(_object_value(response, "usage")),
                        provider_event=event,
                    )
                    continue
                if event_type in {"response.failed", "error"}:
                    error = _object_value(event, "error") or _object_value(
                        _object_value(event, "response"), "error"
                    )
                    provider_code, provider_message = _provider_error_fields(error)
                    raise ProtocolCallError(
                        "MODEL_UPSTREAM_ERROR",
                        retryable=True,
                        provider_code=provider_code,
                        provider_message=provider_message,
                        upstream_body=_safe_upstream_body(error),
                    )
        except ProtocolCallError:
            raise
        except Exception as exc:
            raise _protocol_call_error(exc) from exc
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()


@dataclass(frozen=True)
class AnthropicMessagesDriver:
    """Anthropic Messages 协议驱动，负责系统提示、图片与流式事件的转换。"""

    client: Any
    request_kind: str = "anthropic.messages"

    def observable_request(
        self,
        request: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """返回 messages 接口的线上请求体；stream 为真时补上 stream 字段。"""
        payload = _anthropic_request(request)
        if stream:
            payload["stream"] = True
        return payload

    def complete(self, request: dict[str, Any]) -> Any:
        """调用 messages 接口，把文本块拼成统一补全结构；已取消时抛 MODEL_CANCELLED。"""
        _raise_if_cancelled(request)
        payload = _anthropic_request(request)
        try:
            response = self.client.messages.create(**payload, stream=False)
        except ProtocolCallError:
            raise
        except Exception as exc:
            raise _protocol_call_error(exc) from exc
        text = "".join(
            str(getattr(block, "text", ""))
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", None) == "text"
        )
        usage = getattr(response, "usage", None)
        return SimpleNamespace(
            id=getattr(response, "id", None),
            provider_response=response,
            usage=_anthropic_usage(usage),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text),
                    finish_reason=getattr(response, "stop_reason", None),
                )
            ],
        )

    def stream(self, request: dict[str, Any]) -> Iterator[Any]:
        """把 message_start、content_block_delta、message_delta 事件转换为增量 chunk。"""
        payload = _anthropic_request(request)
        try:
            events = self.client.messages.create(**payload, stream=True)
        except Exception as exc:
            raise _protocol_call_error(exc) from exc
        try:
            response_id = None
            for event in events:
                _raise_if_cancelled(request)
                event_type = getattr(event, "type", None)
                if event_type == "message_start":
                    message = getattr(event, "message", None)
                    response_id = getattr(message, "id", None)
                    yield _stream_chunk(
                        response_id,
                        usage=_anthropic_usage(getattr(message, "usage", None)),
                        provider_event=event,
                    )
                    continue
                if event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if getattr(delta, "type", None) == "text_delta":
                        yield _stream_chunk(
                            response_id,
                            text=str(getattr(delta, "text", "")),
                            provider_event=event,
                        )
                    continue
                if event_type == "message_delta":
                    delta = getattr(event, "delta", None)
                    yield _stream_chunk(
                        response_id,
                        finish_reason=getattr(delta, "stop_reason", None),
                        usage=_anthropic_usage(getattr(event, "usage", None)),
                        provider_event=event,
                    )
        except ProtocolCallError:
            raise
        except Exception as exc:
            raise _protocol_call_error(exc) from exc
        finally:
            close = getattr(events, "close", None)
            if callable(close):
                close()


@dataclass(frozen=True)
class GeminiGenerateContentDriver:
    """Gemini generateContent 协议驱动，直接用 httpx 访问 REST 接口。"""

    client: httpx.Client
    base_url: str
    api_key: str
    model: str
    request_kind: str = "gemini.generate_content"

    def observable_request(
        self,
        request: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """返回 Gemini 线上请求体（contents 加 generationConfig）。"""
        return _gemini_request(request)

    def complete(self, request: dict[str, Any]) -> Any:
        """调用 generateContent 并解析 JSON；HTTP 或解析失败转成 ProtocolCallError。"""
        _raise_if_cancelled(request)
        payload = _gemini_request(request)
        try:
            response = self.client.post(
                _gemini_endpoint(self.base_url, self.model, "generateContent"),
                headers=_gemini_headers(self.api_key),
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise _protocol_call_error(exc) from exc
        _raise_for_gemini_response(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProtocolCallError("MODEL_INVALID_PROVIDER_RESPONSE") from exc
        return _gemini_completion(data)

    def stream(self, request: dict[str, Any]) -> Iterator[Any]:
        """用 streamGenerateContent 配合 alt=sse 拉流，逐行解析并产出统一 chunk。"""
        _raise_if_cancelled(request)
        payload = _gemini_request(request)
        try:
            with self.client.stream(
                "POST",
                _gemini_endpoint(self.base_url, self.model, "streamGenerateContent", stream=True),
                headers=_gemini_headers(self.api_key),
                json=payload,
            ) as response:
                _raise_for_gemini_response(response)
                for line in response.iter_lines():
                    _raise_if_cancelled(request)
                    if not line:
                        continue
                    raw = line[5:].strip() if line.startswith("data:") else line.strip()
                    if raw == "[DONE]":
                        continue
                    try:
                        data = json.loads(raw)
                    except ValueError as exc:
                        raise ProtocolCallError("MODEL_INVALID_PROVIDER_RESPONSE") from exc
                    yield _gemini_completion(data)
        except ProtocolCallError:
            raise
        except httpx.HTTPError as exc:
            raise _protocol_call_error(exc) from exc


def _responses_request(request: dict[str, Any]) -> dict[str, Any]:
    """把内部消息转换为 Responses 的 input 结构。

    非 system/developer/user/assistant 的角色直接丢弃；图片数量或总字节超限抛 ValueError。
    """
    input_items: list[dict[str, Any]] = []
    image_count = 0
    total_image_bytes = 0
    for message in request.get("messages") or []:
        role = str(message.get("role") or "")
        if role not in {"system", "developer", "user", "assistant"}:
            continue
        content, content_image_count, content_image_bytes = _responses_content(
            message.get("content"), role
        )
        if not content:
            continue
        image_count += content_image_count
        total_image_bytes += content_image_bytes
        input_items.append({"role": role, "content": content})
    if image_count > _MAX_IMAGE_COUNT:
        raise ValueError("MODEL_TOO_MANY_IMAGES")
    if total_image_bytes > _MAX_TOTAL_IMAGE_BYTES:
        raise ValueError("MODEL_REQUEST_TOO_LARGE")
    payload: dict[str, Any] = {
        "model": request["model"],
        "input": input_items,
        "temperature": request["temperature"],
        "max_output_tokens": request["max_tokens"],
        "store": False,
    }
    response_format = request.get("response_format")
    if response_format and response_format.get("type") == "json_object":
        payload["text"] = {"format": {"type": "json_object"}}
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("MODEL_REQUEST_TOO_LARGE")
    return payload


def _responses_content(value: Any, role: str) -> tuple[Any, int, int]:
    """转换单条消息内容：文本转 input_text，data URL 图片转 input_image。

    返回内容、图片数与图片字节数；非 user 角色的图片会被忽略。
    """
    if isinstance(value, str):
        return value, 0, 0
    if not isinstance(value, list):
        return "", 0, 0
    parts: list[dict[str, Any]] = []
    image_count = 0
    total_image_bytes = 0
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text") or "")
            if text:
                parts.append({"type": "input_text", "text": text})
            continue
        if item.get("type") != "image_url" or role != "user":
            continue
        image = item.get("image_url")
        url = str(image.get("url") or "") if isinstance(image, dict) else ""
        if not url:
            continue
        match = _DATA_URL.fullmatch(url)
        if match:
            try:
                decoded = base64.b64decode(match.group(2), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("MODEL_IMAGE_DATA_URL_INVALID") from exc
            if len(decoded) > _MAX_IMAGE_BYTES:
                raise ValueError("MODEL_IMAGE_TOO_LARGE")
            image_count += 1
            total_image_bytes += len(decoded)
        parts.append({"type": "input_image", "image_url": url})
    return parts, image_count, total_image_bytes


def _responses_completion(response: Any) -> Any:
    """把 Responses 响应里的 output_text 拼成 chat.completions 风格的补全对象。"""
    text_parts: list[str] = []
    for item in _object_value(response, "output") or []:
        if _object_value(item, "type") != "message":
            continue
        for part in _object_value(item, "content") or []:
            if _object_value(part, "type") == "output_text":
                text_parts.append(str(_object_value(part, "text") or ""))
    text = "".join(text_parts)
    return SimpleNamespace(
        id=_object_value(response, "id"),
        provider_response=response,
        usage=_responses_usage(_object_value(response, "usage")),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
                finish_reason=_responses_finish_reason(response),
            )
        ],
    )


def _responses_finish_reason(response: Any) -> str | None:
    """把 Responses 的 status 映射为 finish_reason，incomplete 时按具体原因细分。"""
    status = _object_value(response, "status")
    if status == "completed":
        return "stop"
    if status == "incomplete":
        details = _object_value(response, "incomplete_details")
        reason = _object_value(details, "reason")
        return "length" if reason == "max_output_tokens" else str(reason or "incomplete")
    return str(status) if status else None


def _responses_usage(value: Any) -> Any:
    """把 Responses 的 usage 映射为 prompt/completion/total_tokens 结构；缺省返回 None。"""
    if value is None:
        return None
    return SimpleNamespace(
        prompt_tokens=_object_value(value, "input_tokens"),
        completion_tokens=_object_value(value, "output_tokens"),
        total_tokens=_object_value(value, "total_tokens"),
        input_tokens_details=_object_value(value, "input_tokens_details"),
    )


def _object_value(value: Any, name: str) -> Any:
    """读取字段值，同时兼容 dict 与对象属性两种形态。"""
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _gemini_headers(api_key: str) -> dict[str, str]:
    """构造 Gemini REST 请求头，同时带上 Bearer 与 x-goog-api-key。"""
    return {
        "content-type": "application/json",
        "authorization": f"Bearer {api_key}",
        "x-goog-api-key": api_key,
        "accept": "text/event-stream, application/json",
    }


def _gemini_endpoint(
    base_url: str, model: str, method: str, *, stream: bool = False
) -> str:
    """拼出 Gemini 模型方法地址（必要时补 /v1beta 前缀），流式时追加 alt=sse。"""
    parsed = urlsplit(base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if not path.endswith("/v1beta"):
        path = f"{path}/v1beta"
    path = f"{path}/models/{quote(model, safe='')}:{method}"
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if stream and ("alt", "sse") not in query:
        query.append(("alt", "sse"))
    return urlunsplit((parsed.scheme, parsed.netloc, path, urlencode(query), ""))


def _gemini_request(request: dict[str, Any]) -> dict[str, Any]:
    """把内部消息转换为 Gemini 的 contents 与 generationConfig 请求体。

    system 消息合并进 systemInstruction，连续同角色消息合并；图片或体积超限抛 ValueError。
    """
    contents: list[dict[str, Any]] = []
    system_parts: list[dict[str, Any]] = []
    for message in request.get("messages") or []:
        role = str(message.get("role") or "")
        parts = _gemini_content_parts(message.get("content"), role)
        if not parts:
            continue
        if role == "system":
            system_parts.extend(parts)
            continue
        gemini_role = "model" if role == "assistant" else "user"
        if contents and contents[-1]["role"] == gemini_role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": gemini_role, "parts": parts})
    if contents and contents[0]["role"] == "model":
        contents.pop(0)
    payload: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {
            "temperature": request["temperature"],
            "maxOutputTokens": request["max_tokens"],
        },
    }
    if system_parts:
        payload["systemInstruction"] = {"parts": system_parts}
    response_format = request.get("response_format")
    if response_format and response_format.get("type") == "json_object":
        payload["generationConfig"]["responseMimeType"] = "application/json"
    image_parts = [
        part
        for item in contents
        for part in item["parts"]
        if isinstance(part, dict) and "inlineData" in part
    ]
    if len(image_parts) > _MAX_IMAGE_COUNT:
        raise ValueError("MODEL_TOO_MANY_IMAGES")
    total_image_bytes = sum(
        len(base64.b64decode(part["inlineData"]["data"], validate=True))
        for part in image_parts
    )
    if total_image_bytes > _MAX_TOTAL_IMAGE_BYTES:
        raise ValueError("MODEL_REQUEST_TOO_LARGE")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("MODEL_REQUEST_TOO_LARGE")
    return payload


def _gemini_content_parts(value: Any, role: str) -> list[dict[str, Any]]:
    """把单条消息内容转成 Gemini parts：文本转 text，data URL 图片转 inlineData。

    非 data URL 图片或超限图片抛 ValueError。
    """
    if isinstance(value, str):
        return [{"text": value}] if value else []
    if not isinstance(value, list):
        return []
    parts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text") or "")
            if text:
                parts.append({"text": text})
            continue
        if item.get("type") != "image_url" or role != "user":
            continue
        image = item.get("image_url")
        url = str(image.get("url") or "") if isinstance(image, dict) else ""
        match = _DATA_URL.fullmatch(url)
        if not match:
            raise ValueError("MODEL_IMAGE_DATA_URL_INVALID")
        try:
            decoded = base64.b64decode(match.group(2), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("MODEL_IMAGE_DATA_URL_INVALID") from exc
        if len(decoded) > _MAX_IMAGE_BYTES:
            raise ValueError("MODEL_IMAGE_TOO_LARGE")
        parts.append(
            {
                "inlineData": {
                    "mimeType": match.group(1),
                    "data": match.group(2),
                }
            }
        )
    if sum(1 for part in parts if "inlineData" in part) > _MAX_IMAGE_COUNT:
        raise ValueError("MODEL_TOO_MANY_IMAGES")
    return parts


def _raise_for_gemini_response(response: httpx.Response) -> None:
    """状态码不小于 400 时按分类抛 ProtocolCallError，并附上脱敏后的上游信息。"""
    if response.status_code < 400:
        return
    status = response.status_code
    code, retryable = _model_error_classification(status=status)
    try:
        body: Any = response.json()
    except (ValueError, json.JSONDecodeError):
        body = response.text
    provider_code, provider_message = _provider_error_fields(body)
    raise ProtocolCallError(
        code,
        retryable=retryable,
        status_code=status,
        provider_code=provider_code,
        provider_message=provider_message,
        upstream_body=_safe_upstream_body(body),
        request_id=response.headers.get("x-request-id") or response.headers.get("request-id"),
    )


def _gemini_completion(data: dict[str, Any]) -> Any:
    """把 Gemini 单个响应（含流式分片）转成统一补全对象，跳过 thought 片段。"""
    candidates = data.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    content = candidate.get("content") or {}
    text = "".join(
        str(part.get("text") or "")
        for part in content.get("parts") or []
        if isinstance(part, dict) and not part.get("thought")
    )
    usage = data.get("usageMetadata") or {}
    prompt_tokens = usage.get("promptTokenCount")
    output_tokens = usage.get("candidatesTokenCount")
    return SimpleNamespace(
        id=data.get("responseId"),
        provider_response=data,
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=output_tokens,
            total_tokens=usage.get("totalTokenCount"),
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text),
                finish_reason=candidate.get("finishReason"),
                delta=SimpleNamespace(content=text),
            )
        ]
        if text or candidate.get("finishReason")
        else [],
    )


def _anthropic_request(request: dict[str, Any]) -> dict[str, Any]:
    """把内部消息转换为 Anthropic messages 请求体。

    system 消息合并为 system 字段、连续同角色消息合并；Claude 5 部署不接受 temperature，构造时按需省略。
    """
    messages = list(request.get("messages") or [])
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system_parts.append(_content_text(message.get("content")))
            continue
        if role not in {"user", "assistant"}:
            continue
        blocks = _anthropic_content(message.get("content"), role)
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    if converted and converted[0]["role"] == "assistant":
        converted.pop(0)
    payload = {
        "model": request["model"],
        "messages": converted,
        "max_tokens": request["max_tokens"],
    }
    # LLM Center's Claude 5 deployments reject the legacy sampling field.
    # Keep temperature for older Anthropic-compatible deployments.
    temperature = request.get("temperature")
    model = str(request.get("model") or "")
    if temperature is not None and not re.match(r"^claude-(?:sonnet|opus)-5(?:$|[-:])", model):
        payload["temperature"] = temperature
    system = "\n\n".join(part for part in system_parts if part)
    if system:
        payload["system"] = system
    image_count = 0
    total_image_bytes = 0
    for message in converted:
        for block in message["content"]:
            if block.get("type") != "image":
                continue
            image_count += 1
            decoded_size = len(base64.b64decode(block["source"]["data"], validate=True))
            if decoded_size > _MAX_IMAGE_BYTES:
                raise ValueError("MODEL_IMAGE_TOO_LARGE")
            total_image_bytes += decoded_size
    if image_count > _MAX_IMAGE_COUNT:
        raise ValueError("MODEL_TOO_MANY_IMAGES")
    if total_image_bytes > _MAX_TOTAL_IMAGE_BYTES:
        raise ValueError("MODEL_REQUEST_TOO_LARGE")
    if len(str(payload).encode("utf-8")) > _MAX_REQUEST_BYTES:
        raise ValueError("MODEL_REQUEST_TOO_LARGE")
    return payload


def _protocol_call_error(exc: Exception) -> ProtocolCallError:
    """把上游 SDK 异常归一化为 ProtocolCallError，提取状态码、错误码与脱敏响应体。"""
    name = type(exc).__name__.lower()
    status = _status_code(exc)
    body = getattr(exc, "body", None)
    if body is None:
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                body = response.json()
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                body = getattr(response, "text", None)
    provider_code, provider_message = _provider_error_fields(body)
    if not provider_message:
        provider_message = _safe_fragment(exc, 400)
    details = {
        "status_code": status,
        "provider_code": provider_code,
        "provider_message": provider_message,
        "upstream_body": _safe_upstream_body(body),
        "request_id": _safe_fragment(getattr(exc, "request_id", None), 128),
    }
    code, retryable = _model_error_classification(status=status, exception_name=name)
    return ProtocolCallError(code, retryable=retryable, **details)


def _model_error_classification(
    *,
    status: int | None,
    exception_name: str = "",
) -> tuple[str, bool]:
    """按 HTTP 状态码与异常类名归类上游错误，返回错误码与是否可重试。"""
    if status == 401 or "authentication" in exception_name:
        return "MODEL_AUTHENTICATION_FAILED", False
    if status == 403 or "permission" in exception_name:
        return "MODEL_PERMISSION_DENIED", False
    if status == 404 or "notfound" in exception_name:
        return "MODEL_ENDPOINT_NOT_FOUND", False
    if status == 429 or "ratelimit" in exception_name:
        return "MODEL_RATE_LIMITED", True
    if status in {408, 504} or "timeout" in exception_name or "connecterror" in exception_name:
        return "MODEL_TIMEOUT", True
    if status in {400, 422}:
        return "MODEL_INVALID_REQUEST", False
    if status == 409:
        return "MODEL_UPSTREAM_CONFLICT", False
    if status == 413:
        return "MODEL_REQUEST_TOO_LARGE", False
    if status in {500, 502, 503}:
        return "MODEL_UPSTREAM_UNAVAILABLE", True
    return "MODEL_UPSTREAM_ERROR", status is None or status >= 500


def _status_code(exc: Exception) -> int | None:
    """从异常自身或其 response 上取 HTTP 状态码，取不到或非法时返回 None。"""
    raw = getattr(exc, "status_code", None)
    if raw is None:
        raw = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _provider_error_fields(body: Any) -> tuple[str | None, str | None]:
    """从上游响应体提取错误码与错误消息，兼容 error 嵌套以及 code/type/status 字段。"""
    candidate = body
    if isinstance(candidate, dict) and isinstance(candidate.get("error"), dict):
        candidate = candidate["error"]
    if not isinstance(candidate, dict):
        return None, None
    code = _safe_fragment(
        candidate.get("code") or candidate.get("type") or candidate.get("status"),
        128,
    )
    message = _safe_fragment(candidate.get("message") or candidate.get("detail"), 400)
    return code, message


def _safe_upstream_body(body: Any) -> str | None:
    """把上游响应体脱敏并截断成可安全写入日志的字符串。"""
    if body is None:
        return None
    redacted = _redact_sensitive_values(body)
    if isinstance(redacted, str):
        return _safe_fragment(redacted, 2_000)
    try:
        return _safe_fragment(
            json.dumps(redacted, ensure_ascii=False, separators=(",", ":")),
            2_000,
        )
    except (TypeError, ValueError):
        return _safe_fragment(redacted, 2_000)


def _redact_sensitive_values(value: Any) -> Any:
    """递归脱敏 key、token、secret 等字段，列表最多处理前 50 项。"""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(marker in normalized for marker in ("api_key", "authorization", "token", "secret")):
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = _redact_sensitive_values(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive_values(item) for item in value[:50]]
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    return value


def _redact_sensitive_text(value: str) -> str:
    """用正则把文本里的 api key、Authorization、Bearer token 等替换为 [redacted]。"""
    redacted = re.sub(
        r"(?i)(api[-_ ]?key|authorization|access[-_ ]?token|secret)"
        r"(\s*[\"']?\s*[:=]\s*[\"']?)([^\s\"',;}]+)",
        r"\1\2[redacted]",
        value,
    )
    return re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+\-/=]+", "Bearer [redacted]", redacted)


def _safe_fragment(value: Any, limit: int) -> str | None:
    """把任意值压成单行短文本；空值返回 None，超长则截断并加省略号。"""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    return text if len(text) <= limit else f"{text[:limit]}…"


def _raise_if_cancelled(request: dict[str, Any]) -> None:
    """请求携带的 CancellationToken 已取消时抛 MODEL_CANCELLED。"""
    token = request.get("_cancellation")
    if isinstance(token, CancellationToken) and token.cancelled:
        raise ProtocolCallError("MODEL_CANCELLED")


def _wire_request(request: dict[str, Any]) -> dict[str, Any]:
    """剔除内部下划线字段（如 _cancellation），得到可发往上游的请求体。"""
    return {key: value for key, value in request.items() if not key.startswith("_")}


def _anthropic_content(value: Any, role: str) -> list[dict[str, Any]]:
    """把单条消息内容转换为 Anthropic content blocks。

    文本转 text block，user 图片转 base64 image block；非 data URL 图片抛 ValueError。
    """
    if isinstance(value, str):
        return [{"type": "text", "text": value}] if value else []
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            text = str(item.get("text") or "")
            if text:
                blocks.append({"type": "text", "text": text})
            continue
        if item.get("type") != "image_url" or role != "user":
            continue
        image = item.get("image_url")
        url = str(image.get("url") or "") if isinstance(image, dict) else ""
        match = _DATA_URL.fullmatch(url)
        if not match:
            raise ValueError("MODEL_IMAGE_DATA_URL_INVALID")
        try:
            base64.b64decode(match.group(2), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("MODEL_IMAGE_DATA_URL_INVALID") from exc
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": match.group(1),
                    "data": match.group(2),
                },
            }
        )
    return blocks


def _content_text(value: Any) -> str:
    """拼接内容里的全部 text 片段，用于 system 提示归一化。"""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "".join(
        str(item.get("text") or "")
        for item in value
        if isinstance(item, dict) and item.get("type") == "text"
    )


def _anthropic_usage(value: Any) -> Any:
    """把 Anthropic usage 映射为 prompt/completion/total_tokens；缺值返回 None。"""
    if value is None:
        return None
    input_tokens = getattr(value, "input_tokens", None)
    output_tokens = getattr(value, "output_tokens", None)
    return SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=(input_tokens + output_tokens)
        if isinstance(input_tokens, int) and isinstance(output_tokens, int)
        else None,
    )


def _stream_chunk(
    response_id: Any,
    *,
    text: str = "",
    finish_reason: Any = None,
    usage: Any = None,
    provider_event: Any = None,
) -> Any:
    """构造统一流式 chunk：只有出现文本或结束原因时才带 choices。"""
    choices = []
    if text or finish_reason:
        choices.append(
            SimpleNamespace(
                delta=SimpleNamespace(content=text),
                finish_reason=finish_reason,
            )
        )
    return SimpleNamespace(
        id=response_id,
        usage=usage,
        choices=choices,
        provider_event=provider_event,
    )

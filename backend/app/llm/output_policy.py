from __future__ import annotations


# Control-plane routers already have deterministic fallbacks. Retrying an empty
# reasoning-only response delays the real work without improving routing.
OPERATION_EMPTY_RESPONSE_RETRIES: dict[str, int] = {
    "knowledge.document_route": 0,
    "knowledge.bucket_route": 0,
}

def operation_output_tokens(operation: str, configured_tokens: int) -> int:
    """返回模型配置里的输出预算，不再按 operation 做任何隐式放大。

    operation 仍留在签名里供调用方与可观测性标识阶段，但各阶段统一以模型
    配置的 Max Tokens 为唯一来源；configured_tokens 为空时按 1 兜底。
    """

    del operation
    return max(1, int(configured_tokens or 1))


def operation_empty_response_retries(operation: str, default_retries: int) -> int:
    """返回某 operation 出现空响应（只有推理、没有正文）时的重试次数。

    OPERATION_EMPTY_RESPONSE_RETRIES 里有显式配置（含 0）就用配置值，否则回落到
    default_retries；结果最低截到 0。
    """
    configured = OPERATION_EMPTY_RESPONSE_RETRIES.get(operation)
    return max(0, int(default_retries if configured is None else configured))

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast

ProviderT = TypeVar("ProviderT")


@dataclass(frozen=True)
class CapabilityBinding(Generic[ProviderT]):
    """某个能力当前选中的提供方身份及其实时客户端,解析完成后不可变。"""

    capability: str
    provider_id: str
    provider_deployment_id: str
    service_contract_version: str
    provider: ProviderT
    operation_versions: tuple[tuple[str, str], ...] = ()
    config_revision: str = "default"
    resolution_reason: str = "configured"

    @property
    def contract_version(self) -> str:
        """早期接入方使用的兼容别名,等价于 service_contract_version。"""
        return self.service_contract_version

    def durable(self) -> DurableCapabilityBinding:
        """剥离实时 provider 客户端,只导出可序列化、可落盘的绑定身份。"""
        return DurableCapabilityBinding(
            capability=self.capability,
            provider_id=self.provider_id,
            provider_deployment_id=self.provider_deployment_id,
            service_contract_version=self.service_contract_version,
            operation_versions=self.operation_versions,
            config_revision=self.config_revision,
            resolution_reason=self.resolution_reason,
        )


@dataclass(frozen=True)
class DurableCapabilityBinding:
    """可序列化的绑定身份,刻意不持有任何实时客户端,便于持久化与跨进程传递。"""

    capability: str
    provider_id: str
    provider_deployment_id: str
    service_contract_version: str
    operation_versions: tuple[tuple[str, str], ...] = ()
    config_revision: str = "default"
    resolution_reason: str = "configured"


@dataclass(frozen=True)
class CapabilitySnapshot:
    """在一次 Agent 回合开始时一次性捕获的不可变提供方选择结果。"""

    bindings: MappingProxyType
    snapshot_id: str
    durable_bindings: tuple[DurableCapabilityBinding, ...]

    def get(self, capability: str) -> CapabilityBinding[Any] | None:
        """按能力名取绑定;该能力未注册时返回 None 而非抛错。"""
        return cast(CapabilityBinding[Any] | None, self.bindings.get(capability))

    def require(self, capability: str) -> CapabilityBinding[Any]:
        """按能力名取绑定;缺失时抛 LookupError,供必须拿到提供方的调用点使用。"""
        binding = self.get(capability)
        if binding is None:
            raise LookupError(f"capability provider is not configured: {capability}")
        return binding


class CapabilityRegistry:
    """显式注册表:登记属于启动期配置动作,不支持运行时热插拔替换提供方。"""

    def __init__(self) -> None:
        """初始化空的绑定表、重建器索引以及未封存状态。"""
        self._bindings: dict[str, CapabilityBinding[Any]] = {}
        self._rehydrators: dict[tuple[str, str], Callable[[DurableCapabilityBinding], Any]] = {}
        self._sealed = False

    def seal(self) -> None:
        """标记启动期注册已结束;封存后的注册表拒绝任何变更。"""
        self._sealed = True

    def register(self, binding: CapabilityBinding[Any]) -> None:
        """登记一个能力绑定。

        封存后调用抛 RuntimeError;关键字段为空或同一能力重复登记时抛 ValueError。
        """
        if self._sealed:
            raise RuntimeError("capability registry is sealed")
        if (
            not binding.capability
            or not binding.provider_id
            or not binding.provider_deployment_id
            or not binding.contract_version
        ):
            raise ValueError(
                "capability, provider_id, provider_deployment_id and contract_version are required"
            )
        if binding.capability in self._bindings:
            raise ValueError(f"capability already registered: {binding.capability}")
        self._bindings[binding.capability] = binding

    def register_rehydrator(
        self,
        provider_id: str,
        provider_deployment_id: str,
        rehydrator: Callable[[DurableCapabilityBinding], Any],
    ) -> None:
        """按 provider_id + provider_deployment_id 登记重建器,用于从持久化绑定恢复客户端。

        封存后调用抛 RuntimeError,同一部署重复登记抛 ValueError。
        """
        if self._sealed:
            raise RuntimeError("capability registry is sealed")
        key = (provider_id, provider_deployment_id)
        if key in self._rehydrators:
            raise ValueError(f"rehydrator already registered: {provider_id}/{provider_deployment_id}")
        self._rehydrators[key] = rehydrator

    def rehydrate(self, binding: DurableCapabilityBinding) -> Any:
        """用已登记的 provider 部署重建实时客户端;部署不可用时抛 LookupError。"""
        rehydrator = self._rehydrators.get(
            (binding.provider_id, binding.provider_deployment_id)
        )
        if rehydrator is None:
            raise LookupError(
                "capability provider deployment is unavailable: "
                f"{binding.provider_id}/{binding.provider_deployment_id}"
            )
        return rehydrator(binding)

    def snapshot(
        self,
        requested: set[str] | None = None,
        *,
        supported_contracts: Mapping[str, set[str]] | None = None,
    ) -> CapabilitySnapshot:
        """为一次 Agent 回合固化不可变绑定快照,并按规范化内容计算 snapshot_id。

        requested 为 None 表示全选;传入 supported_contracts 时,任一绑定契约版本不在
        允许集合内即抛 ValueError,避免把不兼容的提供方暴露给调用方。
        """
        selected = {
            key: value
            for key, value in self._bindings.items()
            if requested is None or key in requested
        }
        if supported_contracts is not None:
            unsupported = [
                f"{key}={binding.service_contract_version}"
                for key, binding in selected.items()
                if binding.service_contract_version
                not in supported_contracts.get(key, set())
            ]
            if unsupported:
                raise ValueError(
                    "unsupported capability contract: " + ", ".join(sorted(unsupported))
                )
        durable_bindings = tuple(
            binding.durable() for _, binding in sorted(selected.items())
        )
        canonical = [
            {
                "capability": key,
                "provider_id": binding.provider_id,
                "provider_deployment_id": binding.provider_deployment_id,
                "service_contract_version": binding.service_contract_version,
                "operation_versions": sorted(binding.operation_versions),
                "config_revision": binding.config_revision,
                "resolution_reason": binding.resolution_reason,
            }
            for key, binding in sorted(selected.items())
        ]
        snapshot_id = sha256(
            json.dumps(canonical, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()
        return CapabilitySnapshot(MappingProxyType(selected), snapshot_id, durable_bindings)

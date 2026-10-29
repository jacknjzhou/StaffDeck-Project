export const ENTERPRISE_CAPABILITY_CATALOG_CHANGED_EVENT =
  'ultrarag-enterprise-capability-catalog-changed';

export type EnterpriseCapabilityResourceType = 'tool' | 'skill' | 'knowledge' | 'sop';

export type EnterpriseCapabilityCatalogChange = {
  resourceType: EnterpriseCapabilityResourceType;
  agentId?: string;
};

/** 派发能力目录变更事件，通知已订阅方刷新工具、技能、知识或 SOP 列表。 */
export function announceEnterpriseCapabilityCatalogChange(
  detail: EnterpriseCapabilityCatalogChange,
) {
  window.dispatchEvent(
    new CustomEvent<EnterpriseCapabilityCatalogChange>(
      ENTERPRISE_CAPABILITY_CATALOG_CHANGED_EVENT,
      { detail },
    ),
  );
}

/**
 * 订阅能力目录刷新信号：窗口聚焦、页面重新显示、标签页转为可见或收到目录变更事件时触发回调。
 *
 * 返回取消订阅函数，调用方应在清理阶段执行以避免事件监听泄漏。
 */
export function subscribeEnterpriseCapabilityCatalogRefresh(listener: () => void) {
  const onFocus = () => listener();
  const onPageShow = () => listener();
  const onVisibilityChange = () => {
    if (document.visibilityState === 'visible') listener();
  };
  const onCatalogChange = () => listener();

  window.addEventListener('focus', onFocus);
  window.addEventListener('pageshow', onPageShow);
  document.addEventListener('visibilitychange', onVisibilityChange);
  window.addEventListener(ENTERPRISE_CAPABILITY_CATALOG_CHANGED_EVENT, onCatalogChange);

  return () => {
    window.removeEventListener('focus', onFocus);
    window.removeEventListener('pageshow', onPageShow);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    window.removeEventListener(ENTERPRISE_CAPABILITY_CATALOG_CHANGED_EVENT, onCatalogChange);
  };
}

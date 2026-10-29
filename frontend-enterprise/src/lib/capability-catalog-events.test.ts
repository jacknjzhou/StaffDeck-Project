// @vitest-environment jsdom

import { describe, expect, it, vi } from 'vitest';

import {
  announceEnterpriseCapabilityCatalogChange,
  subscribeEnterpriseCapabilityCatalogRefresh,
} from './capability-catalog-events';

/** 验证企业能力目录刷新事件的订阅与广播行为。 */
describe('enterprise capability catalog refresh events', () => {
  /** 验证当前页面内能力变更后订阅者会被通知一次。 */
  it('refreshes when a capability changes in the current page', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeEnterpriseCapabilityCatalogRefresh(listener);

    announceEnterpriseCapabilityCatalogChange({ resourceType: 'tool', agentId: 'agent-1' });

    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });

  /** 验证 SOP 编辑器重新获得焦点时会触发一次刷新回调。 */
  it('refreshes when the SOP editor regains focus', () => {
    const listener = vi.fn();
    const unsubscribe = subscribeEnterpriseCapabilityCatalogRefresh(listener);

    window.dispatchEvent(new Event('focus'));

    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });
});

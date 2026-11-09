// @vitest-environment jsdom

import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AGENT_STORAGE_KEY } from '@/lib/agent-scope-storage';
import type { AgentProfileRead } from '@/types';

import App from './App';

const AUTH_STORAGE_KEY = 'ultrarag_auth';

const authUser = {
  id: 'user-1',
  tenant_id: 'tenant_demo',
  username: 'demo',
  role: 'admin',
};

/** 构造一个最小可用的员工档案，仅保留作用域校正依赖的字段。 */
function makeAgent(id: string, name: string): AgentProfileRead {
  return {
    id,
    tenant_id: 'tenant_demo',
    name,
    is_overall: false,
    status: 'active',
    metadata: {},
    resources: [],
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  };
}

const agentOne = makeAgent('agent-1', '小艾');
const agentTwo = makeAgent('agent-2', '小博');

/** 构造仅实现 text() 的伪 Response，供 api client 解析。 */
function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

/** 补齐 jsdom 缺失的 matchMedia 与指针捕获 API。 */
function stubBrowserApis() {
  if (!window.matchMedia) {
    window.matchMedia = ((query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    })) as typeof window.matchMedia;
  }
  if (!Element.prototype.hasPointerCapture) {
    Element.prototype.hasPointerCapture = () => false;
  }
  if (!Element.prototype.releasePointerCapture) {
    Element.prototype.releasePointerCapture = () => {};
  }
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = () => {};
  }
}

beforeEach(() => {
  stubBrowserApis();
  window.localStorage.setItem(
    AUTH_STORAGE_KEY,
    JSON.stringify({ token: 'token-1', user: authUser }),
  );
  window.localStorage.setItem('staffdeck_onboarding_guide_seen', '1');
  window.localStorage.setItem('staffdeck_quick_start_guide_seen', '1');
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.history.pushState({}, '', '/');
});

describe('App agent scope refresh race', () => {
  /**
   * 作用域切到一个尚未在本地花名册中的员工时，刷新在途期间不得用旧花名册回退，
   * 否则用户的选择会被一次过期的校正覆盖。
   */
  it('keeps the newly selected agent while the roster refetch is in flight', async () => {
    const agentCalls: number[] = [];
    // 首次加载的所有调用都立即返回旧花名册；只有 dispatch 之后的刷新才延迟。
    let delayNextRoster = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/auth/me')) return jsonResponse(authUser);
      if (url.includes('/api/enterprise/agents')) {
        agentCalls.push(Date.now());
        if (!delayNextRoster) return jsonResponse([agentOne]);
        // 模拟慢网络：刷新结果延后返回，期间界面仍持有旧花名册。
        await new Promise((resolve) => setTimeout(resolve, 100));
        return jsonResponse([agentOne, agentTwo]);
      }
      if (url.includes('/api/enterprise/model-configs')) return jsonResponse([]);
      if (url.includes('/api/enterprise/teams')) return jsonResponse([]);
      if (url.includes('/api/enterprise/')) return jsonResponse([]);
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);

    window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, 'agent-1');
    window.history.pushState({}, '', '/enterprise/teams');
    render(<I18nProvider><App /></I18nProvider>);

    await screen.findByLabelText('切换当前员工');
    // 等首批花名册真正渲染完成，否则这里测的只是首次加载，而不是刷新竞态。
    await waitFor(() => {
      expect(screen.getByLabelText('切换当前员工').textContent).toContain('小艾');
      expect(window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY)).toBe('agent-1');
    });
    const callsBeforeDispatch = agentCalls.length;
    delayNextRoster = true;

    act(() => {
      window.dispatchEvent(new CustomEvent('ultrarag-enterprise-agent-scope-change', {
        detail: { agentId: 'agent-2' },
      }));
    });

    // 刷新在途时用户的选择必须已生效；此时不得回退到旧花名册里的 agent-1。
    await new Promise((resolve) => setTimeout(resolve, 40));
    expect(window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY)).toBe('agent-2');

    // 刷新返回包含 agent-2 的新花名册后，作用域仍应稳定指向 agent-2。
    await waitFor(() => {
      expect(agentCalls.length).toBeGreaterThan(callsBeforeDispatch);
    });
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY)).toBe('agent-2');
    expect(screen.getByLabelText('切换当前员工').textContent).toContain('小博');
  });
});

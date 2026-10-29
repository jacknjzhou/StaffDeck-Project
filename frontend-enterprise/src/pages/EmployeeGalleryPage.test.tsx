// @vitest-environment jsdom

import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { EnterpriseAuthUser } from '@/auth';
import type { TeamRead } from '@/types';

import EmployeeGalleryPage from './EmployeeGalleryPage';

const team: TeamRead = {
  id: 'team-1',
  tenant_id: 'tenant_demo',
  name: '增长团队',
  description: '负责增长实验与内容投放',
  owner_user_id: 'user-1',
  config: {},
  status: 'active',
  members: [
    { id: 'm-1', team_id: 'team-1', agent_id: 'agent-1', role: 'leader', agent_name: '小艾', created_at: '2026-08-01T00:00:00Z' },
    { id: 'm-2', team_id: 'team-1', agent_id: 'agent-2', role: 'member', agent_name: '小北', created_at: '2026-08-01T00:00:00Z' },
    { id: 'm-3', team_id: 'team-1', agent_id: 'agent-3', role: 'member', agent_name: '小南', created_at: '2026-08-01T00:00:00Z' },
    { id: 'm-4', team_id: 'team-1', agent_id: 'agent-4', role: 'member', agent_name: '小西', created_at: '2026-08-01T00:00:00Z' },
  ],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

/** 构造仅包含 ok/status 与 JSON 文本体的伪 Response 对象。 */
function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

/** 按 URL 分发员工画廊所需的 fetch 假数据，并返回该 mock 以便断言调用。 */
function stubGalleryFetch(teams: TeamRead[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method === 'POST' && url.includes('/tl/session')) {
      return jsonResponse({ session_id: 'session-tl-1' });
    }
    if (url.includes('/api/enterprise/teams')) return jsonResponse(teams);
    if (url.includes('/api/enterprise/agents')) return jsonResponse([]);
    return jsonResponse({});
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** 渲染当前路由的 pathname 与 search，供断言跳转目标。 */
function LocationEcho() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

/** 在内存路由与 I18n Provider 中渲染员工画廊页，并挂载跳转回显路由。 */
function renderGallery(currentUser?: EnterpriseAuthUser) {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={['/workspace/gallery']}>
        <Routes>
          <Route path="/workspace/gallery" element={<EmployeeGalleryPage currentUser={currentUser} />} />
          <Route path="/workspace/chat/:sessionId" element={<LocationEcho />} />
          <Route path="/enterprise/teams/:teamId" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>
    </I18nProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe('EmployeeGalleryPage teams tab', () => {
  /** 验证团队卡片展示成员数、项目领导与折叠的头像叠放。 */
  it('renders team chat cards with member count, project leader and avatar stack', async () => {
    const user = userEvent.setup();
    stubGalleryFetch([team]);
    renderGallery();

    await user.click(await screen.findByRole('tab', { name: '团队对话' }));

    const section = await screen.findByRole('region', { name: '团队' });
    expect(within(section).getByText('增长团队')).toBeTruthy();
    expect(within(section).getByText('负责增长实验与内容投放')).toBeTruthy();
    expect(within(section).getByText('4 名成员')).toBeTruthy();
    expect(within(section).getByText('项目领导：小艾')).toBeTruthy();
    // 前 3 个成员头像叠放，其余折叠为 +N
    expect(within(section).getByText('+1')).toBeTruthy();
  });

  /** 验证当前用户不是团队所有者时仍渲染租户内的团队。 */
  it('renders tenant teams even when the current user is not the team owner', async () => {
    const user = userEvent.setup();
    stubGalleryFetch([team]);
    renderGallery({
      id: 'user-2',
      username: 'member',
      tenant_id: 'tenant_demo',
      role: 'member',
    });

    await user.click(await screen.findByRole('tab', { name: '团队对话' }));

    const section = await screen.findByRole('region', { name: '团队' });
    expect(within(section).getByText('增长团队')).toBeTruthy();
  });

  /** 验证首次团队请求失败后，打开团队页签会重试并成功展示。 */
  it('retries a failed team request when the teams tab is opened', async () => {
    const user = userEvent.setup();
    let teamRequestCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/enterprise/teams')) {
        teamRequestCount += 1;
        if (teamRequestCount === 1) {
          return {
            ok: false,
            status: 502,
            statusText: 'Bad Gateway',
            text: async () => 'Bad Gateway',
          } as Response;
        }
        return jsonResponse([team]);
      }
      if (url.includes('/api/enterprise/agents')) return jsonResponse([]);
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', fetchMock);
    renderGallery();

    await waitFor(() => expect(teamRequestCount).toBe(1));
    await user.click(await screen.findByRole('tab', { name: '团队对话' }));

    const section = await screen.findByRole('region', { name: '团队' });
    expect(await within(section).findByText('增长团队')).toBeTruthy();
    expect(teamRequestCount).toBe(2);
  });

  /** 验证点击团队卡片会进入聊天应用中的持久团队群聊。 */
  it('opens the persistent team group in the chat app', async () => {
    const user = userEvent.setup();
    const fetchMock = stubGalleryFetch([team]);
    renderGallery();

    await user.click(await screen.findByRole('tab', { name: '团队对话' }));
    const section = await screen.findByRole('region', { name: '团队' });
    await user.click(within(section).getByRole('button', { name: '增长团队' }));

    expect((await screen.findByTestId('location')).textContent).toBe('/workspace/chat/session-tl-1');
    const postCall = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST');
    expect(String(postCall?.[0])).toContain('/api/enterprise/teams/team-1/tl/session');
  });

  /** 验证员工相关页签下不渲染团队区块。 */
  it('does not render the team section on employee tabs', async () => {
    const user = userEvent.setup();
    stubGalleryFetch([team]);
    renderGallery();

    await screen.findByText('暂无数字员工');
    expect(screen.queryByRole('region', { name: '团队' })).toBeNull();

    await user.click(screen.getByRole('tab', { name: '我的数字员工' }));
    expect(screen.queryByRole('region', { name: '团队' })).toBeNull();
  });

  /** 验证没有任何团队时展示团队空状态。 */
  it('shows the teams empty state when there are no teams', async () => {
    const user = userEvent.setup();
    stubGalleryFetch([]);
    renderGallery();

    await user.click(await screen.findByRole('tab', { name: '团队对话' }));

    expect(await screen.findByText('暂无团队')).toBeTruthy();
  });
});

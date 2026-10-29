// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';

import TeamChatPage from './TeamChatPage';

/** 渲染当前路由的路径与查询串，用于断言重定向结果。 */
function LocationEcho() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

/** 每个用例结束后卸载组件并还原被替换的全局对象。 */
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** 旧版团队聊天路由的重定向行为。 */
describe('TeamChatPage legacy redirect', () => {
  /** 验证访问旧版团队聊天地址会请求团队会话并跳转到工作区聊天页。 */
  it('opens the persistent team group in the chat app', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => ({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: async () => JSON.stringify({ session_id: 'session-team-1' }),
    } as Response));
    vi.stubGlobal('fetch', fetchMock);
    render(
      <MemoryRouter initialEntries={['/enterprise/teams/team-1/chat']}>
        <Routes>
          <Route path="/enterprise/teams/:teamId/chat" element={<TeamChatPage />} />
          <Route path="/workspace/chat/:sessionId" element={<LocationEcho />} />
        </Routes>
      </MemoryRouter>,
    );

    expect((await screen.findByTestId('location')).textContent).toBe(
      '/workspace/chat/session-team-1',
    );
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/teams/team-1/tl/session');
  });
});

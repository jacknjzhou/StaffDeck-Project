// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';
import type { ChatSession, TeamRead } from '@/types';

import type { UseChatSession } from '../useChatSession';
import ChatHeader from './ChatHeader';

const team: TeamRead = {
  id: 'team-1',
  tenant_id: 'tenant_demo',
  name: '增长团队',
  description: '',
  owner_user_id: 'user-1',
  config: {},
  status: 'active',
  members: [],
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

/** 构造返回指定 JSON 内容的伪 Response 对象（状态码固定为 200）。 */
function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(body ?? {}),
  } as Response;
}

/** 构造仅包含用例所需字段的会话状态对象，供头部组件渲染使用。 */
function buildChat(session: Partial<ChatSession>): UseChatSession {
  return {
    auth: null,
    currentSession: {
      id: 'session-1',
      tenant_id: 'tenant_demo',
      status: 'active',
      updated_at: '2026-08-01T00:00:00Z',
      ...session,
    } as ChatSession,
    openRename: vi.fn(),
    logout: vi.fn(),
  } as unknown as UseChatSession;
}

/** 在 i18n 上下文中渲染会话头部组件。 */
function renderHeader(chat: UseChatSession) {
  return render(
    <I18nProvider>
      <ChatHeader chat={chat} />
    </I18nProvider>,
  );
}

/** 每个用例结束后卸载组件并还原全局 mock。 */
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** 会话头部对团队身份标识的展示与解析。 */
describe('ChatHeader team badge', () => {
  /** 验证头部高度与顶部内边距为原生桌面拖拽区域留出空间。 */
  it('keeps header controls below the native desktop drag region', () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([team])));
    const { container } = renderHeader(buildChat({ title: '计划讨论' }));
    const headerClassName = container.firstElementChild?.className || '';

    expect(headerClassName).toContain('h-[88px]');
    expect(headerClassName).toContain('pt-[32px]');
  });

  /** 验证团队会话展示团队名与群聊标识。 */
  it('shows the team name and group-chat badge when present', () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([team])));
    renderHeader(buildChat({ title: '计划讨论', team_id: 'team-1', team_name: '增长团队' }));

    expect(screen.getByText('增长团队')).toBeTruthy();
    expect(screen.getByText('群聊')).toBeTruthy();
  });

  /** 验证历史遗留的项目领导标题被群聊身份取代，不再展示。 */
  it('hides the legacy project-lead session title behind the group identity', () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([team])));
    renderHeader(buildChat({ title: '增长团队 · TL 对话', team_id: 'team-1', team_name: '增长团队' }));

    expect(screen.getByText('增长团队')).toBeTruthy();
    expect(screen.getByText('群聊')).toBeTruthy();
    expect(screen.queryByText('增长团队 · TL 对话')).toBeNull();
  });

  /** 验证缺少 team_name 时从团队列表接口解析出团队名。 */
  it('resolves the team name from the teams list when team_name is missing', async () => {
    const fetchMock = vi.fn(async () => jsonResponse([team]));
    vi.stubGlobal('fetch', fetchMock);
    renderHeader(buildChat({ title: '计划讨论', team_id: 'team-1' }));

    expect((await screen.findByText('增长团队')).textContent).toBeTruthy();
    expect(screen.getByText('群聊')).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/enterprise/teams?tenant_id='),
      expect.anything(),
    );
  });

  /** 验证普通员工会话不渲染任何团队标识。 */
  it('renders no badge for regular employee sessions', () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse([team])));
    renderHeader(buildChat({ title: '计划讨论' }));

    expect(screen.queryByText(/团队/)).toBeNull();
  });
});

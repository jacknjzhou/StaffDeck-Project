// @vitest-environment jsdom

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../i18n';

import AccountsPage, {
  AccountRoleBadge,
  AccountStatusBadge,
  effectiveStatus,
  statusActions,
} from './AccountsPage';

const mocks = vi.hoisted(() => ({
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

vi.mock('@/components/ui/app-toast', () => ({
  notify: {
    error: mocks.notifyError,
    success: mocks.notifySuccess,
  },
}));

/** 验证账号角色徽章的文案与配色渲染。 */
describe('AccountRoleBadge', () => {
  /** 验证管理员角色展示“管理员”并使用蓝色状态样式。 */
  it('uses the blue status treatment for administrators', () => {
    const rendered = renderToStaticMarkup(createElement(AccountRoleBadge, { role: 'admin' }));

    expect(rendered).toContain('管理员');
    expect(rendered).toContain('bg-[#e8f0ff]');
    expect(rendered).toContain('text-[#1a71ff]');
    expect(rendered).not.toContain('bg-[#f2f3f7]');
  });

  /** 验证普通成员展示“普通成员”并使用中性灰色状态样式。 */
  it('uses the neutral status treatment for members', () => {
    const rendered = renderToStaticMarkup(createElement(AccountRoleBadge, { role: 'member' }));

    expect(rendered).toContain('普通成员');
    expect(rendered).toContain('bg-[#f2f3f7]');
    expect(rendered).toContain('text-[#858b9c]');
    expect(rendered).not.toContain('bg-[#e8f0ff]');
  });
});

/** 验证账号状态徽标按状态渲染不同文案与配色。 */
describe('AccountStatusBadge', () => {
  /** 验证待审核、已拒绝、正常三种状态分别使用橙、红、绿样式。 */
  it('renders the badge text and tone for each status', () => {
    const pending = renderToStaticMarkup(createElement(AccountStatusBadge, { status: 'pending' }));
    const rejected = renderToStaticMarkup(createElement(AccountStatusBadge, { status: 'rejected' }));
    const active = renderToStaticMarkup(createElement(AccountStatusBadge, { status: 'active' }));

    expect(pending).toContain('待审核');
    expect(pending).toContain('bg-[#fff2e5]');
    expect(rejected).toContain('已拒绝');
    expect(rejected).toContain('bg-[#fce7e7]');
    expect(active).toContain('正常');
    expect(active).toContain('bg-[#e9f7ef]');
  });

  /** 未知状态与缺失状态一样按「正常」渲染，与筛选口径保持一致。 */
  it('renders an unknown status as active', () => {
    const unknown = renderToStaticMarkup(
      createElement(AccountStatusBadge, { status: 'suspended' }),
    );

    expect(unknown).toContain('正常');
    expect(unknown).toContain('bg-[#e9f7ef]');
  });
});

/** 验证状态归一：未知值不会被误判成待审核或已拒绝。 */
describe('effectiveStatus', () => {
  /** pending/rejected 原样保留，其余值（含缺失）一律归为 active。 */
  it('passes through pending/rejected and treats everything else as active', () => {
    expect(effectiveStatus('pending')).toBe('pending');
    expect(effectiveStatus('rejected')).toBe('rejected');
    expect(effectiveStatus('active')).toBe('active');
    expect(effectiveStatus('suspended')).toBe('active');
    expect(effectiveStatus(undefined)).toBe('active');
  });
});

/**
 * 验证审核动作门控。
 *
 * 这是新增的纯函数覆盖（实现前不存在该导出），不是行为 RED；
 * Radix 菜单在 jsdom 中的展开断言成本较高，这里直接断言门控函数的输出。
 */
describe('statusActions', () => {
  /** 待审核两项、已拒绝一项、正常与未知状态均无审核动作。 */
  it('gates approval actions by effective status', () => {
    expect(statusActions('pending')).toEqual([
      { label: '通过', status: 'active' },
      { label: '拒绝', status: 'rejected' },
    ]);
    expect(statusActions('rejected')).toEqual([{ label: '通过', status: 'active' }]);
    expect(statusActions('active')).toEqual([]);
    expect(statusActions('suspended')).toEqual([]);
    expect(statusActions(undefined)).toEqual([]);
  });
});

/** 构造返回指定 JSON 内容与状态码的伪 Response 对象。 */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    text: async () => JSON.stringify(body),
  } as Response;
}

const accounts = [
  {
    id: 'user-active',
    tenant_id: 'tenant_demo',
    username: 'active_user',
    display_name: '在职员工',
    role: 'member',
    status: 'active',
  },
  {
    id: 'user-pending',
    tenant_id: 'tenant_demo',
    username: 'pending_user',
    display_name: '待审员工',
    role: 'member',
    status: 'pending',
  },
];

/** 在 i18n 上下文中渲染账号管理页。 */
function renderAccountsPage() {
  render(
    <I18nProvider>
      <AccountsPage
        currentUser={{ id: 'user-1', tenant_id: 'tenant_demo', username: 'admin', role: 'admin' }}
      />
    </I18nProvider>,
  );
}

/** 取包含指定用户名的移动端卡片；同一行在桌面表格中还有一份，取 `article` 即可区分。 */
function mobileCard(username: string): HTMLElement {
  const card = screen
    .getAllByText(username)
    .map((node) => node.closest('article'))
    .find((node): node is HTMLElement => node !== null);
  if (!card) throw new Error(`未找到 ${username} 的移动端卡片`);
  return card;
}

/** 打开指定账号行的操作菜单。 */
async function openRowMenu(user: ReturnType<typeof userEvent.setup>, username: string) {
  await user.click(within(mobileCard(username)).getByRole('button', { name: '账号操作' }));
}

/** 按 HTTP 方法筛选 fetch 调用；未指定 method 视为 GET。 */
function callsWithMethod(fetchMock: ReturnType<typeof vi.fn>, method: string) {
  return fetchMock.mock.calls.filter(
    ([, init]) => ((init as RequestInit | undefined)?.method ?? 'GET') === method,
  );
}

/** 每个用例结束后卸载组件、还原全局桩与 mock。 */
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  mocks.notifyError.mockReset();
  mocks.notifySuccess.mockReset();
});

/** 在 jsdom 中补齐 Radix Select 依赖的浏览器 API。 */
beforeAll(() => {
  window.HTMLElement.prototype.scrollIntoView = vi.fn();
  window.HTMLElement.prototype.hasPointerCapture = vi.fn();
  window.HTMLElement.prototype.releasePointerCapture = vi.fn();
});

/** 验证状态筛选与关键字筛选叠加后只保留匹配账号。 */
describe('AccountsPage status filter', () => {
  /** 切换为「待审核」后，正常账号从列表消失、待审核账号仍可见。 */
  it('narrows rows to the selected status', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(accounts)));
    renderAccountsPage();

    await waitFor(() => expect(screen.getAllByText('active_user').length).toBeGreaterThan(0));
    expect(screen.getAllByText('pending_user').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('combobox', { name: '状态筛选' }));
    await user.click(await screen.findByRole('option', { name: '待审核' }));

    await waitFor(() => expect(screen.queryAllByText('active_user')).toHaveLength(0));
    expect(screen.getAllByText('pending_user').length).toBeGreaterThan(0);
  });

  /** 关键字与状态是 AND：只命中关键字但状态不符、或只匹配状态但关键字不符的行都会消失。 */
  it('combines keyword and status with AND semantics', async () => {
    const user = userEvent.setup();
    const rows = [
      ...accounts,
      {
        id: 'user-pending-2',
        tenant_id: 'tenant_demo',
        username: 'pending_extra',
        display_name: '待审员工二',
        role: 'member',
        status: 'pending',
      },
    ];
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(rows)));
    renderAccountsPage();

    await waitFor(() => expect(screen.getAllByText('pending_extra').length).toBeGreaterThan(0));

    await user.click(screen.getByRole('combobox', { name: '状态筛选' }));
    await user.click(await screen.findByRole('option', { name: '待审核' }));
    await waitFor(() => expect(screen.queryAllByText('active_user')).toHaveLength(0));

    const search = screen.getByPlaceholderText('搜索用户名或显示名');
    await user.type(search, 'pending_user');
    await waitFor(() => expect(screen.queryAllByText('pending_extra')).toHaveLength(0));
    expect(screen.getAllByText('pending_user').length).toBeGreaterThan(0);

    // 关键字命中 active_user，但状态筛选是「待审核」——AND 下应无结果。
    await user.clear(search);
    await user.type(search, 'active_user');
    await waitFor(() => expect(screen.getAllByText('暂无账号').length).toBeGreaterThan(0));
    expect(screen.queryAllByText('pending_user')).toHaveLength(0);
  });

  /** 未知状态与徽标口径一致：徽标显示「正常」，就应能出现在「正常」筛选结果里。 */
  it('keeps an unknown status visible under the active filter', async () => {
    const user = userEvent.setup();
    const rows = [
      {
        id: 'user-suspended',
        tenant_id: 'tenant_demo',
        username: 'suspended_user',
        display_name: '未知状态员工',
        role: 'member',
        status: 'suspended',
      },
    ];
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(rows)));
    renderAccountsPage();

    await waitFor(() => expect(screen.getAllByText('suspended_user').length).toBeGreaterThan(0));
    expect(screen.getAllByText('正常').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('combobox', { name: '状态筛选' }));
    await user.click(await screen.findByRole('option', { name: '正常' }));

    await waitFor(() => expect(screen.getAllByText('suspended_user').length).toBeGreaterThan(0));
  });
});

/** 验证审核动作：请求形状、就地更新、失败保留列表与兜底文案。 */
describe('AccountsPage approval actions', () => {
  /** 点击「通过」后用 PUT 返回值就地更新该行，不再重发 GET。 */
  it('updates the approved row in place without refetching', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'PUT') {
        return jsonResponse({ ...accounts[1], status: 'active' });
      }
      return jsonResponse(accounts);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderAccountsPage();

    await waitFor(() => expect(screen.getAllByText('pending_user').length).toBeGreaterThan(0));

    await openRowMenu(user, 'pending_user');
    await user.click(await screen.findByRole('menuitem', { name: '通过' }));

    await waitFor(() => expect(callsWithMethod(fetchMock, 'PUT')).toHaveLength(1));
    const [putUrl, putInit] = callsWithMethod(fetchMock, 'PUT')[0];
    expect(String(putUrl)).toBe('/api/auth/users/user-pending/status');
    expect(JSON.parse(String((putInit as RequestInit).body))).toEqual({
      tenant_id: 'tenant_demo',
      status: 'active',
    });

    await waitFor(() =>
      expect(within(mobileCard('pending_user')).getByText('正常')).toBeTruthy(),
    );
    expect(within(mobileCard('pending_user')).queryByText('待审核')).toBeNull();
    expect(callsWithMethod(fetchMock, 'GET')).toHaveLength(1);
  });

  /** PUT 失败时保留整张列表，并提示错误。 */
  it('keeps rows and shows an error notice when the update fails', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'PUT') {
        return jsonResponse({ detail: '更新失败' }, 500);
      }
      return jsonResponse(accounts);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderAccountsPage();

    await waitFor(() => expect(screen.getAllByText('pending_user').length).toBeGreaterThan(0));

    await openRowMenu(user, 'pending_user');
    await user.click(await screen.findByRole('menuitem', { name: '通过' }));

    await waitFor(() => expect(mocks.notifyError).toHaveBeenCalled());
    expect(within(mobileCard('pending_user')).getByText('待审核')).toBeTruthy();
    expect(screen.getAllByText('active_user').length).toBeGreaterThan(0);
  });

  /** 该行的状态请求未完成前，审核项应保持禁用，避免重复提交。 */
  it('disables the row status actions while its request is in flight', async () => {
    const user = userEvent.setup();
    let releasePut: (() => void) | undefined;
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'PUT') {
        return new Promise<Response>((resolve) => {
          releasePut = () => resolve(jsonResponse({ ...accounts[1], status: 'active' }));
        });
      }
      return jsonResponse(accounts);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderAccountsPage();

    await waitFor(() => expect(screen.getAllByText('pending_user').length).toBeGreaterThan(0));

    await openRowMenu(user, 'pending_user');
    await user.click(await screen.findByRole('menuitem', { name: '通过' }));
    await waitFor(() => expect(releasePut).toBeDefined());
    await waitFor(() =>
      expect(screen.queryByRole('menuitem', { name: '通过' })).toBeNull(),
    );

    await openRowMenu(user, 'pending_user');
    const approve = await screen.findByRole('menuitem', { name: '通过' });
    expect(approve.getAttribute('aria-disabled')).toBe('true');

    releasePut?.();
    await waitFor(() =>
      expect(within(mobileCard('pending_user')).getByText('正常')).toBeTruthy(),
    );
  });

  /** 非 Error 抛出的兜底文案应使用「操作失败」，而不是「保存账号失败」。 */
  it('uses the 操作失败 fallback for a non-Error rejection', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'PUT') throw 'offline';
      return jsonResponse(accounts);
    });
    vi.stubGlobal('fetch', fetchMock);
    renderAccountsPage();

    await waitFor(() => expect(screen.getAllByText('pending_user').length).toBeGreaterThan(0));

    await openRowMenu(user, 'pending_user');
    await user.click(await screen.findByRole('menuitem', { name: '通过' }));

    await waitFor(() => expect(mocks.notifyError).toHaveBeenCalledWith('操作失败'));
  });
});

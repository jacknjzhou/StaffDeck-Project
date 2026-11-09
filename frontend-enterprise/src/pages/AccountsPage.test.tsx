// @vitest-environment jsdom

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../i18n';

import AccountsPage, { AccountRoleBadge, AccountStatusBadge } from './AccountsPage';

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

/** 每个用例结束后卸载组件、还原全局桩与 mock。 */
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
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
});

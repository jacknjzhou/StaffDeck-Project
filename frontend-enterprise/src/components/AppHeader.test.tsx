// @vitest-environment jsdom

import { createElement } from 'react';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';

import { I18nProvider } from '@/i18n';
import { ENTERPRISE_AUTH_STORAGE_KEY } from '@/auth';

import AppHeader from './AppHeader';

/** AppHeader 页头的样式透传与全局账户菜单行为。 */
describe('AppHeader', () => {
  /** 每个用例结束后卸载组件并清空本地登录态缓存。 */
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  /** 验证简单页面标题可通过 className 选用居中对齐。 */
  it('allows simple page titles to opt into centered controls', () => {
    const { container } = render(
      createElement(
        I18nProvider,
        null,
        createElement(AppHeader, {
          title: '模型',
          right: createElement('span', null, 'controls'),
          className: 'items-center',
        }),
      ),
    );
    const header = container.querySelector('header');

    expect(header?.classList.contains('items-center')).toBe(true);
    expect(header?.classList.contains('items-start')).toBe(false);
  });

  /** 验证全局账户菜单展示当前用户的全量 API 密钥与显示名。 */
  it('places the current user full-access key in the global account menu', async () => {
    window.localStorage.setItem(ENTERPRISE_AUTH_STORAGE_KEY, JSON.stringify({
      token: 'session-token',
      user: {
        id: 'user_member',
        tenant_id: 'tenant_demo',
        username: 'member',
        display_name: '普通成员',
        role: 'member',
      },
    }));
    const user = userEvent.setup();
    render(
      createElement(
        I18nProvider,
        null,
        createElement(AppHeader, { title: '账号管理' }),
      ),
    );

    await user.click(screen.getByRole('button', { name: '账户菜单' }));

    expect(await screen.findByText('API 全量密钥')).toBeTruthy();
    expect(screen.getByText('普通成员')).toBeTruthy();
  });
});

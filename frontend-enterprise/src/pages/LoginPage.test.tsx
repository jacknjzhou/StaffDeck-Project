// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ENTERPRISE_AUTH_STORAGE_KEY,
  type EnterpriseAuthSession,
} from '../auth';
import { I18nProvider } from '../i18n';

import LoginPage from './LoginPage';

const session: EnterpriseAuthSession = {
  token: 'token-1',
  user: {
    id: 'user-1',
    tenant_id: 'tenant_demo',
    username: 'admin',
    role: 'admin',
  },
};

/** 构造返回指定 JSON 内容与状态码的伪 Response 对象。 */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Unauthorized',
    text: async () => JSON.stringify(body),
  } as Response;
}

/** 在 i18n 上下文中渲染登录页，并返回登录成功回调的 mock。 */
function renderLogin(onLogin = vi.fn()) {
  render(
    <I18nProvider>
      <LoginPage onLogin={onLogin} />
    </I18nProvider>,
  );
  return onLogin;
}

/** 打开登录表单并填入账号与密码，可覆盖默认凭据。 */
async function showFormAndEnterCredentials(
  user: ReturnType<typeof userEvent.setup>,
  username = 'admin',
  password = 'secret',
) {
  await user.click(screen.getByRole('button', { name: '登录' }));
  await user.type(screen.getByLabelText('账号'), username);
  await user.type(screen.getByLabelText('密码'), password);
}

/** 每个用例结束后卸载组件、还原全局 mock 并清空本地登录态。 */
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

/** 登录页的落地页展示、密码可见性与登录提交流程。 */
describe('LoginPage', () => {
  /** 验证未点击登录前只展示落地页，点击后才出现账号与密码输入框。 */
  it('shows the original landing hero before revealing credentials', async () => {
    const user = userEvent.setup();
    renderLogin();

    expect(screen.getByText('我们来做什么？')).toBeTruthy();
    expect(screen.getByAltText('StaffDeck 产品预览')).toBeTruthy();
    expect(screen.queryByLabelText('账号')).toBeNull();

    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(screen.getByLabelText('账号')).toBeTruthy();
    expect(screen.getByLabelText('密码')).toBeTruthy();
  });

  /** 验证密码可见性按钮可在隐藏与明文之间来回切换。 */
  it('toggles the password between hidden and visible text', async () => {
    const user = userEvent.setup();
    renderLogin();
    await user.click(screen.getByRole('button', { name: '登录' }));
    const password = screen.getByLabelText('密码');

    expect(password.getAttribute('type')).toBe('password');
    await user.click(screen.getByRole('button', { name: '显示密码' }));
    expect(password.getAttribute('type')).toBe('text');
    await user.click(screen.getByRole('button', { name: '隐藏密码' }));
    expect(password.getAttribute('type')).toBe('password');
  });

  /** 验证提交前去除账号密码首尾空格，并携带默认租户发起登录请求。 */
  it('posts trimmed credentials with the configured tenant', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.fn(async () => jsonResponse(session));
    vi.stubGlobal('fetch', fetchMock);
    renderLogin();

    await showFormAndEnterCredentials(user, '  admin  ', '  secret  ');
    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith('/api/auth/login', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        tenant_id: 'tenant_demo',
        username: 'admin',
        password: 'secret',
      }),
    }));
  });

  /** 验证登录成功后写入本地会话存储，并回调通知调用方。 */
  it('stores the session and notifies the caller after a successful login', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(session)));
    const onLogin = renderLogin();

    await showFormAndEnterCredentials(user);
    await user.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => expect(onLogin).toHaveBeenCalledWith(session));
    expect(JSON.parse(window.localStorage.getItem(ENTERPRISE_AUTH_STORAGE_KEY) || 'null'))
      .toEqual(session);
  });

  it('提交注册申请后展示等待审核提示', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ status: 'pending', username: 'zhangsan' }, 201),
    ));
    renderLogin();
    await user.click(screen.getByRole('button', { name: '注册' }));
    await user.type(screen.getByLabelText('账号'), 'zhangsan');
    await user.type(screen.getByLabelText('显示名'), '张三');
    await user.type(screen.getByLabelText('密码'), 'secret123');
    await user.click(screen.getByRole('button', { name: '提交注册申请' }));
    expect(await screen.findByText('注册申请已提交，请等待管理员审核')).toBeTruthy();
  });

  it('待审核账号登录时展示审核提示而非密码错误', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      detail: { code: 'ACCOUNT_PENDING_APPROVAL', message: 'account is not approved for login' },
    }, 403)));
    renderLogin();
    await showFormAndEnterCredentials(user, 'zhangsan', 'secret123');
    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByText('账号正在等待管理员审核')).toBeTruthy();
    expect(screen.queryByText('密码输入错误')).toBeNull();
  });

  /** 验证被拒绝账号的 403 同样走提示条，而不是落进字段级「密码输入错误」。 */
  it('被拒绝账号登录时展示拒绝提示而非密码错误', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      detail: {
        code: 'ACCOUNT_REGISTRATION_REJECTED',
        message: 'account registration was rejected',
      },
    }, 403)));
    renderLogin();
    await showFormAndEnterCredentials(user, 'zhangsan', 'secret123');
    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(await screen.findByText('注册申请未通过，请联系管理员')).toBeTruthy();
    expect(screen.queryByText('密码输入错误')).toBeNull();
    expect(screen.queryByText('账号输入错误')).toBeNull();
  });
});

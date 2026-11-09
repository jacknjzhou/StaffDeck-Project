// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '@/i18n';

import ChangePasswordDialog from '../ChangePasswordDialog';

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  notifyError: vi.fn(),
  notifySuccess: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  api: { post: mocks.post },
}));

vi.mock('@/components/ui/app-toast', () => ({
  notify: {
    error: mocks.notifyError,
    success: mocks.notifySuccess,
  },
}));

beforeEach(() => {
  mocks.post.mockReset();
  mocks.notifyError.mockReset();
  mocks.notifySuccess.mockReset();
});

afterEach(cleanup);

/** 渲染带 i18n 上下文的改密弹窗（Input 内部依赖该上下文）。 */
function renderDialog(onClose = vi.fn()) {
  render(
    <I18nProvider>
      <ChangePasswordDialog open onClose={onClose} />
    </I18nProvider>,
  );
  return onClose;
}

/** 填入口令并提交表单。 */
async function submitPasswords(current: string, next: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('当前密码'), current);
  await user.type(screen.getByLabelText('新密码'), next);
  await user.click(screen.getByRole('button', { name: '确认修改' }));
  return user;
}

/** 自助改密弹窗：本地校验、服务端错误内联展示与成功后关闭。 */
describe('ChangePasswordDialog', () => {
  /** 验证新口令不足 8 位时在本地拦截，不发起请求。 */
  it('rejects a new password shorter than 8 characters before submitting', async () => {
    renderDialog();

    await submitPasswords('old-pass-123', 'short');

    expect(await screen.findByText('新密码至少 8 位')).toBeTruthy();
    expect(mocks.post).not.toHaveBeenCalled();
  });

  /** 验证 403 时在弹窗内展示当前口令错误，不关闭弹窗。 */
  it('shows an inline message when the current password is rejected', async () => {
    mocks.post.mockRejectedValue(
      Object.assign(new Error('current password is incorrect'), { status: 403 }),
    );
    const onClose = renderDialog();

    await submitPasswords('wrong-pass-1', 'new-pass-123');

    expect(await screen.findByText('当前密码不正确，请重试')).toBeTruthy();
    expect(onClose).not.toHaveBeenCalled();
  });

  /** 验证成功后调用接口并关闭弹窗。 */
  it('submits the new password and closes on success', async () => {
    mocks.post.mockResolvedValue({});
    const onClose = renderDialog();

    await submitPasswords('old-pass-123', 'new-pass-123');

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
    expect(mocks.post).toHaveBeenCalledWith('/api/auth/change-password', {
      current_password: 'old-pass-123',
      new_password: 'new-pass-123',
    });
  });
});

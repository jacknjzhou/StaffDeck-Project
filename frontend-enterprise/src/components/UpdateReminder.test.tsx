// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import UpdateReminder, { REMINDED_VERSION_KEY } from './UpdateReminder';

/** 集中声明版本查询与 toast 相关的 mock 函数，供 vi.mock 工厂引用。 */
const mocks = vi.hoisted(() => ({
  apiGet: vi.fn(),
  dismiss: vi.fn(),
  toastCustom: vi.fn(),
}));

vi.mock('@/api/client', () => ({
  api: { get: mocks.apiGet },
}));

vi.mock('sonner', () => ({
  toast: {
    custom: mocks.toastCustom,
    dismiss: mocks.dismiss,
  },
}));

vi.mock('@/i18n', () => ({
  useI18n: () => ({
    t: (key: string, values?: Record<number, string>) => (
      values
        ? Object.entries(values).reduce(
          (text, [index, value]) => text.replace(`{${index}}`, value),
          key,
        )
        : key
    ),
  }),
}));

const update = {
  current_version: '0.2.0',
  latest_version: '0.3.0',
  update_available: true,
  release_url: 'https://github.com/OpenBMB/StaffDeck/releases/tag/v0.3.0',
  check_enabled: true,
  check_succeeded: true,
};

/** 每个用例前清空本地提示记录，并重置各 mock 的调用状态。 */
beforeEach(() => {
  window.localStorage.clear();
  mocks.apiGet.mockReset();
  mocks.dismiss.mockReset();
  mocks.toastCustom.mockReset().mockReturnValue('update-toast');
});

/** 每个用例结束后卸载已渲染的组件。 */
afterEach(cleanup);

/** 更新提醒组件的触发条件、内容与去重行为。 */
describe('UpdateReminder', () => {
  /** 验证引导流程未完成（enabled 为 false）时不发起版本查询请求。 */
  it('waits until the onboarding guides are complete', () => {
    render(<UpdateReminder enabled={false} />);
    expect(mocks.apiGet).not.toHaveBeenCalled();
  });

  /** 验证有新版本时弹出提醒、记录已提示版本，并支持跳转发布页与关闭。 */
  it('shows a release link and remembers the announced version', async () => {
    mocks.apiGet.mockResolvedValue(update);
    render(<UpdateReminder enabled />);

    await waitFor(() => expect(mocks.toastCustom).toHaveBeenCalledOnce());
    expect(mocks.apiGet).toHaveBeenCalledWith('/api/app/version');
    expect(window.localStorage.getItem(REMINDED_VERSION_KEY)).toBe('0.3.0');

    const renderToast = mocks.toastCustom.mock.calls[0][0];
    render(renderToast('toast-id'));
    expect(screen.getByText('StaffDeck 有新版本')).toBeTruthy();
    expect(document.body.textContent).toContain(update.latest_version);
    expect(document.body.textContent).toContain(update.current_version);
    expect(screen.getByRole('link', { name: /查看更新/ }).getAttribute('href')).toBe(
      update.release_url,
    );

    await userEvent.click(screen.getByRole('button', { name: '关闭更新提醒' }));
    expect(mocks.dismiss).toHaveBeenCalledWith('toast-id');
  });

  /** 验证同一版本在本浏览器已提示过时不再重复提醒。 */
  it('does not repeat a version already announced in this browser', async () => {
    window.localStorage.setItem(REMINDED_VERSION_KEY, '0.3.0');
    mocks.apiGet.mockResolvedValue(update);
    render(<UpdateReminder enabled />);

    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledOnce());
    expect(mocks.toastCustom).not.toHaveBeenCalled();
  });

  /** 验证版本检查被关闭或接口请求失败时保持静默，不弹出提醒。 */
  it('stays silent when checks are disabled or unavailable', async () => {
    mocks.apiGet.mockResolvedValue({ ...update, check_enabled: false, update_available: false });
    const { rerender } = render(<UpdateReminder enabled />);
    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledOnce());
    expect(mocks.toastCustom).not.toHaveBeenCalled();

    mocks.apiGet.mockRejectedValue(new Error('offline'));
    rerender(<UpdateReminder enabled={false} />);
    rerender(<UpdateReminder enabled />);
    await waitFor(() => expect(mocks.apiGet).toHaveBeenCalledTimes(2));
    expect(mocks.toastCustom).not.toHaveBeenCalled();
  });
});

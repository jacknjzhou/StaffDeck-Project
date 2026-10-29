// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest';

import { copyTextToClipboard } from './clipboard';

const originalClipboard = navigator.clipboard;
const originalExecCommand = document.execCommand;

/** 覆盖 navigator.clipboard 以模拟不同的剪贴板能力。 */
function setClipboard(value: Clipboard | undefined) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value });
}

/** 覆盖 document.execCommand 以模拟旧版复制通道。 */
function setExecCommand(value: typeof document.execCommand | undefined) {
  Object.defineProperty(document, 'execCommand', { configurable: true, value });
}

/** 构造复制时派发 copy 事件并写入剪贴板数据的 execCommand。 */
function copyEventExecCommand() {
  const setData = vi.fn();
  const execCommand = vi.fn((command: string) => {
    if (command !== 'copy') return false;
    const event = new Event('copy', { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'clipboardData', { value: { setData } });
    document.dispatchEvent(event);
    return true;
  });
  return { execCommand, setData };
}

afterEach(() => {
  setClipboard(originalClipboard);
  setExecCommand(originalExecCommand);
  document.body.replaceChildren();
  vi.restoreAllMocks();
});

/** 验证复制文本到剪贴板的多级降级策略。 */
describe('copyTextToClipboard', () => {
  /** 验证存在 Clipboard API 时优先使用 writeText。 */
  it('uses the Clipboard API when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const execCommand = vi.fn(() => true);
    setClipboard({ writeText } as unknown as Clipboard);
    setExecCommand(execCommand);

    await copyTextToClipboard('hello');

    expect(writeText).toHaveBeenCalledWith('hello');
    expect(execCommand).not.toHaveBeenCalled();
  });

  /** 验证 Clipboard API 拒绝时降级到临时 textarea 选中复制。 */
  it('falls back to a selected textarea when Clipboard API rejects', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('permission denied'));
    const { execCommand, setData } = copyEventExecCommand();
    const input = document.createElement('input');
    document.body.appendChild(input);
    input.focus();
    setClipboard({ writeText } as unknown as Clipboard);
    setExecCommand(execCommand);

    await copyTextToClipboard('fallback text');

    expect(execCommand).toHaveBeenCalledWith('copy');
    expect(setData).toHaveBeenCalledWith('text/plain', 'fallback text');
    expect(document.activeElement).toBe(input);
    expect(document.querySelector('textarea')).toBeNull();
  });

  /** 验证 execCommand 未派发 copy 事件时不会误报成功。 */
  it('does not report success when execCommand emits no copy event', async () => {
    setClipboard(undefined);
    setExecCommand(vi.fn(() => true));

    await expect(copyTextToClipboard('not actually copied')).rejects.toThrow(
      'Clipboard access is unavailable',
    );
  });

  /** 验证两种复制方式均失败时抛出统一错误。 */
  it('reports failure when neither copy method succeeds', async () => {
    setClipboard(undefined);
    setExecCommand(vi.fn(() => false));

    await expect(copyTextToClipboard('unavailable')).rejects.toThrow('Clipboard access is unavailable');
  });
});

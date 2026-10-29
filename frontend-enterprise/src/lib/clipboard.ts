/**
 * 降级复制实现：用隐藏 textarea 选中文本后调用 document.execCommand('copy')。
 *
 * 会监听 copy 事件确认浏览器真的执行了复制，并在结束后恢复原有选区与焦点元素。
 * @param text 待复制的纯文本。
 * @returns 复制并确认成功时为 true；环境不支持或未收到 copy 事件时为 false。
 */
function copyWithSelection(text: string): boolean {
  if (typeof document === 'undefined' || !document.body || typeof document.execCommand !== 'function') {
    return false;
  }

  const activeElement = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const selection = document.getSelection();
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, index) => selection.getRangeAt(index).cloneRange())
    : [];
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.style.position = 'fixed';
  textarea.style.left = '0';
  textarea.style.top = '0';
  textarea.style.width = '1px';
  textarea.style.height = '1px';
  textarea.style.padding = '0';
  textarea.style.border = '0';
  textarea.style.opacity = '0.01';
  textarea.style.pointerEvents = 'none';
  document.body.appendChild(textarea);

  // Some restricted HTTP browsers return true from execCommand without
  // emitting a real copy operation. Supplying the payload through the copy
  // event improves compatibility and prevents a false-success result.
  let copyEventSeen = false;
  /** 复制事件处理器：写入剪贴板数据并阻止默认行为，同时标记事件已触发。 */
  const handleCopy = (event: ClipboardEvent) => {
    copyEventSeen = true;
    if (!event.clipboardData) return;
    event.clipboardData.setData('text/plain', text);
    event.preventDefault();
  };
  document.addEventListener('copy', handleCopy, true);

  try {
    textarea.focus();
    textarea.select();
    textarea.setSelectionRange(0, textarea.value.length);
    return document.execCommand('copy') && copyEventSeen;
  } finally {
    document.removeEventListener('copy', handleCopy, true);
    textarea.remove();
    if (selection) {
      selection.removeAllRanges();
      ranges.forEach((range) => selection.addRange(range));
    }
    activeElement?.focus();
  }
}

/**
 * 在浏览器、桌面 WebView 及非安全（HTTP）本地部署环境下复制文本。
 *
 * 优先使用 navigator.clipboard.writeText；不可用或被权限拒绝时退回 execCommand 方案。
 * @param text 待复制的纯文本。
 * @returns 复制成功时正常返回，两种方案均失败时抛出错误。
 */
export async function copyTextToClipboard(text: string): Promise<void> {
  const clipboard = typeof navigator === 'undefined' ? undefined : navigator.clipboard;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return;
    } catch {
      // Some desktop webviews expose Clipboard API but reject it due to permissions.
    }
  }

  if (!copyWithSelection(text)) {
    throw new Error('Clipboard access is unavailable');
  }
}

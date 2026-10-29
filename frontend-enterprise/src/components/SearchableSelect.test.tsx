// @vitest-environment jsdom

import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import SearchableSelect from './SearchableSelect';
import { I18nProvider } from '@/i18n';

const options = [
  { value: 'u1', label: '张三', keywords: ['zhangsan'] },
  { value: 'u2', label: '李四', keywords: ['lisi'] },
  { value: 'u3', label: '王五', keywords: ['wangwu'] },
];

/** 在 i18n 上下文中渲染可搜索下拉框，并返回选中回调的 mock 与渲染结果。 */
function renderSelect(props: Partial<React.ComponentProps<typeof SearchableSelect>> = {}) {
  const onValueChange = vi.fn();
  return {
    onValueChange,
    ...render(
      <I18nProvider>
        <SearchableSelect
          value=""
          onValueChange={onValueChange}
          options={options}
          placeholder="选择内部成员"
          searchPlaceholder="搜索成员"
          emptyText="无匹配成员"
          {...props}
        />
      </I18nProvider>,
    ),
  };
}

/** 每个用例结束后卸载已渲染的组件。 */
afterEach(() => {
  cleanup();
});

/** 可搜索下拉框的展开、过滤、键盘操作与已选值展示。 */
describe('SearchableSelect', () => {
  /** 验证展开后聚焦搜索框、按名称过滤，选中后回传值并关闭列表。 */
  it('opens the list, focuses search and filters options by label', async () => {
    const user = userEvent.setup();
    const { onValueChange } = renderSelect();

    await user.click(screen.getByRole('combobox'));
    const input = await screen.findByPlaceholderText('搜索成员');
    expect(document.activeElement).toBe(input);
    expect(await screen.findByRole('option', { name: /张三/ })).toBeTruthy();

    await user.type(input, '李');
    await waitFor(() => {
      expect(screen.queryByRole('option', { name: /张三/ })).toBeNull();
    });
    expect(screen.getByRole('option', { name: /李四/ })).toBeTruthy();

    await user.click(screen.getByRole('option', { name: /李四/ }));
    expect(onValueChange).toHaveBeenCalledWith('u2');
    // 选中后关闭
    await waitFor(() => {
      expect(screen.queryByRole('option', { name: /李四/ })).toBeNull();
    });
  });

  /** 验证支持按关键字搜索，无匹配时展示空文案且不渲染任何选项。 */
  it('filters by keywords and shows empty text when nothing matches', async () => {
    const user = userEvent.setup();
    renderSelect();

    await user.click(screen.getByRole('combobox'));
    const input = await screen.findByPlaceholderText('搜索成员');
    await user.type(input, 'wangwu');
    expect(screen.getByRole('option', { name: /王五/ })).toBeTruthy();

    await user.clear(input);
    await user.type(input, '不存在');
    expect(screen.getByText('无匹配成员')).toBeTruthy();
    expect(screen.queryByRole('option')).toBeNull();
  });

  /** 验证键盘输入后按回车即可选中首个匹配项。 */
  it('supports keyboard navigation and Enter to select', async () => {
    const user = userEvent.setup();
    const { onValueChange } = renderSelect();

    await user.click(screen.getByRole('combobox'));
    const input = await screen.findByPlaceholderText('搜索成员');
    await user.type(input, '张');
    await user.keyboard('{Enter}');
    expect(onValueChange).toHaveBeenCalledWith('u1');
  });

  /** 验证已选值时触发器上展示对应的选项名称。 */
  it('shows the selected label on the trigger', () => {
    renderSelect({ value: 'u2' });
    expect(screen.getByRole('combobox').textContent).toContain('李四');
  });
});

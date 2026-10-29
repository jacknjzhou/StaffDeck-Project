// @vitest-environment jsdom

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import PlatformEmployeeCard from './PlatformEmployeeCard';

/** 渲染一张带统计信息的平台员工卡片，并返回打开详情回调的 mock。 */
function renderCard(onUnpublish?: () => void) {
  const onOpen = vi.fn();
  render(
    <PlatformEmployeeCard
      avatar={<span>avatar</span>}
      name="Finance Employee"
      role="Finance"
      description="Handles finance workflows"
      stats={[{ label: 'SOP', value: 2 }]}
      onOpen={onOpen}
      onUnpublish={onUnpublish}
    />,
  );
  return { onOpen };
}

/** 平台员工卡片在广场治理场景下的下线操作行为。 */
describe('PlatformEmployeeCard gallery governance', () => {
  /** 验证未传入管理员下线回调时不展示「从广场下线」按钮。 */
  it('does not expose the unpublish action without admin callback', () => {
    renderCard();
    expect(screen.queryByRole('button', { name: '从广场下线' })).toBeNull();
  });

  /** 验证点击下线按钮只触发下线回调，不会打开员工详情。 */
  it('runs the unpublish action without opening employee details', async () => {
    const user = userEvent.setup();
    const onUnpublish = vi.fn();
    const { onOpen } = renderCard(onUnpublish);

    await user.click(screen.getByRole('button', { name: '从广场下线' }));

    expect(onUnpublish).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
  });
});

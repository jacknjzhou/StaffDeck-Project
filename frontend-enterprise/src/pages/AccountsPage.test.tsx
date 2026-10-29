import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { AccountRoleBadge } from './AccountsPage';

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

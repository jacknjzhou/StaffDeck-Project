import { describe, expect, it } from 'vitest';

import {
  canDeleteBinding,
  canManageBinding,
  getChannelPresentation,
  ROLE_LABEL,
} from './channelPresentation';

/** 渠道展示文案的解析逻辑。 */
describe('channel presentation', () => {
  /** 验证各渠道使用其真实的名称、用户称谓与标识字段名。 */
  it.each([
    ['wechat', '微信', '微信用户', '机器人 ID'],
    ['wecom', '企业微信', '企业微信用户', '机器人 ID'],
    ['feishu', '飞书', '飞书用户', 'App ID'],
  ])('%s uses its actual channel labels', (channel, name, userLabel, identifierLabel) => {
    const presentation = getChannelPresentation(channel);
    expect(presentation.name).toBe(name);
    expect(presentation.userLabel).toBe(userLabel);
    expect(presentation.identifierLabel).toBe(identifierLabel);
    expect(presentation.disconnectDescription).toContain(name);
  });

  /** 验证新接入渠道优先使用自定义配置的渠道名称生成文案。 */
  it('prefers the configured channel name for newly added channels', () => {
    const presentation = getChannelPresentation('custom', '内部协作');
    expect(presentation.name).toBe('内部协作');
    expect(presentation.userLabel).toBe('内部协作用户');
    expect(presentation.blurb).toBe('通过内部协作与数字员工对话。');
  });
});

/** 渠道绑定管理中的角色权限判定。 */
describe('channel binding manager roles', () => {
  /** 验证仅管理员与拥有者可以删除渠道绑定。 */
  it.each([
    ['admin', true],
    ['owner', true],
    ['collaborator', false],
    [null, false],
    [undefined, false],
  ])('my_role=%s can delete binding? %s', (role, expected) => {
    expect(canDeleteBinding({ my_role: (role as string | null | undefined) ?? null })).toBe(expected);
  });

  /** 验证管理员、拥有者与协作者均可管理渠道绑定。 */
  it.each([
    ['admin', true],
    ['owner', true],
    ['collaborator', true],
    [null, false],
    [undefined, false],
  ])('my_role=%s can manage binding? %s', (role, expected) => {
    expect(canManageBinding({ my_role: (role as string | null | undefined) ?? null })).toBe(expected);
  });

  /** 验证已知角色的展示名称映射。 */
  it('exposes role labels for known roles', () => {
    expect(ROLE_LABEL.admin).toBe('管理员');
    expect(ROLE_LABEL.owner).toBe('拥有者');
    expect(ROLE_LABEL.collaborator).toBe('协作者');
  });
});

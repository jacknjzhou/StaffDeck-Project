import { describe, expect, it } from 'vitest';

import { formatHandoffAssigneeValue, parseHandoffAssigneeValue } from './handoff-assignee';

/** 移交负责人取值的格式化逻辑。 */
describe('formatHandoffAssigneeValue', () => {
  /** 验证未指定用户时格式化为空字符串。 */
  it('returns empty for missing user', () => {
    expect(formatHandoffAssigneeValue(null, null)).toBe('');
    expect(formatHandoffAssigneeValue('', '')).toBe('');
  });

  /** 验证 Web 渠道只输出纯用户 ID。 */
  it('returns plain user id for web delivery', () => {
    expect(formatHandoffAssigneeValue('user_1', null)).toBe('user_1');
    expect(formatHandoffAssigneeValue('user_1', 'web')).toBe('user_1');
  });

  /** 验证渠道投递时在用户 ID 后追加渠道后缀。 */
  it('appends channel for channel delivery', () => {
    expect(formatHandoffAssigneeValue('user_1', 'feishu')).toBe('user_1::feishu');
  });
});

/** 移交负责人取值的解析逻辑。 */
describe('parseHandoffAssigneeValue', () => {
  /** 验证空值与空白值解析为未指派。 */
  it('parses empty value as unassigned', () => {
    expect(parseHandoffAssigneeValue('')).toEqual({ userId: '', channel: null });
    expect(parseHandoffAssigneeValue('   ')).toEqual({ userId: '', channel: null });
  });

  /** 验证纯用户 ID 解析为 Web 渠道投递。 */
  it('parses plain user id as web delivery', () => {
    expect(parseHandoffAssigneeValue('user_1')).toEqual({ userId: 'user_1', channel: 'web' });
  });

  /** 验证复合取值被拆分为用户 ID 与渠道。 */
  it('parses composite value into user and channel', () => {
    expect(parseHandoffAssigneeValue('user_1::feishu')).toEqual({
      userId: 'user_1',
      channel: 'feishu',
    });
  });

  /** 验证渠道片段缺失或为空时回退到 Web 渠道。 */
  it('falls back to web when channel segment is malformed', () => {
    expect(parseHandoffAssigneeValue('user_1::')).toEqual({ userId: 'user_1::', channel: 'web' });
    expect(parseHandoffAssigneeValue('::feishu')).toEqual({ userId: '::feishu', channel: 'web' });
  });

  /** 验证解析与格式化可以相互往返。 */
  it('round-trips with formatHandoffAssigneeValue', () => {
    expect(parseHandoffAssigneeValue(formatHandoffAssigneeValue('user_1', 'feishu'))).toEqual({
      userId: 'user_1',
      channel: 'feishu',
    });
    expect(parseHandoffAssigneeValue(formatHandoffAssigneeValue('user_1', null))).toEqual({
      userId: 'user_1',
      channel: 'web',
    });
  });
});

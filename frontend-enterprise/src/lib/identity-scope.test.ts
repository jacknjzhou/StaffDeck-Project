import { describe, expect, it } from 'vitest';

import { feishuAppIdFromIdentityScope } from './identity-scope';

/** 验证从飞书身份作用域中解析应用 ID 的规则。 */
describe('feishuAppIdFromIdentityScope', () => {
  /** 验证能从标准飞书身份作用域中提取出应用 ID。 */
  it('extracts the app id from a feishu identity scope', () => {
    expect(
      feishuAppIdFromIdentityScope('app:20:cli_aaf3d15c5138dbe5:tenant:16:1a0aaaa3801ddcbc'),
    ).toBe('cli_aaf3d15c5138dbe5');
  });

  /** 验证不同编码长度的应用 ID 与租户键都能被正确解析。 */
  it('supports app ids and tenant keys of any encoded length', () => {
    expect(feishuAppIdFromIdentityScope('app:3:abc:tenant:2:xy')).toBe('abc');
  });

  /** 验证非飞书来源的作用域一律返回 null。 */
  it('returns null for non-feishu scopes', () => {
    expect(feishuAppIdFromIdentityScope('')).toBeNull();
    expect(feishuAppIdFromIdentityScope('corpA')).toBeNull();
    expect(feishuAppIdFromIdentityScope('wecom:corp:5:corpA:bot:4:bot1')).toBeNull();
    expect(feishuAppIdFromIdentityScope('binding_123')).toBeNull();
  });

  /** 验证格式不合法的飞书作用域返回 null 而不抛错。 */
  it('returns null for malformed feishu scopes', () => {
    expect(feishuAppIdFromIdentityScope('app:20:short:tenant:16:1a0aaaa3801ddcbc')).toBeNull();
    expect(feishuAppIdFromIdentityScope('app:xx:cli_aaf3d15c5138dbe5:tenant:16:1a0aaaa3801ddcbc')).toBeNull();
    expect(feishuAppIdFromIdentityScope('app:20:cli_aaf3d15c5138dbe5:corp:16:1a0aaaa3801ddcbc')).toBeNull();
    expect(feishuAppIdFromIdentityScope('app:')).toBeNull();
  });
});

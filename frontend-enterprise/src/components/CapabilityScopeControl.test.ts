import { describe, expect, it } from 'vitest';

import { capabilityScopeLabel, normalizeCapabilityScope } from './CapabilityScopeControl';

/** 验证能力作用域的归一化与展示文案。 */
describe('capability scope helpers', () => {
  /** 验证规范线值 `sop_specific` 会被原样返回。 */
  it('keeps the canonical SOP-specific wire value', () => {
    expect(normalizeCapabilityScope('sop_specific')).toBe('sop_specific');
  });

  /** 验证标签风格的旧拼写 `sop-specific` 会被归一化为规范值。 */
  it('accepts the label-style legacy spelling', () => {
    expect(normalizeCapabilityScope('sop-specific')).toBe('sop_specific');
  });

  /** 验证缺失或未知取值回退为 `general`，标签回退为“通用”。 */
  it('defaults missing or unknown values to general', () => {
    expect(normalizeCapabilityScope(undefined)).toBe('general');
    expect(normalizeCapabilityScope('unknown')).toBe('general');
    expect(capabilityScopeLabel(undefined)).toBe('通用');
  });
});

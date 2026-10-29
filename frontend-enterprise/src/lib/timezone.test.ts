import { describe, expect, it } from 'vitest';

import { formatClientDateTime, parseBackendDateTime } from './timezone';

/** 后端时间字符串的解析规则。 */
describe('parseBackendDateTime', () => {
  /** 验证不带时区的 ISO 时间按 UTC 解析，而非本地时间。 */
  it('treats naive ISO timestamps as UTC instead of local time', () => {
    const parsed = parseBackendDateTime('2026-08-11T09:00:00');
    expect(parsed.getTime()).toBe(Date.UTC(2026, 7, 11, 9, 0, 0));
  });

  /** 验证已带 Z 或时区偏移的时间戳保持其绝对时刻不变。 */
  it('keeps timestamps that already carry a timezone suffix', () => {
    const withZ = parseBackendDateTime('2026-08-11T09:00:00Z');
    expect(withZ.getTime()).toBe(Date.UTC(2026, 7, 11, 9, 0, 0));
    const withOffset = parseBackendDateTime('2026-08-11T17:00:00+08:00');
    expect(withOffset.getTime()).toBe(Date.UTC(2026, 7, 11, 9, 0, 0));
  });

  /** 验证仅日期字符串按 UTC 解析，且不会被补上时间后缀。 */
  it('parses date-only strings as UTC without appending a suffix', () => {
    const parsed = parseBackendDateTime('2026-08-11');
    expect(parsed.getTime()).toBe(Date.UTC(2026, 7, 11));
  });

  /** 验证空串与非法字符串都解析为无效日期。 */
  it('returns an invalid date for empty or malformed input', () => {
    expect(Number.isNaN(parseBackendDateTime('').getTime())).toBe(true);
    expect(Number.isNaN(parseBackendDateTime('not-a-date').getTime())).toBe(true);
  });
});

/** 客户端时区的时间格式化规则。 */
describe('formatClientDateTime', () => {
  /** 验证不带时区的 UTC 时间戳会转换为客户端时区展示。 */
  it('renders naive UTC timestamps in the client timezone', () => {
    const expected = new Date(Date.UTC(2026, 7, 11, 9, 0, 0)).toLocaleString('zh-CN', {
      hour12: false,
    });
    expect(formatClientDateTime('2026-08-11T09:00:00')).toBe(expected);
  });

  /** 验证缺失或非法输入时回退到指定的兜底文本。 */
  it('falls back to the empty text for missing values', () => {
    expect(formatClientDateTime(undefined)).toBe('-');
    expect(formatClientDateTime('bad', '')).toBe('');
  });
});

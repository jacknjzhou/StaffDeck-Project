import { getDateLocale } from '@/i18n';

const FALLBACK_TIME_ZONE = 'Asia/Shanghai';

/**
 * 读取当前运行环境的 IANA 时区,读取失败时回退到 Asia/Shanghai。
 */
export function getClientTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || FALLBACK_TIME_ZONE;
  } catch {
    return FALLBACK_TIME_ZONE;
  }
}

/**
 * 将后端返回的时间字符串解析为 Date。
 *
 * - 空值返回 Invalid Date,由调用方负责兜底展示;
 * - 已带 Z 或时区偏移的字符串原样解析,naive UTC 时间戳补 `Z` 后缀按 UTC 处理。
 */
export function parseBackendDateTime(value?: string): Date {
  const text = String(value || '').trim();
  if (!text) return new Date('');
  // 纯日期字符串本就被当作 UTC 解析，无需补时区后缀
  if (!text.includes('T')) return new Date(text);
  // 后端时间戳为 naive UTC（无 Z 后缀），缺失时按 UTC 解析而非本地时间
  if (/([zZ]|[+-]\d{2}:\d{2})$/.test(text)) return new Date(text);
  return new Date(`${text}Z`);
}

/**
 * 按客户端时区把后端时间格式化为本地日期时间文本。
 *
 * @param value 后端返回的时间字符串,空值或非法时间时返回 emptyText
 * @param emptyText 无有效时间时的占位文案,默认 "-"
 * @returns 24 小时制的本地化日期时间字符串
 */
export function formatClientDateTime(value?: string, emptyText = '-'): string {
  if (!value) return emptyText;
  const date = parseBackendDateTime(value);
  if (Number.isNaN(date.getTime())) return emptyText;
  return date.toLocaleString(getDateLocale(), {
    hour12: false,
    timeZone: getClientTimeZone(),
  });
}

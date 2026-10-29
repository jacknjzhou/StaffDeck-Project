export type DistillFailure = {
  code?: string;
  detail: string;
  stage: string;
  summary: string;
};

const GENERIC_FAILURE_DETAILS = new Set([
  '',
  '生成失败',
  '生成失败，当前草稿未变更。',
  '改写失败',
  '改写失败，当前草稿未变更。',
]);

/**
 * 归一化 SOP 草稿生成或局部改写失败时的错误信息。
 *
 * @param error 原始错误，可为 Error、字符串或其它未知值。
 * @param kind 失败场景：distill 为草稿生成，rewrite 为局部改写。
 * @returns 含 code/detail/stage/summary 的失败描述；通用文案会替换为排查提示。
 */
export function buildDistillFailure(
  error: unknown,
  kind: 'distill' | 'rewrite',
): DistillFailure {
  const rawDetail = error instanceof Error ? error.message : String(error || '').trim();
  const stage = kind === 'distill' ? 'SOP 草稿生成' : 'SOP 局部改写';
  const summary = kind === 'distill' ? '生成失败' : '改写失败';
  const detail = GENERIC_FAILURE_DETAILS.has(rawDetail)
    ? '上游没有返回详细错误信息，请检查所选模型配置及服务日志。'
    : rawDetail;

  return {
    code: extractFailureCode(detail),
    detail,
    stage,
    summary,
  };
}

/**
 * 从错误详情文本中提取稳定错误码，供界面展示与排查。
 *
 * 依次尝试显式 code 字段、request/provider failed (...) 括号内容与全大写稳定码。
 *
 * @param detail 错误详情文本。
 * @returns 命中的错误码；无法识别时返回 undefined。
 */
function extractFailureCode(detail: string): string | undefined {
  const explicit = detail.match(/(?:error[_ ]?code|provider_code|code)\s*[=:]\s*['"]?([\w.-]+)/i);
  if (explicit?.[1]) return explicit[1];

  const parenthesized = detail.match(/(?:request|provider)\s+failed\s+\(([^)]+)\)/i);
  if (parenthesized?.[1]) return parenthesized[1].trim();

  const stableCode = detail.match(/\b([A-Z][A-Z0-9_]{3,})\b/);
  return stableCode?.[1];
}

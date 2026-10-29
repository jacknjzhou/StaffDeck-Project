import { describe, expect, it } from 'vitest';

import { apiErrorMessage } from './apiErrorMessages';

/** 验证接口错误信息的本地化与未知错误码包装规则。 */
describe('apiErrorMessage', () => {
  /** 验证以原始字符串传入的已知稳定错误码会被本地化为中文提示。 */
  it('localizes a known stable error code passed as a raw string', () => {
    expect(apiErrorMessage('MODEL_PROTOCOL_OPTIONS_INVALID', '保存失败')).toBe(
      '模型协议选项无效，请检查 API 协议与协议参数',
    );
  });

  /** 验证未知稳定错误码会被包装，普通上游诊断信息保持原样。 */
  it('wraps an unknown stable error code without rewriting normal diagnostics', () => {
    expect(apiErrorMessage('SOME_NEW_BACKEND_FAILURE', '操作失败')).toBe(
      '操作失败（错误码：SOME_NEW_BACKEND_FAILURE）',
    );
    expect(apiErrorMessage('upstream request failed: timeout', '操作失败')).toBe(
      'upstream request failed: timeout',
    );
  });
});

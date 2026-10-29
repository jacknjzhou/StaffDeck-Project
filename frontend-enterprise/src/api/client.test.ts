import { describe, expect, it } from 'vitest';

import { ApiError } from './client';

/** ApiError 对后端错误响应的解析与消息提取。 */
describe('ApiError', () => {
  /** 验证结构化错误码与可读提示会被保留在 ApiError 上。 */
  it('preserves a structured backend error code and human-readable message', () => {
    const error = new ApiError(404, JSON.stringify({
      detail: {
        code: 'EVOLUTION_FEEDBACK_NOT_FOUND',
        message: '未找到可用于改进的 Skill 或 SOP 反馈',
      },
    }), 'Not Found');

    expect(error.code).toBe('EVOLUTION_FEEDBACK_NOT_FOUND');
    expect(error.message).toBe('未找到可用于改进的 Skill 或 SOP 反馈');
  });

  /** 验证 422 校验错误详情仍按 loc: msg 的形式拼接为可读消息。 */
  it('keeps validation detail formatting compatible', () => {
    const error = new ApiError(422, JSON.stringify({
      detail: [{ loc: ['body', 'name'], msg: 'Field required' }],
    }), 'Unprocessable Entity');

    expect(error.code).toBeUndefined();
    expect(error.message).toBe('body.name: Field required');
  });

  /** 验证字符串形式的 detail 会被识别为稳定错误码。 */
  it('recognizes a stable error code returned as a string detail', () => {
    const error = new ApiError(422, JSON.stringify({
      detail: 'MODEL_PROTOCOL_OPTIONS_INVALID',
    }), 'Unprocessable Entity');

    expect(error.code).toBe('MODEL_PROTOCOL_OPTIONS_INVALID');
    expect(error.message).toBe('MODEL_PROTOCOL_OPTIONS_INVALID');
  });
});

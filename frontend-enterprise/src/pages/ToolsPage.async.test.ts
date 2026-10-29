import { describe, expect, it } from 'vitest';

import { buildToolPayload, TOOL_FORM_INITIAL_VALUES } from './ToolsPage';

/** 构造一份带 HTTP 提供方地址的工具表单初始值。 */
function values() {
  return {
    ...TOOL_FORM_INITIAL_VALUES,
    name: 'orders.submit',
    display_name: '提交订单',
    description: '提交订单任务',
    allowed_skills: '',
    url: 'https://provider.test/tasks',
  };
}

/** 分离式（detached）HTTP 工具的执行策略序列化规则。 */
describe('detached HTTP tool configuration', () => {
  /** 验证提供方托管任务会保留状态 URL 与任务 ID 字段等跟踪配置。 */
  it('keeps provider tracking fields for provider-managed tasks', () => {
    const payload = buildToolPayload({
      ...values(),
      execution_mode: 'detached',
      async_strategy: 'provider_task',
      status_url: 'https://provider.test/tasks/{taskId}',
    });

    expect(payload?.execution_policy).toMatchObject({
      execution_mode: 'detached',
      async_strategy: 'provider_task',
      status_url: 'https://provider.test/tasks/{taskId}',
      task_id_field: 'taskId',
    });
  });

  /** 验证 StaffDeck 托管任务不会持久化过期的提供方状态 URL。 */
  it('does not persist provider status URLs for StaffDeck-managed tasks', () => {
    const payload = buildToolPayload({
      ...values(),
      execution_mode: 'detached',
      async_strategy: 'staffdeck_worker',
      status_url: 'https://stale.example/tasks/{taskId}',
    });

    expect(payload?.execution_policy).toMatchObject({
      execution_mode: 'detached',
      async_strategy: 'staffdeck_worker',
      status_url: null,
    });
  });
});

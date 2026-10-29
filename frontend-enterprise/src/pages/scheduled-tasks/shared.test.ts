import { describe, expect, it } from 'vitest';

import type { ScheduledTaskRead, ScheduledTaskRunRead } from '../../types';
import {
  matchesRunFilter,
  RUN_STATUS_BADGE,
  scheduledTaskSopOptions,
  taskToFormValues,
} from './shared';

/** 构造一条指定运行状态的定时任务执行记录。 */
function run(status: string): ScheduledTaskRunRead {
  return {
    id: `run-${status}`,
    tenant_id: 'tenant-demo',
    scheduled_task_id: 'scheduled-1',
    agent_id: 'agent-1',
    user_id: 'user-1',
    scheduled_for: '2026-08-01T09:00:00',
    status,
    trace: {},
    created_at: '2026-08-01T09:00:00',
    updated_at: '2026-08-01T09:00:00',
  };
}

/** 定时任务在 Harness 各运行状态下的筛选归属与状态文案。 */
describe('scheduled task Harness statuses', () => {
  /** 验证排队、运行中、待补充信息与未完成均归入“待处理”筛选。 */
  it.each(['queued', 'running', 'needs_input', 'incomplete'])(
    'keeps %s in the pending filter',
    (status) => {
      expect(matchesRunFilter(run(status), 'pending')).toBe(true);
    },
  );

  /** 验证非终态的 Harness 结果拥有明确的中文状态文案。 */
  it('presents non-terminal Harness outcomes explicitly', () => {
    expect(RUN_STATUS_BADGE.needs_input.text).toBe('待补充信息');
    expect(RUN_STATUS_BADGE.incomplete.text).toBe('未完成');
  });
});

/** 定时任务的 SOP 选项过滤与表单值还原。 */
describe('scheduled task SOP selection', () => {
  /** 验证显式指定 SOP 专属的 SOP 可选，草稿状态的 SOP 被排除。 */
  it('allows explicitly selected SOP-specific SOPs while excluding drafts', () => {
    const rows = [
      { id: 'general', status: 'published', capability_scope: 'general' },
      { id: 'specific', status: 'published', capability_scope: 'sop_specific' },
      { id: 'draft', status: 'draft', capability_scope: 'general' },
    ];

    expect(scheduledTaskSopOptions(rows).map((row) => row.id)).toEqual([
      'general',
      'specific',
    ]);
  });

  /** 验证从任务元数据中还原被固定版本的 Harness v2 SOP。 */
  it('restores the pinned Harness v2 SOP from task metadata', () => {
    const task = {
      id: 'scheduled-1',
      tenant_id: 'tenant-demo',
      agent_id: 'agent-1',
      created_by_user_id: 'user-1',
      title: '日报',
      prompt: '生成日报',
      schedule_type: 'daily',
      schedule: { time: '09:00' },
      timezone: 'Asia/Shanghai',
      status: 'active',
      concurrency_policy: 'forbid',
      misfire_policy: 'coalesce',
      run_count: 0,
      metadata: {
        sop_id: 'daily_report_v2',
        sop_version_policy: 'pinned',
        sop_version: '1.0.0',
      },
      created_at: '2026-08-01T09:00:00',
      updated_at: '2026-08-01T09:00:00',
    } satisfies ScheduledTaskRead;

    expect(taskToFormValues(task).sop_id).toBe('daily_report_v2');
    expect(taskToFormValues(task).sop_version_policy).toBe('pinned');
  });

  /** 验证缺少版本策略的存量任务默认使用最新已发布 SOP。 */
  it('defaults existing tasks to the latest published SOP policy', () => {
    const task = {
      id: 'scheduled-2',
      tenant_id: 'tenant-demo',
      agent_id: 'agent-1',
      created_by_user_id: 'user-1',
      title: '日报',
      prompt: '生成日报',
      schedule_type: 'daily',
      schedule: { time: '09:00' },
      timezone: 'Asia/Shanghai',
      status: 'active',
      concurrency_policy: 'forbid',
      misfire_policy: 'coalesce',
      run_count: 0,
      metadata: { sop_id: 'daily_report_v2' },
      created_at: '2026-08-01T09:00:00',
      updated_at: '2026-08-01T09:00:00',
    } satisfies ScheduledTaskRead;

    expect(taskToFormValues(task).sop_version_policy).toBe('latest');
  });
});

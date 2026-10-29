import { describe, expect, it } from 'vitest';

import type { AgentProfileRead, ChatSession } from '@/types';

import { buildSessionFilterOptions } from './sessionFilterOptions';

/** 构造一个数字员工档案，供筛选选项测试使用。 */
function agent(id: string, name: string): AgentProfileRead {
  return {
    id,
    tenant_id: 'tenant-demo',
    name,
    is_overall: false,
    status: 'active',
    metadata: {},
    resources: [],
    created_at: '2026-08-02T00:00:00Z',
    updated_at: '2026-08-02T00:00:00Z',
  };
}

/** 构造一条已完成会话，指定其所属的数字员工。 */
function session(id: string, agentId: string): ChatSession {
  return {
    id,
    tenant_id: 'tenant-demo',
    agent_id: agentId,
    status: 'completed',
    updated_at: '2026-08-02T00:00:00Z',
  };
}

/** 会话筛选下拉选项的构建逻辑。 */
describe('chat session filter options', () => {
  /** 验证保留聚合选项、排除无会话员工，且不改变入参员工顺序。 */
  it('keeps the aggregate option and excludes agents with no sessions', () => {
    const agents = [agent('agent-c', 'Gamma'), agent('agent-b', 'Beta'), agent('agent-a', 'Alpha')];
    const originalOrder = agents.map((item) => item.id);
    const sessions = [
      session('session-a1', 'agent-a'),
      session('session-a2', 'agent-a'),
      session('session-b1', 'agent-b'),
    ];

    expect(buildSessionFilterOptions(agents, sessions)).toEqual([
      { value: 'all', label: '全部会话 · 3' },
      { value: 'agent-a', label: 'Alpha · 2' },
      { value: 'agent-b', label: 'Beta · 1' },
    ]);
    expect(agents.map((item) => item.id)).toEqual(originalOrder);
  });

  /** 验证当前选中的草稿员工可被选择，且不显示计数为 0 的标签。 */
  it('keeps an active draft selectable without showing a zero count', () => {
    const options = buildSessionFilterOptions(
      [agent('agent-a', 'Alpha'), agent('agent-draft', 'Draft Agent')],
      [session('session-a1', 'agent-a')],
      'agent-draft',
    );

    expect(options).toContainEqual({ value: 'agent-draft', label: 'Draft Agent' });
    expect(options.some((option) => option.label.includes('· 0'))).toBe(false);
  });

  /** 验证团队会话按团队单独计数，不并入项目负责人的员工计数。 */
  it('counts team groups separately from the project leader employee', () => {
    const group = {
      ...session('session-team', 'agent-a'),
      team_id: 'team-1',
      team_name: '增长团队',
    };

    expect(buildSessionFilterOptions([agent('agent-a', 'Alpha')], [group])).toEqual([
      { value: 'all', label: '全部会话 · 1' },
      { value: 'team:team-1', label: '增长团队 · 1' },
    ]);
  });
});

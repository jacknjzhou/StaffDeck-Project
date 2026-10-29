import {
  employeeDisplayName,
  employeeDisplayNameWithCreator,
} from '@/employee';
import { toTeamScope } from '@/lib/agent-scope-storage';
import type { AgentProfileRead, ChatSession } from '@/types';

/** 会话筛选下拉框的单个选项：筛选值 + 带计数的展示文案。 */
export type SessionFilterOption = {
  value: string;
  label: string;
};

/**
 * 根据员工列表与会话列表构建会话筛选下拉选项。
 *
 * 顺序固定为：全部会话、有会话记录的员工（按显示名排序）、团队（按名称排序）。
 * 计数只统计未归属团队的会话；正在草稿中的员工即使会话数为 0 也会保留。
 *
 * @param agents 可参与筛选的员工档案列表。
 * @param sessions 当前会话列表，用于统计各员工 / 团队的会话数。
 * @param activeDraftAgentId 正在新建草稿的员工 ID，用于保留零会话的草稿选项。
 * @returns 供筛选下拉框渲染的选项数组，首项为 `all`。
 */
export function buildSessionFilterOptions(
  agents: readonly AgentProfileRead[],
  sessions: readonly ChatSession[],
  activeDraftAgentId = '',
): SessionFilterOption[] {
  const counts = new Map<string, number>();
  const teamCounts = new Map<string, { name: string; count: number }>();
  sessions.forEach((session) => {
    if (session.agent_id && !session.team_id) {
      counts.set(session.agent_id, (counts.get(session.agent_id) || 0) + 1);
    }
    if (session.team_id) {
      const entry = teamCounts.get(session.team_id);
      const name = session.team_name || entry?.name || '团队';
      teamCounts.set(session.team_id, { name, count: (entry?.count || 0) + 1 });
    }
  });

  const rows = agents
    .filter((agent) => (counts.get(agent.id) || 0) > 0 || agent.id === activeDraftAgentId)
    .sort((left, right) => (
      employeeDisplayName(left).localeCompare(employeeDisplayName(right), 'zh-Hans-CN')
    ));

  const teamRows = [...teamCounts.entries()]
    .sort((left, right) => left[1].name.localeCompare(right[1].name, 'zh-Hans-CN'))
    .map(([teamId, entry]) => ({
      value: toTeamScope(teamId),
      label: `${entry.name} · ${entry.count}`,
    }));

  return [
    { value: 'all', label: `全部会话 · ${sessions.length}` },
    ...rows.map((agent) => {
      const count = counts.get(agent.id) || 0;
      return {
        value: agent.id,
        // Keep an active zero-session draft selectable without exposing a noisy “· 0” item.
        label: count > 0
          ? `${employeeDisplayNameWithCreator(agent)} · ${count}`
          : employeeDisplayNameWithCreator(agent),
      };
    }),
    ...teamRows,
  ];
}

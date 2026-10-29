export const ENTERPRISE_AGENT_STORAGE_KEY = 'ultrarag_enterprise_agent_scope';
export const SELECTED_AGENT_STORAGE_KEY = ENTERPRISE_AGENT_STORAGE_KEY;
export const SESSION_FILTER_STORAGE_PREFIX = 'skill_agent_session_filter';

/** 为指定用户生成会话筛选条件的存储键；userId 为空时回退为 anonymous。 */
export function sessionFilterStorageKey(userId: string): string {
  return `${SESSION_FILTER_STORAGE_PREFIX}:${userId || 'anonymous'}`;
}

/** 将数字员工作用域写入本地存储；agentId 为空时不做任何写入，userId 参数保留未使用。 */
export function persistSharedAgentScope(agentId: string, userId?: string): void {
  void userId;
  if (!agentId) return;
  window.localStorage.setItem(ENTERPRISE_AGENT_STORAGE_KEY, agentId);
}

/** 移除本地存储中的共享数字员工作用域；userId 参数保留未使用。 */
export function clearSharedAgentScope(userId?: string): void {
  void userId;
  window.localStorage.removeItem(ENTERPRISE_AGENT_STORAGE_KEY);
}

// Team scopes share the same storage slot as employee agent ids, prefixed so
// readers can tell "current team" apart from "current employee".
export const TEAM_SCOPE_PREFIX = 'team:';

/** 将团队 ID 转为带 team: 前缀的作用域标识；teamId 为空时返回空串。 */
export function toTeamScope(teamId: string): string {
  return teamId ? `${TEAM_SCOPE_PREFIX}${teamId}` : '';
}

/** 判断作用域值是否为团队作用域：需以 team: 开头且前缀后带实际团队 ID。 */
export function isTeamScope(value: string | null | undefined): boolean {
  return typeof value === 'string'
    && value.startsWith(TEAM_SCOPE_PREFIX)
    && value.length > TEAM_SCOPE_PREFIX.length;
}

/** 从团队作用域中取出团队 ID；传入非团队作用域时返回空串。 */
export function teamIdFromScope(value: string | null | undefined): string {
  return isTeamScope(value) ? String(value).slice(TEAM_SCOPE_PREFIX.length) : '';
}

/** 读取共享作用域；团队作用域对员工向页面视为"未选员工"，返回空串。 */
export function readEmployeeScope(): string {
  const raw = window.localStorage.getItem(ENTERPRISE_AGENT_STORAGE_KEY) || '';
  return isTeamScope(raw) ? '' : raw;
}

/** 在 window 上派发作用域变更事件，通知各页面切换到新的数字员工。 */
export function emitAgentScopeChange(agentId: string): void {
  window.dispatchEvent(
    new CustomEvent('ultrarag-enterprise-agent-scope-change', {
      detail: { agentId },
    }),
  );
}

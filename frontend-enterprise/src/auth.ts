export type EnterpriseAuthUser = {
  id: string;
  tenant_id: string;
  username: string;
  display_name?: string;
  role: 'admin' | 'member';
  avatar_url?: string;
};

export type EnterpriseAuthSession = {
  token: string;
  user: EnterpriseAuthUser;
};

export const ENTERPRISE_AUTH_STORAGE_KEY = 'ultrarag_auth';

/** 读取本地存储中的企业登录会话。 */
export function getEnterpriseAuthSession(): EnterpriseAuthSession | null {
  return readStoredSession(ENTERPRISE_AUTH_STORAGE_KEY);
}

/**
 * 持久化企业登录会话到 localStorage。
 *
 * 副作用：写入 `ultrarag_auth` 键；存储超限时降级为不带头像的最小会话重试，
 * 仍失败则抛出"存储空间不足"错误，避免登录流程误报为账号密码错误。
 */
export function setEnterpriseAuthSession(session: EnterpriseAuthSession): void {
  try {
    window.localStorage.setItem(ENTERPRISE_AUTH_STORAGE_KEY, JSON.stringify(session));
  } catch {
    // 存储超限等异常(极端情况):降级为不带头像字段的最小会话再试一次
    try {
      const minimal: EnterpriseAuthSession = {
        ...session,
        user: { ...session.user, avatar_url: undefined },
      };
      window.localStorage.setItem(ENTERPRISE_AUTH_STORAGE_KEY, JSON.stringify(minimal));
    } catch {
      // 抛出真实原因,避免被登录流程误报为账号/密码错误
      throw new Error('浏览器存储空间不足，请清理站点数据后重试');
    }
  }
}

/** 清除本地存储中的企业登录会话，用于退出登录。 */
export function clearEnterpriseAuthSession(): void {
  window.localStorage.removeItem(ENTERPRISE_AUTH_STORAGE_KEY);
}

/**
 * 从 localStorage 指定键解析会话对象。
 *
 * @returns 会话无效（无 token、缺 user.id 或 JSON 解析失败）时返回 null。
 */
function readStoredSession(key: string): EnterpriseAuthSession | null {
  const raw = window.localStorage.getItem(key);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as EnterpriseAuthSession;
    if (!parsed.token || !parsed.user?.id) return null;
    return parsed;
  } catch {
    return null;
  }
}

/**
 * 判断用户是否为管理员。
 *
 * @returns 用户为空或角色非 admin 时返回 false。
 */
export function isEnterpriseAdmin(user?: EnterpriseAuthUser | null): boolean {
  return user?.role === 'admin';
}

/**
 * 判断数字员工是否已发布到员工画廊。
 *
 * @returns 仅当 metadata.published_to_gallery 严格为 true 时返回 true。
 */
export function isGalleryEmployee(agent?: { metadata?: Record<string, unknown> } | null): boolean {
  return agent?.metadata?.published_to_gallery === true;
}

/**
 * 判断数字员工是否归属于指定用户。
 *
 * @returns 用户未登录时返回 false；否则比较 metadata.owner_user_id 与用户 id。
 */
export function isEmployeeOwnedBy(
  agent: { metadata?: Record<string, unknown> },
  user?: EnterpriseAuthUser | null,
): boolean {
  if (!user) return false;
  const metadata = agent.metadata || {};
  const ownerUserId = metadata.owner_user_id;
  return ownerUserId === user.id;
}
